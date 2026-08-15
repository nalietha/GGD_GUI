"""Repositories: the only place that maps rows to domain models.

Each repository owns one table. Callers work in :mod:`livepulse.core.models`
types and never see a ``Row``, a UUID string, or an ISO timestamp — those
conversions live here, once.

There is deliberately no repository *protocol* and no in-memory fake. SQLite
runs in-process and opens a fresh schema in microseconds, so tests use a real
``:memory:`` database. A hand-written fake would be less faithful and would
need updating in lockstep with the real one — two implementations of the same
knowledge, which is the duplication worth avoiding.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import aiosqlite

from livepulse.core.errors import StorageError
from livepulse.core.models import Channel, LiveState, Node, StateSource
from livepulse.storage.db import Database

__all__ = [
    "ChannelRepository",
    "LiveStateRepository",
    "NodeRepository",
    "Repositories",
    "SettingsRepository",
]


# --------------------------------------------------------------------------- #
# Conversions — defined once, used by every repository below
# --------------------------------------------------------------------------- #


def _to_iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    # Rows written before a timezone was attached would otherwise compare
    # against aware datetimes and raise.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_channel(row: aiosqlite.Row) -> Channel:
    return Channel(
        id=UUID(row["id"]),
        provider=row["provider"],
        platform_ref=row["platform_ref"],
        handle=row["handle"],
        display_name=row["display_name"],
        url=row["url"],
        color=row["color"],
        alt_color=row["alt_color"],
        enabled=bool(row["enabled"]),
    )


def _row_to_node(row: aiosqlite.Row) -> Node:
    return Node(
        id=row["id"],
        channel_id=UUID(row["channel_id"]) if row["channel_id"] else None,
        effect=row["effect"],
        color=row["color"],
        params=json.loads(row["params"]),
    )


def _row_to_live_state(row: aiosqlite.Row) -> LiveState:
    return LiveState(
        is_live=bool(row["is_live"]),
        title=row["title"],
        category=row["category"],
        viewers=row["viewers"],
        started_at=_from_iso(row["started_at"]),
        thumbnail_url=row["thumbnail_url"],
        observed_at=_from_iso(row["observed_at"]) or datetime.now(UTC),
        source=StateSource(row["source"]),
    )


# --------------------------------------------------------------------------- #
# Repositories
# --------------------------------------------------------------------------- #


class ChannelRepository:
    """Channels: the streams a user has configured, on any platform."""

    __slots__ = ("_db",)

    _COLUMNS = "id, provider, platform_ref, handle, display_name, url, color, alt_color, enabled"

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, channel: Channel) -> Channel:
        """Insert a channel.

        Raises:
            StorageError: this ``(provider, platform_ref)`` already exists.
                Adding the same streamer twice is a user mistake worth naming,
                not a silent no-op.
        """
        try:
            await self._db.execute(
                f"INSERT INTO channels ({self._COLUMNS}, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(channel.id),
                    channel.provider,
                    channel.platform_ref,
                    channel.handle,
                    channel.display_name,
                    channel.url,
                    channel.color,
                    channel.alt_color,
                    int(channel.enabled),
                    datetime.now(UTC).isoformat(),
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise StorageError(
                f"channel {channel.handle!r} already exists on {channel.provider}"
            ) from exc
        return channel

    async def get(self, channel_id: UUID) -> Channel | None:
        row = await self._db.fetch_one(
            f"SELECT {self._COLUMNS} FROM channels WHERE id = ?", (str(channel_id),)
        )
        return _row_to_channel(row) if row else None

    async def find_by_ref(self, provider: str, platform_ref: str) -> Channel | None:
        """Look up by platform identity, for deduplicating an add."""
        row = await self._db.fetch_one(
            f"SELECT {self._COLUMNS} FROM channels WHERE provider = ? AND platform_ref = ?",
            (provider.lower(), platform_ref),
        )
        return _row_to_channel(row) if row else None

    async def list(self, *, enabled_only: bool = False) -> list[Channel]:
        clause = " WHERE enabled = 1" if enabled_only else ""
        rows = await self._db.fetch_all(
            f"SELECT {self._COLUMNS} FROM channels{clause} ORDER BY handle COLLATE NOCASE"
        )
        return [_row_to_channel(row) for row in rows]

    async def update(self, channel: Channel) -> bool:
        """Overwrite a channel. Returns False if it no longer exists."""
        affected = await self._db.execute(
            "UPDATE channels SET provider = ?, platform_ref = ?, handle = ?, "
            "display_name = ?, url = ?, color = ?, alt_color = ?, enabled = ? WHERE id = ?",
            (
                channel.provider,
                channel.platform_ref,
                channel.handle,
                channel.display_name,
                channel.url,
                channel.color,
                channel.alt_color,
                int(channel.enabled),
                str(channel.id),
            ),
        )
        return affected > 0

    async def delete(self, channel_id: UUID) -> bool:
        """Delete a channel.

        Bound nodes are unbound by the schema's ``ON DELETE SET NULL`` and keep
        their colour and effect — V1 also reset the colour to black, destroying
        a setting the user never asked to change.
        """
        return await self._db.execute("DELETE FROM channels WHERE id = ?", (str(channel_id),)) > 0


class NodeRepository:
    """Nodes: the physical light positions and what each is bound to."""

    __slots__ = ("_db",)

    _COLUMNS = "id, channel_id, effect, color, params"

    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure(self, count: int) -> int:
        """Create any missing nodes for ``0..count-1``.

        Idempotent, and never touches a node that already exists — growing the
        stand from 16 to 24 slots must not reset the first 16. V1 regenerated
        the whole save file from defaults whenever it was absent, and its build
        copied the repo's copy over the deployed one on every publish.

        Returns:
            How many nodes were created.
        """
        if count < 0:
            raise StorageError(f"node count cannot be negative, got {count}")

        existing = {row["id"] for row in await self._db.fetch_all("SELECT id FROM nodes")}
        missing = [node_id for node_id in range(count) if node_id not in existing]
        if missing:
            await self._db.execute_many(
                "INSERT INTO nodes (id) VALUES (?)", [(node_id,) for node_id in missing]
            )
        return len(missing)

    async def get(self, node_id: int) -> Node | None:
        row = await self._db.fetch_one(
            f"SELECT {self._COLUMNS} FROM nodes WHERE id = ?", (node_id,)
        )
        return _row_to_node(row) if row else None

    async def list(self) -> list[Node]:
        rows = await self._db.fetch_all(f"SELECT {self._COLUMNS} FROM nodes ORDER BY id")
        return [_row_to_node(row) for row in rows]

    async def update(self, node: Node) -> bool:
        affected = await self._db.execute(
            "UPDATE nodes SET channel_id = ?, effect = ?, color = ?, params = ? WHERE id = ?",
            (
                str(node.channel_id) if node.channel_id else None,
                node.effect,
                node.color,
                json.dumps(node.params),
                node.id,
            ),
        )
        return affected > 0

    async def bind(self, node_id: int, channel_id: UUID | None) -> bool:
        """Point a node at a channel, or unbind it with ``None``."""
        try:
            affected = await self._db.execute(
                "UPDATE nodes SET channel_id = ? WHERE id = ?",
                (str(channel_id) if channel_id else None, node_id),
            )
        except aiosqlite.IntegrityError as exc:
            raise StorageError(f"no such channel: {channel_id}") from exc
        return affected > 0

    async def bound_channel_ids(self) -> set[UUID]:
        """Distinct channels any node points at.

        The ingest scheduler polls these; an unbound channel costs nothing.
        """
        rows = await self._db.fetch_all(
            "SELECT DISTINCT channel_id FROM nodes WHERE channel_id IS NOT NULL"
        )
        return {UUID(row["channel_id"]) for row in rows}


class LiveStateRepository:
    """The most recent observation per channel."""

    __slots__ = ("_db",)

    _COLUMNS = (
        "channel_id, is_live, title, category, viewers, started_at, "
        "thumbnail_url, observed_at, source"
    )

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, channel_id: UUID, state: LiveState) -> None:
        """Store an observation, replacing any earlier one."""
        try:
            await self._db.execute(
                f"INSERT INTO live_state ({self._COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (channel_id) DO UPDATE SET "
                "is_live = excluded.is_live, title = excluded.title, "
                "category = excluded.category, viewers = excluded.viewers, "
                "started_at = excluded.started_at, thumbnail_url = excluded.thumbnail_url, "
                "observed_at = excluded.observed_at, source = excluded.source",
                (
                    str(channel_id),
                    int(state.is_live),
                    state.title,
                    state.category,
                    state.viewers,
                    _to_iso(state.started_at),
                    state.thumbnail_url,
                    _to_iso(state.observed_at),
                    state.source.value,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise StorageError(f"no such channel: {channel_id}") from exc

    async def get(self, channel_id: UUID) -> LiveState | None:
        """The latest observation, or ``None`` if never observed.

        ``None`` is meaningfully different from an offline state, and callers
        are expected to treat it that way.
        """
        row = await self._db.fetch_one(
            f"SELECT {self._COLUMNS} FROM live_state WHERE channel_id = ?", (str(channel_id),)
        )
        return _row_to_live_state(row) if row else None

    async def all(self) -> dict[UUID, LiveState]:
        """Every observation, keyed by channel. One query per render snapshot."""
        rows = await self._db.fetch_all(f"SELECT {self._COLUMNS} FROM live_state")
        return {UUID(row["channel_id"]): _row_to_live_state(row) for row in rows}

    async def clear(self, channel_id: UUID) -> bool:
        """Forget an observation, returning the channel to 'never checked'."""
        return (
            await self._db.execute(
                "DELETE FROM live_state WHERE channel_id = ?", (str(channel_id),)
            )
            > 0
        )


class SettingsRepository:
    """Runtime settings, as JSON values keyed by name.

    Only for settings the user changes while running — brightness, display
    mode. Static configuration is in ``config.toml`` and credentials are outside
    the database entirely.
    """

    __slots__ = ("_db",)

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self._db.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError as exc:
            raise StorageError(f"setting {key!r} holds invalid JSON") from exc

    async def set(self, key: str, value: Any) -> None:
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    async def all(self) -> dict[str, Any]:
        rows = await self._db.fetch_all("SELECT key, value FROM settings")
        return {row["key"]: json.loads(row["value"]) for row in rows}

    async def delete(self, key: str) -> bool:
        """Remove a setting, so its default applies again."""
        return await self._db.execute("DELETE FROM settings WHERE key = ?", (key,)) > 0


class Repositories:
    """Every repository over one database.

    A single object to hand to the composition root, so adding a repository
    does not change the signature of everything that needs storage.
    """

    __slots__ = ("channels", "live_state", "nodes", "settings")

    def __init__(self, db: Database) -> None:
        self.channels = ChannelRepository(db)
        self.nodes = NodeRepository(db)
        self.live_state = LiveStateRepository(db)
        self.settings = SettingsRepository(db)
