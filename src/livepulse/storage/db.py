"""Database connection and schema migration.

Owns the connection and the schema version, and nothing else — row-to-model
mapping is the repositories' job.

Why SQLite rather than the JSON file V1 used: atomic writes, a real schema
version, concurrent readers that cannot observe a half-written state, and
constraints the code cannot forget to enforce. WAL mode lets the web layer read
while ingestion writes, which is exactly the interleaving that made V1's
``File.WriteAllText`` unsafe.

Migrations are plain ``.sql`` files applied in filename order and tracked with
SQLite's ``user_version`` pragma. No ORM, no migration framework: the schema is
small, and a file you can read is worth more than a DSL you have to learn.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import aiosqlite
import structlog

from livepulse.core.errors import MigrationError, StorageError

__all__ = ["MIGRATIONS_DIR", "Database"]

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: ``001_initial.sql`` → version 1. The prefix is the version, so ordering and
#: numbering cannot disagree.
_MIGRATION_NAME = re.compile(r"^(\d+)_[a-z0-9_]+\.sql$")

IN_MEMORY = ":memory:"

#: Wait rather than fail when another connection holds a write lock. Contention
#: is brief here (one writer, small rows), so a retry window is enough.
BUSY_TIMEOUT_MS = 5_000


class Database:
    """An open SQLite connection with the schema at the expected version.

    Use as an async context manager; it connects, migrates, and closes.
    """

    __slots__ = ("_connection", "_migrations_dir", "_path")

    def __init__(self, path: Path | str, *, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self._path = str(path)
        self._migrations_dir = migrations_dir
        self._connection: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        return self._path

    @property
    def is_memory(self) -> bool:
        return self._path == IN_MEMORY

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise StorageError("database is not connected; call connect() first")
        return self._connection

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    async def connect(self) -> None:
        """Open the connection and apply the pragmas that make it safe."""
        if self._connection is not None:
            return

        if not self.is_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        try:
            connection = await aiosqlite.connect(self._path)
        except aiosqlite.Error as exc:
            raise StorageError(f"cannot open database {self._path}: {exc}") from exc

        connection.row_factory = aiosqlite.Row

        # WAL lets readers proceed during a write. It is a no-op for :memory:,
        # so it is skipped rather than failing.
        if not self.is_memory:
            await connection.execute("PRAGMA journal_mode = WAL")
        # Off by default in SQLite, which would make every REFERENCES clause
        # in the schema decorative.
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA synchronous = NORMAL")
        await connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        await connection.commit()

        self._connection = connection

    async def close(self) -> None:
        """Close the connection. Idempotent."""
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    async def __aenter__(self) -> Self:
        await self.connect()
        await self.migrate()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    # ----------------------------------------------------------------- #
    # Migration
    # ----------------------------------------------------------------- #

    async def schema_version(self) -> int:
        row = await self.fetch_one("PRAGMA user_version")
        return int(row[0]) if row else 0

    async def migrate(self) -> int:
        """Apply every pending migration in order.

        Each runs in its own transaction together with its version bump, so an
        interrupted upgrade leaves the schema at the last fully applied version
        rather than half-way through one.

        Returns:
            The schema version after migrating.

        Raises:
            MigrationError: a migration is malformed or failed to apply.
        """
        current = await self.schema_version()
        pending = [(v, p) for v, p in self._discover() if v > current]

        for version, path in pending:
            statements = path.read_text(encoding="utf-8")
            try:
                await self.connection.executescript(statements)
                # executescript commits implicitly, so the version bump is a
                # separate statement; it is the last thing written, which keeps
                # "version N" meaning "N applied in full".
                await self.connection.execute(f"PRAGMA user_version = {version}")
                await self.connection.commit()
            except aiosqlite.Error as exc:
                await self.connection.rollback()
                raise MigrationError(f"migration {path.name} failed: {exc}") from exc
            log.info("migration applied", version=version, name=path.name)

        final = await self.schema_version()
        if pending:
            log.info("schema up to date", version=final, applied=len(pending))
        return final

    def _discover(self) -> list[tuple[int, Path]]:
        """Find migrations, rejecting anything ambiguous.

        A duplicate or unparseable version number is a packaging mistake that
        must fail at startup, not silently skip a schema change.
        """
        if not self._migrations_dir.is_dir():
            raise MigrationError(f"migrations directory not found: {self._migrations_dir}")

        found: dict[int, Path] = {}
        for path in sorted(self._migrations_dir.glob("*.sql")):
            match = _MIGRATION_NAME.match(path.name)
            if match is None:
                raise MigrationError(
                    f"migration {path.name!r} is misnamed; expected NNN_lower_snake.sql"
                )
            version = int(match.group(1))
            if version in found:
                raise MigrationError(
                    f"duplicate migration version {version}: {found[version].name} and {path.name}"
                )
            found[version] = path

        return sorted(found.items())

    # ----------------------------------------------------------------- #
    # Query helpers
    # ----------------------------------------------------------------- #

    async def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        """Run a write statement, commit, and report rows affected."""
        cursor = await self.connection.execute(sql, parameters)
        await self.connection.commit()
        return cursor.rowcount

    async def execute_many(self, sql: str, parameters: Iterable[Sequence[Any]]) -> None:
        await self.connection.executemany(sql, parameters)
        await self.connection.commit()

    async def fetch_one(self, sql: str, parameters: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.connection.execute(sql, parameters) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.connection.execute(sql, parameters) as cursor:
            return list(await cursor.fetchall())

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Group writes so they land together or not at all.

        The importer needs this: a partial import that creates channels but not
        their node bindings is worse than no import.
        """
        connection = self.connection
        try:
            yield connection
        except Exception:
            await connection.rollback()
            raise
        else:
            await connection.commit()
