"""Manual provider — state set by hand rather than fetched.

Two real jobs, not a stub:

* **A platform with no API.** Someone streaming somewhere LivePulse cannot
  query still gets a node; the user flips it from the web UI.
* **The test double.** Every scheduler, coordinator, and end-to-end test needs
  a provider it can drive deterministically. Using a fake HTTP server for that
  would test the wrong thing.

It is also the answer for a stand with no internet at all: bind nodes, toggle
them, and the display works with nothing configured.
"""

from __future__ import annotations

from collections.abc import Sequence

from livepulse.core.errors import ChannelNotFoundError
from livepulse.core.models import AuthKind, ChannelRef, LiveState, StateSource
from livepulse.providers.base import BaseProvider, Capabilities, ProviderConfig
from livepulse.providers.registry import register_provider

__all__ = ["ManualProvider"]

PROVIDER_ID = "manual"


class ManualProvider(BaseProvider):
    """Reports whatever it has been told."""

    id = PROVIDER_ID
    caps = Capabilities(
        supports_batch=True,
        # No network, so batch size is unbounded in any practical sense.
        max_batch=10_000,
        supports_push=False,
        needs_auth=AuthKind.NONE,
        # Reading a dict is free, but polling faster than the render loop would
        # be pointless. This is the floor, not a recommendation.
        min_poll_interval=1.0,
    )

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config or ProviderConfig(provider_id=PROVIDER_ID))
        self._live: dict[str, LiveState] = {}
        self._known: set[str] = set()

    async def resolve(self, handle: str) -> ChannelRef:
        """Accept any handle.

        A manual channel has no platform to check against, so the handle is
        both the reference and the name. Rejecting the empty string still
        matters — it would otherwise create an unnameable channel.
        """
        cleaned = handle.strip().lstrip("@")
        if not cleaned:
            raise ChannelNotFoundError(PROVIDER_ID, "a manual channel needs a name")

        self._known.add(cleaned)
        return ChannelRef(provider=PROVIDER_ID, platform_ref=cleaned, handle=cleaned)

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        """Report the state each ref was last set to; offline if never set."""
        found = {
            ref.platform_ref: self._live[ref.platform_ref]
            for ref in refs
            if ref.platform_ref in self._live
        }
        return self.fill_offline(refs, found, source=StateSource.MANUAL)

    # ----------------------------------------------------------------- #
    # Control surface — used by the web UI and by tests
    # ----------------------------------------------------------------- #

    def set_live(self, platform_ref: str, is_live: bool, **details: object) -> LiveState:
        """Declare a channel live or offline.

        Returns the recorded state, so a caller can publish it without
        re-reading.
        """
        state = LiveState(is_live=is_live, source=StateSource.MANUAL, **details)  # type: ignore[arg-type]
        self._live[platform_ref] = state
        return state

    def clear(self, platform_ref: str) -> bool:
        """Forget a channel's state, returning it to 'never observed'."""
        return self._live.pop(platform_ref, None) is not None

    def clear_all(self) -> None:
        self._live.clear()


def _build(config: ProviderConfig) -> ManualProvider:
    return ManualProvider(config)


register_provider(
    PROVIDER_ID,
    _build,
    ManualProvider.caps,
    description="Set live status by hand — for platforms with no API, and for testing",
)
