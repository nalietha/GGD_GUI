-- Initial schema.
--
-- Replaces V1's save.json, which was rewritten wholesale with File.WriteAllText
-- by the web app while a Python process read it on a 10-second timer: no locking,
-- no atomicity, no schema version, and torn reads waiting to happen.
--
-- Timestamps are ISO-8601 UTC strings. SQLite has no date type, and text sorts
-- correctly for this format, which keeps range queries simple.

CREATE TABLE channels (
    id           TEXT    PRIMARY KEY,          -- our UUID; V1's privateId, preserved on import
    provider     TEXT    NOT NULL,             -- registry key: 'twitch', 'youtube', ...
    platform_ref TEXT    NOT NULL,             -- the platform's own id; V1's publicStreamerId
    handle       TEXT    NOT NULL,             -- login/slug, used for URLs
    display_name TEXT,
    url          TEXT,
    color        TEXT    NOT NULL DEFAULT '#9146FF',
    alt_color    TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL,

    -- The same channel cannot be added twice, but the same handle on two
    -- different platforms is a legitimate and expected case.
    UNIQUE (provider, platform_ref)
);

CREATE INDEX idx_channels_provider ON channels (provider) WHERE enabled = 1;

CREATE TABLE nodes (
    id         INTEGER PRIMARY KEY,            -- logical node number the user sees
    channel_id TEXT    REFERENCES channels (id) ON DELETE SET NULL,
    effect     TEXT    NOT NULL DEFAULT 'static',
    color      TEXT    NOT NULL DEFAULT '#9146FF',
    params     TEXT    NOT NULL DEFAULT '{}'   -- JSON, effect-specific
);

-- ON DELETE SET NULL, not CASCADE: deleting a channel must unbind the node,
-- never delete it. V1 went further and reset the node's colour to #000000,
-- silently destroying a user's choice on an unrelated action.

CREATE INDEX idx_nodes_channel ON nodes (channel_id);

CREATE TABLE live_state (
    channel_id    TEXT    PRIMARY KEY REFERENCES channels (id) ON DELETE CASCADE,
    is_live       INTEGER NOT NULL,
    title         TEXT,
    category      TEXT,
    viewers       INTEGER,
    started_at    TEXT,
    thumbnail_url TEXT,
    observed_at   TEXT    NOT NULL,
    source        TEXT    NOT NULL             -- poll | push | manual
);

-- A row here means "observed". Its absence means "never checked". V1 conflated
-- the two, so a node whose streamer had never been polled held its last colour
-- indefinitely.

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                        -- JSON-encoded scalar or object
);

-- Runtime settings only: brightness, display mode, ambient effect. Credentials
-- live outside the database, and static config lives in config.toml. In V1 a web
-- request rewrote the same appsettings.json that held the Twitch client secret,
-- and did so with a naming policy that made every setting unreadable afterwards.

CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT    NOT NULL,
    channel_id TEXT    REFERENCES channels (id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    payload    TEXT
);

CREATE INDEX idx_events_at ON events (at DESC);
