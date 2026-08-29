"""Repository behaviour, including the schema guarantees V1 lacked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from livepulse.core.errors import StorageError
from livepulse.core.models import Channel, LiveState, StateSource
from livepulse.storage.repositories import Repositories, StoredToken


class TestChannelRepository:
    async def test_round_trips(self, repos: Repositories, channel: Channel) -> None:
        await repos.channels.add(channel)
        assert await repos.channels.get(channel.id) == channel

    async def test_get_missing_returns_none(self, repos: Repositories) -> None:
        assert await repos.channels.get(uuid4()) is None

    async def test_rejects_a_duplicate_platform_reference(
        self, repos: Repositories, channel: Channel
    ) -> None:
        """Adding the same streamer twice is a user mistake worth naming."""
        await repos.channels.add(channel)
        duplicate = channel.model_copy(update={"id": uuid4()})

        with pytest.raises(StorageError, match="already exists"):
            await repos.channels.add(duplicate)

    async def test_the_same_handle_on_two_platforms_is_allowed(self, repos: Repositories) -> None:
        """The whole point of V2: one person, several platforms."""
        await repos.channels.add(Channel(provider="twitch", platform_ref="1", handle="samename"))
        await repos.channels.add(Channel(provider="youtube", platform_ref="1", handle="samename"))
        assert len(await repos.channels.list()) == 2

    async def test_find_by_ref_is_case_insensitive_on_provider(
        self, repos: Repositories, channel: Channel
    ) -> None:
        await repos.channels.add(channel)
        found = await repos.channels.find_by_ref("TWITCH", channel.platform_ref)
        assert found is not None
        assert found.id == channel.id

    async def test_list_sorts_case_insensitively(self, repos: Repositories) -> None:
        for handle in ("zeta", "Alpha", "middle"):
            await repos.channels.add(Channel(provider="twitch", platform_ref=handle, handle=handle))
        assert [c.handle for c in await repos.channels.list()] == ["Alpha", "middle", "zeta"]

    async def test_enabled_only_filters(self, repos: Repositories) -> None:
        await repos.channels.add(Channel(provider="twitch", platform_ref="1", handle="on"))
        await repos.channels.add(
            Channel(provider="twitch", platform_ref="2", handle="off", enabled=False)
        )
        assert [c.handle for c in await repos.channels.list(enabled_only=True)] == ["on"]

    async def test_update_persists(self, repos: Repositories, channel: Channel) -> None:
        await repos.channels.add(channel)
        await repos.channels.update(channel.model_copy(update={"color": "#123456"}))

        stored = await repos.channels.get(channel.id)
        assert stored is not None
        assert stored.color == "#123456"

    async def test_update_of_a_missing_channel_reports_false(
        self, repos: Repositories, channel: Channel
    ) -> None:
        assert await repos.channels.update(channel) is False

    async def test_delete_reports_whether_anything_went(
        self, repos: Repositories, channel: Channel
    ) -> None:
        await repos.channels.add(channel)
        assert await repos.channels.delete(channel.id) is True
        assert await repos.channels.delete(channel.id) is False


class TestNodeRepository:
    async def test_ensure_creates_missing_nodes(self, repos: Repositories) -> None:
        assert await repos.nodes.ensure(16) == 16
        assert len(await repos.nodes.list()) == 16

    async def test_ensure_is_idempotent(self, repos: Repositories) -> None:
        await repos.nodes.ensure(16)
        assert await repos.nodes.ensure(16) == 0

    async def test_ensure_preserves_existing_configuration(
        self, repos: Repositories, channel: Channel
    ) -> None:
        """Growing the stand must not reset the nodes already set up.

        V1 regenerated save.json from defaults whenever it was missing, and its
        build copied the repo's copy over the deployed one on every publish.
        """
        await repos.channels.add(channel)
        await repos.nodes.ensure(16)

        node = await repos.nodes.get(3)
        assert node is not None
        await repos.nodes.update(
            node.model_copy(update={"color": "#abcdef", "channel_id": channel.id})
        )

        created = await repos.nodes.ensure(24)

        assert created == 8
        preserved = await repos.nodes.get(3)
        assert preserved is not None
        assert preserved.color == "#abcdef"
        assert preserved.channel_id == channel.id

    async def test_rejects_a_negative_count(self, repos: Repositories) -> None:
        with pytest.raises(StorageError, match="negative"):
            await repos.nodes.ensure(-1)

    async def test_params_round_trip_as_json(self, repos: Repositories) -> None:
        await repos.nodes.ensure(1)
        node = await repos.nodes.get(0)
        assert node is not None
        await repos.nodes.update(node.model_copy(update={"params": {"speed": 0.5, "on": True}}))

        stored = await repos.nodes.get(0)
        assert stored is not None
        assert stored.params == {"speed": 0.5, "on": True}

    async def test_bind_and_unbind(self, repos: Repositories, channel: Channel) -> None:
        await repos.channels.add(channel)
        await repos.nodes.ensure(4)

        await repos.nodes.bind(0, channel.id)
        bound = await repos.nodes.get(0)
        assert bound is not None
        assert bound.channel_id == channel.id

        await repos.nodes.bind(0, None)
        unbound = await repos.nodes.get(0)
        assert unbound is not None
        assert unbound.channel_id is None

    async def test_binding_to_an_unknown_channel_is_refused(self, repos: Repositories) -> None:
        """The foreign key catches what V1 allowed: nodes pointing at nothing."""
        await repos.nodes.ensure(1)
        with pytest.raises(StorageError, match="no such channel"):
            await repos.nodes.bind(0, uuid4())

    async def test_several_nodes_can_share_a_channel(
        self, repos: Repositories, channel: Channel
    ) -> None:
        """The deployed save.json has two nodes on one streamer; keep that working."""
        await repos.channels.add(channel)
        await repos.nodes.ensure(4)
        await repos.nodes.bind(1, channel.id)
        await repos.nodes.bind(3, channel.id)

        assert await repos.nodes.bound_channel_ids() == {channel.id}

    async def test_deleting_a_channel_unbinds_without_losing_the_colour(
        self, repos: Repositories, channel: Channel
    ) -> None:
        """V1 also reset the node's colour to black, destroying a user's choice."""
        await repos.channels.add(channel)
        await repos.nodes.ensure(2)
        node = await repos.nodes.get(0)
        assert node is not None
        await repos.nodes.update(
            node.model_copy(update={"channel_id": channel.id, "color": "#abcdef"})
        )

        await repos.channels.delete(channel.id)

        after = await repos.nodes.get(0)
        assert after is not None
        assert after.channel_id is None, "the node must be unbound"
        assert after.color == "#abcdef", "but must keep its colour"


class TestLiveStateRepository:
    async def test_never_observed_is_distinct_from_offline(
        self, repos: Repositories, channel: Channel
    ) -> None:
        """The distinction V1 lost, which left unpolled nodes holding stale colour."""
        await repos.channels.add(channel)
        assert await repos.live_state.get(channel.id) is None

        await repos.live_state.record(channel.id, LiveState.offline())
        observed = await repos.live_state.get(channel.id)
        assert observed is not None
        assert observed.is_live is False

    async def test_records_full_context(self, repos: Repositories, channel: Channel) -> None:
        await repos.channels.add(channel)
        started = datetime(2026, 3, 1, 18, 30, tzinfo=UTC)
        await repos.live_state.record(
            channel.id,
            LiveState(
                is_live=True,
                title="stream title",
                category="Just Chatting",
                viewers=4321,
                started_at=started,
                source=StateSource.PUSH,
            ),
        )

        stored = await repos.live_state.get(channel.id)
        assert stored is not None
        assert (stored.title, stored.category, stored.viewers) == (
            "stream title",
            "Just Chatting",
            4321,
        )
        assert stored.started_at == started
        assert stored.source is StateSource.PUSH

    async def test_recording_twice_replaces(self, repos: Repositories, channel: Channel) -> None:
        await repos.channels.add(channel)
        await repos.live_state.record(channel.id, LiveState(is_live=True))
        await repos.live_state.record(channel.id, LiveState.offline())

        stored = await repos.live_state.get(channel.id)
        assert stored is not None
        assert stored.is_live is False

    async def test_recording_for_an_unknown_channel_is_refused(self, repos: Repositories) -> None:
        with pytest.raises(StorageError, match="no such channel"):
            await repos.live_state.record(uuid4(), LiveState(is_live=True))

    async def test_all_returns_one_map_for_the_render_snapshot(self, repos: Repositories) -> None:
        first = Channel(provider="twitch", platform_ref="1", handle="a")
        second = Channel(provider="twitch", platform_ref="2", handle="b")
        await repos.channels.add(first)
        await repos.channels.add(second)
        await repos.live_state.record(first.id, LiveState(is_live=True))
        await repos.live_state.record(second.id, LiveState.offline())

        states = await repos.live_state.all()
        assert states[first.id].is_live is True
        assert states[second.id].is_live is False

    async def test_deleting_a_channel_removes_its_state(
        self, repos: Repositories, channel: Channel
    ) -> None:
        await repos.channels.add(channel)
        await repos.live_state.record(channel.id, LiveState(is_live=True))
        await repos.channels.delete(channel.id)
        assert await repos.live_state.all() == {}

    async def test_clear_returns_to_never_observed(
        self, repos: Repositories, channel: Channel
    ) -> None:
        await repos.channels.add(channel)
        await repos.live_state.record(channel.id, LiveState(is_live=True))
        assert await repos.live_state.clear(channel.id) is True
        assert await repos.live_state.get(channel.id) is None


class TestSettingsRepository:
    @pytest.mark.parametrize("value", [42, "text", True, 1.5, None, {"a": 1}, [1, 2]])
    async def test_round_trips_json_values(self, repos: Repositories, value: object) -> None:
        await repos.settings.set("k", value)
        assert await repos.settings.get("k") == value

    async def test_missing_key_returns_the_default(self, repos: Repositories) -> None:
        assert await repos.settings.get("absent", "fallback") == "fallback"

    async def test_set_overwrites(self, repos: Repositories) -> None:
        await repos.settings.set("k", 1)
        await repos.settings.set("k", 2)
        assert await repos.settings.get("k") == 2

    async def test_delete_restores_the_default(self, repos: Repositories) -> None:
        await repos.settings.set("k", 1)
        assert await repos.settings.delete("k") is True
        assert await repos.settings.get("k", "default") == "default"


class TestTokenRepository:
    """OAuth tokens for providers that authenticate as a user.

    Twitch EventSub over WebSocket refuses an app access token, so a user token
    and its refresh token must survive restarts.
    """

    async def test_round_trips(self, repos: Repositories) -> None:
        expires = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        await repos.tokens.save(
            "twitch",
            StoredToken(
                access_token="a", refresh_token="r", expires_at=expires, scopes="user:read"
            ),
        )

        stored = await repos.tokens.get("twitch")
        assert stored is not None
        assert (stored.access_token, stored.refresh_token) == ("a", "r")
        assert stored.expires_at == expires
        assert stored.scopes == "user:read"

    async def test_missing_provider_returns_none(self, repos: Repositories) -> None:
        assert await repos.tokens.get("twitch") is None

    async def test_saving_twice_replaces(self, repos: Repositories) -> None:
        """Refreshing must overwrite, not accumulate."""
        await repos.tokens.save("twitch", StoredToken(access_token="old"))
        await repos.tokens.save("twitch", StoredToken(access_token="new"))

        stored = await repos.tokens.get("twitch")
        assert stored is not None
        assert stored.access_token == "new"

    async def test_provider_is_case_insensitive(self, repos: Repositories) -> None:
        await repos.tokens.save("Twitch", StoredToken(access_token="a"))
        assert await repos.tokens.get("TWITCH") is not None

    async def test_delete_forces_a_fresh_login(self, repos: Repositories) -> None:
        await repos.tokens.save("twitch", StoredToken(access_token="a"))
        assert await repos.tokens.delete("twitch") is True
        assert await repos.tokens.get("twitch") is None

    async def test_providers_lists_without_exposing_tokens(self, repos: Repositories) -> None:
        await repos.tokens.save("twitch", StoredToken(access_token="secret"))
        assert await repos.tokens.providers() == ["twitch"]

    async def test_obtained_at_defaults_to_now(self, repos: Repositories) -> None:
        await repos.tokens.save("twitch", StoredToken(access_token="a"))
        stored = await repos.tokens.get("twitch")
        assert stored is not None
        assert stored.obtained_at is not None


class TestStoredTokenFreshness:
    def test_a_future_expiry_is_fresh(self) -> None:
        token = StoredToken(access_token="a", expires_at=datetime(2030, 1, 1, tzinfo=UTC))
        assert token.is_fresh(datetime(2026, 1, 1, tzinfo=UTC)) is True

    def test_a_past_expiry_is_stale(self) -> None:
        token = StoredToken(access_token="a", expires_at=datetime(2026, 1, 1, tzinfo=UTC))
        assert token.is_fresh(datetime(2030, 1, 1, tzinfo=UTC)) is False

    def test_the_margin_prevents_racing_an_expiry(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        token = StoredToken(access_token="a", expires_at=now + timedelta(minutes=1))
        assert token.is_fresh(now) is False, "one minute left should count as stale"

    def test_an_unknown_expiry_is_assumed_usable(self) -> None:
        """Refusing to try would strand a token that is probably fine; a 401
        corrects us cheaply."""
        assert StoredToken(access_token="a").is_fresh(datetime(2026, 1, 1, tzinfo=UTC)) is True
