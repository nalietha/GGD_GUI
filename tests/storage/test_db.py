"""Connection lifecycle and schema migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from livepulse.core.errors import MigrationError, StorageError
from livepulse.storage.db import IN_MEMORY, Database


class TestLifecycle:
    async def test_context_manager_connects_and_migrates(self) -> None:
        async with Database(IN_MEMORY) as db:
            assert await db.schema_version() == 1

    async def test_using_before_connect_is_a_clear_error(self) -> None:
        with pytest.raises(StorageError, match="not connected"):
            _ = Database(IN_MEMORY).connection

    async def test_connect_is_idempotent(self) -> None:
        db = Database(IN_MEMORY)
        await db.connect()
        first = db.connection
        await db.connect()
        assert db.connection is first
        await db.close()

    async def test_close_is_idempotent(self) -> None:
        db = Database(IN_MEMORY)
        await db.connect()
        await db.close()
        await db.close()

    async def test_creates_the_parent_directory(self, tmp_path: Path) -> None:
        """First boot has no /var/lib/livepulse; create it rather than crash."""
        target = tmp_path / "nested" / "deeper" / "livepulse.db"
        async with Database(target):
            pass
        assert target.exists()

    async def test_foreign_keys_are_enforced(self, db: Database) -> None:
        """Off by default in SQLite, which would make the schema's REFERENCES decorative."""
        row = await db.fetch_one("PRAGMA foreign_keys")
        assert row is not None
        assert row[0] == 1


class TestMigration:
    async def test_is_idempotent(self, db: Database) -> None:
        assert await db.migrate() == 1
        assert await db.migrate() == 1

    async def test_applies_pending_migrations_in_order(self, tmp_path: Path) -> None:
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_first.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
        (migrations / "002_second.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

        async with Database(IN_MEMORY, migrations_dir=migrations) as db:
            assert await db.schema_version() == 2
            assert await db.fetch_one("SELECT name FROM sqlite_master WHERE name = 'b'")

    async def test_resumes_from_the_recorded_version(self, tmp_path: Path) -> None:
        """Only pending migrations run; already-applied ones are not replayed."""
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_first.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
        target = tmp_path / "livepulse.db"

        async with Database(target, migrations_dir=migrations) as db:
            assert await db.schema_version() == 1

        (migrations / "002_second.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")
        async with Database(target, migrations_dir=migrations) as db:
            # Re-running 001 would raise "table a already exists".
            assert await db.schema_version() == 2

    async def test_misnamed_migration_is_rejected(self, tmp_path: Path) -> None:
        """A file that does not encode its version could be applied out of order."""
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "add_thing.sql").write_text("SELECT 1;")

        db = Database(IN_MEMORY, migrations_dir=migrations)
        await db.connect()
        with pytest.raises(MigrationError, match="misnamed"):
            await db.migrate()
        await db.close()

    async def test_duplicate_version_is_rejected(self, tmp_path: Path) -> None:
        """Two files claiming version 2 means one would silently never run."""
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "002_one.sql").write_text("SELECT 1;")
        (migrations / "002_two.sql").write_text("SELECT 1;")

        db = Database(IN_MEMORY, migrations_dir=migrations)
        await db.connect()
        with pytest.raises(MigrationError, match="duplicate migration version 2"):
            await db.migrate()
        await db.close()

    async def test_missing_directory_is_rejected(self, tmp_path: Path) -> None:
        db = Database(IN_MEMORY, migrations_dir=tmp_path / "absent")
        await db.connect()
        with pytest.raises(MigrationError, match="not found"):
            await db.migrate()
        await db.close()

    async def test_broken_sql_reports_the_file(self, tmp_path: Path) -> None:
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_broken.sql").write_text("CREATE TABLE (;")

        db = Database(IN_MEMORY, migrations_dir=migrations)
        await db.connect()
        with pytest.raises(MigrationError, match=r"001_broken\.sql"):
            await db.migrate()
        await db.close()

    async def test_version_is_not_bumped_when_a_migration_fails(self, tmp_path: Path) -> None:
        """A failed upgrade must leave the schema at the last good version."""
        migrations = tmp_path / "migrations"
        migrations.mkdir()
        (migrations / "001_broken.sql").write_text("CREATE TABLE (;")

        db = Database(IN_MEMORY, migrations_dir=migrations)
        await db.connect()
        with pytest.raises(MigrationError):
            await db.migrate()
        assert await db.schema_version() == 0
        await db.close()


class TestTransaction:
    async def test_rolls_back_on_error(self, db: Database) -> None:
        """A half-applied import is worse than none."""
        with pytest.raises(RuntimeError):
            async with db.transaction() as connection:
                await connection.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
                raise RuntimeError("boom")

        assert await db.fetch_one("SELECT 1 FROM settings WHERE key = 'a'") is None

    async def test_commits_on_success(self, db: Database) -> None:
        async with db.transaction() as connection:
            await connection.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")

        assert await db.fetch_one("SELECT 1 FROM settings WHERE key = 'a'") is not None
