"""Provider registration, construction, and URL identification."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from livepulse.core.errors import ConfigError, ProviderAuthError
from livepulse.core.models import AuthKind, ChannelRef, LiveState
from livepulse.providers import registry
from livepulse.providers.base import (
    BaseProvider,
    Capabilities,
    Provider,
    ProviderConfig,
    batched,
    handle_from_url,
)


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    saved = dict(registry._PROVIDERS)
    try:
        yield
    finally:
        registry._PROVIDERS.clear()
        registry._PROVIDERS.update(saved)


class Dummy(BaseProvider):
    id = "dummy"
    caps = Capabilities(supports_batch=True, max_batch=50)

    async def resolve(self, handle: str) -> ChannelRef:
        return ChannelRef(provider=self.id, platform_ref=handle, handle=handle)

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        return self.fill_offline(refs, {})


class TestRegistration:
    def test_manual_is_registered_on_import(self) -> None:
        assert "manual" in registry.available_providers()

    def test_a_new_provider_needs_no_core_changes(self, isolated_registry: None) -> None:
        """Open/closed, and the whole point of the rewrite.

        Adding a platform touches the registry and nothing else — not the
        scheduler, not the renderer, not the schema.
        """
        registry.register_provider("dummy", Dummy, Dummy.caps, "a test platform")

        provider = registry.create_provider("dummy")
        assert isinstance(provider, Provider)
        assert "dummy" in registry.available_providers()

    def test_ids_are_normalised(self, isolated_registry: None) -> None:
        registry.register_provider("  DUMMY  ", Dummy, Dummy.caps)
        assert registry.create_provider("dummy").id == "dummy"

    def test_unknown_provider_names_the_alternatives(self) -> None:
        with pytest.raises(ConfigError, match="unknown provider 'nope'") as caught:
            registry.create_provider("nope")
        assert "manual" in str(caught.value)

    def test_capabilities_are_readable_without_constructing(self) -> None:
        """`livepulse provider list` must work with no credentials present."""
        assert registry.capabilities("manual").supports_batch is True

    def test_descriptions_are_supplied_for_the_ui(self) -> None:
        assert registry.describe_providers()["manual"]


class TestIdentify:
    @pytest.fixture(autouse=True)
    def _register_platforms(self, isolated_registry: None) -> None:
        registry.register_provider(
            "twitch",
            Dummy,
            Dummy.caps,
            url_patterns=[r"twitch\.tv/(?P<handle>[a-z0-9_]+)"],
        )
        registry.register_provider(
            "kick",
            Dummy,
            Dummy.caps,
            url_patterns=[r"kick\.com/(?P<handle>[a-z0-9_-]+)"],
        )
        registry.register_provider(
            "youtube",
            Dummy,
            Dummy.caps,
            url_patterns=[r"youtube\.com/@(?P<handle>[a-z0-9_.-]+)"],
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("https://twitch.tv/ironmouse", ("twitch", "ironmouse")),
            ("http://www.twitch.tv/ironmouse", ("twitch", "ironmouse")),
            ("twitch.tv/ironmouse", ("twitch", "ironmouse")),
            ("https://twitch.tv/ironmouse/", ("twitch", "ironmouse")),
            ("https://kick.com/some-one", ("kick", "some-one")),
            ("https://youtube.com/@somebody", ("youtube", "somebody")),
        ],
    )
    def test_recognises_channel_links(self, text: str, expected: tuple[str, str]) -> None:
        """V1 had this logic and never called it — every channel it added was
        hardcoded to Twitch, which is why its 'platform' field was always the same."""
        assert registry.identify(text) == expected

    def test_is_case_insensitive(self) -> None:
        assert registry.identify("HTTPS://TWITCH.TV/IronMouse") == ("twitch", "IronMouse")

    @pytest.mark.parametrize("text", ["ironmouse", "", "   ", "not a url"])
    def test_returns_none_when_it_cannot_tell(self, text: str) -> None:
        """A bare username could be anyone's, so guessing is not allowed."""
        assert registry.identify(text) is None


