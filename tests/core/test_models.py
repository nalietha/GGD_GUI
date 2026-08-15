"""Domain model invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from livepulse.core.models import Channel, ChannelRef, LiveState, Node, StateSource


class TestChannel:
    def test_generates_a_stable_local_id(self) -> None:
        """Identity is ours, not the platform's — V1's privateId, kept."""
        first, second = (
            Channel(provider="twitch", platform_ref="1", handle="a"),
            Channel(provider="twitch", platform_ref="2", handle="b"),
        )
        assert first.id != second.id

    @pytest.mark.parametrize("given", ["Twitch", "TWITCH", "  twitch  "])
    def test_provider_is_normalised(self, given: str) -> None:
        """Registry lookups are exact-match, so casing must not reach them."""
        assert Channel(provider=given, platform_ref="1", handle="a").provider == "twitch"

    def test_label_prefers_display_name(self) -> None:
        channel = Channel(
            provider="twitch", platform_ref="1", handle="enyaignis", display_name="Enya Ignis"
        )
        assert channel.label == "Enya Ignis"

    def test_label_falls_back_to_handle(self) -> None:
        assert Channel(provider="twitch", platform_ref="1", handle="bao").label == "bao"

    def test_ref_round_trips(self) -> None:
        channel = Channel(provider="twitch", platform_ref="110059426", handle="bao")
        assert channel.ref == ChannelRef(provider="twitch", platform_ref="110059426", handle="bao")

    @pytest.mark.parametrize("colour", ["#fff", "#FFFFFF", "#9146FF", "#000000"])
    def test_accepts_valid_hex(self, colour: str) -> None:
        assert Channel(provider="p", platform_ref="1", handle="h", color=colour).color == colour

    @pytest.mark.parametrize("colour", ["fff", "#ff", "#gggggg", "", "red", "#1234567"])
    def test_rejects_invalid_hex(self, colour: str) -> None:
        """V1 stored ``""`` in ColorHex and crashed hex_to_color on it."""
        with pytest.raises(ValidationError):
            Channel(provider="p", platform_ref="1", handle="h", color=colour)


class TestChannelRef:
    def test_is_hashable_for_batch_dedup(self) -> None:
        ref = ChannelRef(provider="twitch", platform_ref="1", handle="a")
        duplicate = ChannelRef(provider="twitch", platform_ref="1", handle="a")
        assert len({ref, duplicate}) == 1

    def test_is_immutable(self) -> None:
        ref = ChannelRef(provider="twitch", platform_ref="1", handle="a")
        with pytest.raises(ValidationError):
            ref.handle = "b"  # type: ignore[misc]


class TestLiveState:
    def test_offline_helper_is_an_observation_not_an_absence(self) -> None:
        """'Observed offline' and 'never observed' must stay distinguishable.

        V1 conflated them, so an unpolled node held its last colour forever.
        """
        state = LiveState.offline(source=StateSource.MANUAL)
        assert state.is_live is False
        assert state.source is StateSource.MANUAL
        assert state.observed_at is not None

    def test_carries_richer_context_than_a_boolean(self) -> None:
        state = LiveState(is_live=True, title="stream", category="Just Chatting", viewers=1200)
        assert state.viewers == 1200
        assert state.category == "Just Chatting"

    def test_rejects_negative_viewers(self) -> None:
        with pytest.raises(ValidationError):
            LiveState(is_live=True, viewers=-1)


class TestNode:
    def test_defaults_to_unbound_static(self) -> None:
        node = Node(id=0)
        assert node.channel_id is None
        assert node.effect == "static"

    def test_rejects_negative_id(self) -> None:
        with pytest.raises(ValidationError):
            Node(id=-1)
