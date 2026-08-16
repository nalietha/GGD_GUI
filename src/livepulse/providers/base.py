"""The provider contract.

A provider answers one question about one platform: *is this channel live?*
Twitch is one implementation among many — there is no Twitch-shaped hole
anywhere else in the application.

Two protocols rather than one, because the platforms genuinely differ. Every
provider can be polled; only some can hold a subscription. Forcing a poll-only
provider to stub out ``watch`` would be an interface it cannot honour, so
:class:`PushProvider` is separate and the scheduler checks for it.

The polling contract
--------------------

:meth:`Provider.poll` must return an entry for **every** ref it was given,
including an explicit offline state for channels the platform did not mention.
That is what keeps "observed offline" distinct from "never checked" — the
distinction V1 lost, which left unpolled nodes holding a stale colour forever.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from livepulse.core.models import AuthKind, ChannelRef, LiveState, StateSource

__all__ = [
    "BaseProvider",
    "Capabilities",
    "EmitFn",
    "Provider",
    "ProviderConfig",
    "PushProvider",
    "batched",
    "handle_from_url",
]

#: Called by a push provider when a channel's state changes.
EmitFn = Callable[[ChannelRef, LiveState], Awaitable[None]]

#: Chunking is not ChannelRef-specific; the scheduler batches its own
#: bookkeeping objects with the same helper.
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a provider can do, so the scheduler can plan around it.

    Declared rather than discovered: the scheduler batches, paces, and chooses
    push-versus-poll from this alone, and never special-cases a provider by name.
    """

    supports_batch: bool = False
    """Whether one request can cover several channels. Twitch can; most cannot."""

    max_batch: int = 1
    """Channels per request. Twitch Helix caps ``user_id`` at 100."""

    supports_push: bool = False
    """Whether the provider also implements :class:`PushProvider`."""

    needs_auth: AuthKind = AuthKind.NONE

    min_poll_interval: float = 30.0
    """Provider-enforced floor in seconds. The scheduler never polls faster,
    even when the global configuration asks it to."""

    def __post_init__(self) -> None:
        if self.max_batch < 1:
            raise ValueError(f"max_batch must be at least 1, got {self.max_batch}")
        if self.supports_batch and self.max_batch == 1:
            raise ValueError("supports_batch is meaningless with max_batch of 1")


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Everything a provider needs to be constructed.

    Secrets are kept apart from options because options are safe to render in
    the web UI and secrets are not. V1 stored the Twitch client secret in the
    same file the settings page rewrote.
    """

    provider_id: str
    options: Mapping[str, Any] = field(default_factory=dict)
    """From ``[providers.<id>]`` in config.toml."""

    secrets: Mapping[str, str] = field(default_factory=dict)
    """From the secrets file or the environment. Never logged, never returned."""

    timeout: float = 10.0

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def secret(self, key: str) -> str | None:
        return self.secrets.get(key) or None

    def require_secret(self, key: str) -> str:
        """Fetch a credential or fail with a message naming what is missing.

        Raises:
            ProviderAuthError: the credential is absent or empty.
        """
        from livepulse.core.errors import ProviderAuthError

        value = self.secret(key)
        if not value:
            raise ProviderAuthError(
                self.provider_id,
                f"missing credential {key!r}; set it in secrets.toml or "
                f"LIVEPULSE_SECRET_{self.provider_id.upper()}_{key.upper()}",
            )
        return value


@runtime_checkable
class Provider(Protocol):
    """Resolves handles and reports live state for one platform."""

    id: str
    caps: Capabilities

    async def resolve(self, handle: str) -> ChannelRef:
        """Turn a handle or URL into a stable platform reference.

        Called once when a channel is added, not on every poll — platform ids
        do not change when someone renames.

        Raises:
            ChannelNotFoundError: no such channel on this platform.
            ProviderAuthError: credentials are absent or rejected.
        """
        ...

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        """Report the live state of every ref, keyed by ``platform_ref``.

        Must contain an entry for every ref given, using an explicit offline
        state where the platform reported nothing. Never returns a partial map.

        Callers pass at most ``caps.max_batch`` refs.

        Raises:
            ProviderAuthError: credentials were rejected.
            ProviderRateLimited: the platform asked us to slow down.
            ProviderError: anything else the platform did wrong.
        """
        ...

    async def aclose(self) -> None:
        """Release sockets and sessions. Idempotent."""
        ...


@runtime_checkable
class PushProvider(Protocol):
    """A provider that can also hold a live subscription.

    Implemented only where the platform genuinely supports it — Twitch EventSub
    over WebSocket, or an inbound webhook. The scheduler drops a pushed channel
    to a slow reconciliation poll rather than stopping entirely, because a
    dropped socket must not silently freeze the display.
    """

    async def watch(self, refs: Sequence[ChannelRef], emit: EmitFn) -> None:
        """Hold a subscription, calling ``emit`` on every change.

        Runs until cancelled. Raising is allowed — the supervisor restarts it
        with backoff and falls back to polling meanwhile.
        """
        ...


class BaseProvider:
    """Optional convenience base. Providers may implement the protocol directly.

    Supplies the parts every provider would otherwise repeat: identity, a
    no-op ``aclose``, and the offline-filling that the poll contract requires.
    """

    id: str = "base"
    caps: Capabilities = Capabilities()

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def aclose(self) -> None:
        """No resources by default."""

    def fill_offline(
        self,
        refs: Sequence[ChannelRef],
        found: Mapping[str, LiveState],
        source: StateSource = StateSource.POLL,
    ) -> dict[str, LiveState]:
        """Complete a partial result with explicit offline states.

        Platforms return only what is live, so this is where "absent from the
        response" becomes "observed offline" rather than "unknown". Every
        batch provider needs it, so it lives here once.
        """
        return {
            ref.platform_ref: found.get(ref.platform_ref) or LiveState.offline(source=source)
            for ref in refs
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id!r})"


def batched(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Split a sequence into request-sized chunks.

    V1's C# Twitch client did not chunk at all, so more than 100 streamers
    would have produced a request the API rejects. Its Python client chunked,
    the two disagreed, and only one of them was ever right.
    """
    if size < 1:
        raise ValueError(f"batch size must be at least 1, got {size}")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def handle_from_url(text: str) -> str:
    """Reduce a pasted URL to its last path segment.

    Providers that need platform-specific parsing override this; it exists so
    the common case — someone pasting a channel link — is handled once.
    """
    cleaned = text.strip().rstrip("/")
    if "://" not in cleaned and "." not in cleaned.split("/")[0]:
        return cleaned.lstrip("@")

    without_query = cleaned.split("?", 1)[0].split("#", 1)[0]
    segment = without_query.rsplit("/", 1)[-1]
    return segment.lstrip("@") or cleaned
