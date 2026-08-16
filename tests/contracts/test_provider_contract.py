"""Shared contract every provider must satisfy.

The scheduler is written against :class:`Provider` and must not care which
platform it got. Every implementation is held to the same behaviour here, so
adding a platform means adding a fixture entry — not auditing the scheduler.

Real-network providers are skipped unless credentials are configured, so the
suite runs offline and gets fuller coverage when they are present.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from livepulse.core.errors import ChannelNotFoundError
from livepulse.core.models import ChannelRef, LiveState
from livepulse.providers import create_provider
from livepulse.providers.base import Capabilities, Provider

#: Every provider that can be exercised with no network and no credentials.
#: A new offline-capable provider is added here and inherits the whole suite.
OFFLINE_PROVIDERS = ["manual"]


@pytest.fixture(params=OFFLINE_PROVIDERS)
async def provider(request: pytest.FixtureRequest):
    instance = create_provider(request.param)
    try:
        yield instance
    finally:
        await instance.aclose()


class TestShape:
    def test_satisfies_the_protocol(self, provider: Provider) -> None:
        assert isinstance(provider, Provider)

    def test_declares_an_id_matching_its_registration(self, provider: Provider) -> None:
        """The id is stored in channels.provider; a mismatch orphans rows."""
        assert provider.id in OFFLINE_PROVIDERS

    def test_declares_capabilities(self, provider: Provider) -> None:
        assert isinstance(provider.caps, Capabilities)
        assert provider.caps.max_batch >= 1
        assert provider.caps.min_poll_interval > 0


class TestResolve:
    async def test_returns_a_ref_for_its_own_platform(self, provider: Provider) -> None:
        ref = await provider.resolve("someone")
        assert ref.provider == provider.id
        assert ref.platform_ref
        assert ref.handle

    async def test_rejects_an_empty_handle(self, provider: Provider) -> None:
        """An unnameable channel is worse than a rejected one."""
        with pytest.raises(ChannelNotFoundError):
            await provider.resolve("   ")

    async def test_strips_a_leading_at(self, provider: Provider) -> None:
        assert (await provider.resolve("@someone")).handle == "someone"


class TestPollContract:
    """The guarantee the whole state model rests on."""

    async def test_returns_an_entry_for_every_ref(self, provider: Provider) -> None:
        """Never a partial map.

        A missing key would be indistinguishable from "never checked", which is
        the conflation that left V1's unpolled nodes holding a stale colour.
        """
        refs = [await provider.resolve(f"channel{n}") for n in range(3)]
        result = await provider.poll(refs)

        assert set(result) == {ref.platform_ref for ref in refs}

    async def test_unknown_channels_are_explicitly_offline(self, provider: Provider) -> None:
        refs = [await provider.resolve("never-seen")]
        result = await provider.poll(refs)

        state = result["never-seen"]
        assert isinstance(state, LiveState)
        assert state.is_live is False
        assert state.observed_at is not None

    async def test_an_empty_request_returns_an_empty_map(self, provider: Provider) -> None:
        """The scheduler may hand over an empty batch after filtering."""
        assert await provider.poll([]) == {}

    async def test_records_where_the_observation_came_from(self, provider: Provider) -> None:
        refs = [await provider.resolve("someone")]
        assert (await provider.poll(refs))["someone"].source is not None


class TestLifecycle:
    async def test_aclose_is_idempotent(self, provider: Provider) -> None:
        """systemd may stop a unit that already failed mid-shutdown."""
        await provider.aclose()
        await provider.aclose()


class TestManualSpecifics:
    """Behaviour only the manual provider offers."""

    async def test_reports_what_it_was_told(self) -> None:
        from livepulse.providers.manual import ManualProvider

        provider = ManualProvider()
        ref = await provider.resolve("someone")
        provider.set_live("someone", True)

        assert (await provider.poll([ref]))["someone"].is_live is True

    async def test_can_be_switched_back_off(self) -> None:
        from livepulse.providers.manual import ManualProvider

        provider = ManualProvider()
        ref = await provider.resolve("someone")
        provider.set_live("someone", True)
        provider.set_live("someone", False)

        assert (await provider.poll([ref]))["someone"].is_live is False

    async def test_clearing_returns_to_never_observed(self) -> None:
        from livepulse.providers.manual import ManualProvider

        provider = ManualProvider()
        ref = await provider.resolve("someone")
        provider.set_live("someone", True)

        assert provider.clear("someone") is True
        assert (await provider.poll([ref]))["someone"].is_live is False

    async def test_carries_extra_detail(self) -> None:
        from livepulse.providers.manual import ManualProvider

        provider = ManualProvider()
        ref = await provider.resolve("someone")
        provider.set_live("someone", True, title="a stream", viewers=42)

        state = (await provider.poll([ref]))["someone"]
        assert (state.title, state.viewers) == ("a stream", 42)


ProviderBuilder = Callable[[], Awaitable[Provider]]


class TestRefsAreHashable:
    async def test_refs_dedupe(self, provider: Provider) -> None:
        """The scheduler dedupes before batching; refs must be usable in a set."""
        first = await provider.resolve("someone")
        second = await provider.resolve("someone")
        assert len({first, second}) == 1
        assert isinstance(first, ChannelRef)
