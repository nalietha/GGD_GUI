-- OAuth tokens for providers that need a user identity rather than an app one.
--
-- Twitch EventSub over WebSocket will not accept an app access token: creating
-- a subscription on a WebSocket transport requires a *user* access token. That
-- token is obtained once through the device-code flow and then kept alive by
-- its refresh token, so it has to outlive the process.
--
-- This table holds credentials. The database file is chmod 0600 on creation for
-- that reason -- see storage/db.py. Provider *secrets* (client id and secret)
-- still live outside the database entirely, in the environment.

CREATE TABLE oauth_tokens (
    provider      TEXT PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    expires_at    TEXT,               -- ISO-8601 UTC; NULL means unknown
    scopes        TEXT NOT NULL DEFAULT '',
    obtained_at   TEXT NOT NULL
);