class TestResolveInput:
    @pytest.fixture(autouse=True)
    def _register_twitch(self, isolated_registry: None) -> None:
        registry.register_provider(
            "twitch", Dummy, Dummy.caps, url_patterns=[r"twitch\.tv/(?P<handle>[a-z0-9_]+)"]
        )

    def test_a_link_wins_over_the_default(self) -> None:
        registry.register_provider("manual", Dummy, Dummy.caps)
        assert registry.resolve_input("twitch.tv/someone", "manual") == ("twitch", "someone")

    def test_a_bare_handle_uses_the_default(self) -> None:
        assert registry.resolve_input("someone", "twitch") == ("twitch", "someone")

    def test_a_bare_handle_with_no_default_is_refused(self) -> None:
        """Guessing would file a YouTube channel under Twitch — V1's behaviour."""
        with pytest.raises(ConfigError, match="cannot tell which platform"):
            registry.resolve_input("someone")

    def test_an_unknown_default_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="unknown provider"):
            registry.resolve_input("someone", "nope")


class TestHandleFromUrl:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("someone", "someone"),
            ("@someone", "someone"),
            ("https://twitch.tv/someone", "someone"),
            ("https://twitch.tv/someone/", "someone"),
            ("https://youtube.com/@someone", "someone"),
            ("https://twitch.tv/someone?x=1", "someone"),
            ("https://twitch.tv/someone#about", "someone"),
        ],
    )
    def test_reduces_to_the_handle(self, text: str, expected: str) -> None:
        assert handle_from_url(text) == expected


class TestCapabilities:
    def test_batch_size_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            Capabilities(max_batch=0)

    def test_batching_with_a_size_of_one_is_rejected(self) -> None:
        """Contradictory capabilities would make the scheduler plan nonsense."""
        with pytest.raises(ValueError, match="meaningless"):
            Capabilities(supports_batch=True, max_batch=1)

    def test_defaults_are_conservative(self) -> None:
        caps = Capabilities()
        assert caps.supports_batch is False
        assert caps.supports_push is False
        assert caps.needs_auth is AuthKind.NONE


class TestBatched:
    def refs(self, count: int) -> list[ChannelRef]:
        return [ChannelRef(provider="p", platform_ref=str(n), handle=str(n)) for n in range(count)]

    def test_splits_into_request_sized_chunks(self) -> None:
        """V1's C# client never chunked at all, so >100 streamers would have
        produced a request Twitch rejects. Its Python client did. They disagreed."""
        chunks = list(batched(self.refs(250), 100))
        assert [len(chunk) for chunk in chunks] == [100, 100, 50]

    def test_a_short_list_is_one_chunk(self) -> None:
        assert len(list(batched(self.refs(5), 100))) == 1

    def test_an_empty_list_yields_nothing(self) -> None:
        assert list(batched([], 100)) == []

    def test_rejects_a_zero_batch_size(self) -> None:
        """Would loop forever."""
        with pytest.raises(ValueError, match="at least 1"):
            list(batched(self.refs(3), 0))


class TestProviderConfig:
    def test_options_are_readable_with_defaults(self) -> None:
        config = ProviderConfig(provider_id="p", options={"host": "example.com"})
        assert config.option("host") == "example.com"
        assert config.option("missing", "fallback") == "fallback"

    def test_a_missing_credential_says_how_to_supply_it(self) -> None:
        """A headless appliance gives no other chance to explain."""
        config = ProviderConfig(provider_id="twitch")
        with pytest.raises(ProviderAuthError) as caught:
            config.require_secret("client_id")

        message = str(caught.value)
        assert "client_id" in message
        assert "LIVEPULSE_SECRET_TWITCH_CLIENT_ID" in message

    def test_an_empty_credential_counts_as_missing(self) -> None:
        """V1 shipped appsettings.json with ClientId: "" and failed obscurely."""
        config = ProviderConfig(provider_id="twitch", secrets={"client_id": ""})
        with pytest.raises(ProviderAuthError):
            config.require_secret("client_id")

    def test_a_present_credential_is_returned(self) -> None:
        config = ProviderConfig(provider_id="twitch", secrets={"client_id": "abc"})
        assert config.require_secret("client_id") == "abc"
