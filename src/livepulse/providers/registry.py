"""Provider registration and discovery.

The one place that maps a provider id to a constructor. Adding a platform means
registering it here and nowhere else — the scheduler, the renderer, the web
layer, and the storage schema all stay untouched.

This module also owns URL identification: each provider registers the link
shapes it recognises, so pasting ``twitch.tv/someone`` or ``kick.com/someone``
resolves to the right platform automatically. V1 had that logic
(``ExtractUsernameAndPlatform``) but never called it — every channel it added
was hardcoded ``Platform = "Twitch"``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import structlog

from livepulse.core.errors import ConfigError
from livepulse.providers.base import Capabilities, Provider, ProviderConfig, handle_from_url

__all__ = [
    "ProviderFactory",
    "available_providers",
    "create_provider",
    "describe_providers",
    "identify",
    "register_provider",
]

log = structlog.get_logger(__name__)

#: Builds a provider from its configuration.
ProviderFactory = Callable[[ProviderConfig], Provider]


@dataclass(frozen=True, slots=True)
class _Registration:
    provider_id: str
    factory: ProviderFactory
    caps: Capabilities
    description: str
    url_patterns: tuple[re.Pattern[str], ...] = field(default=())


_PROVIDERS: dict[str, _Registration] = {}


def register_provider(
    provider_id: str,
    factory: ProviderFactory,
    caps: Capabilities,
    description: str = "",
    url_patterns: Sequence[str] = (),
) -> None:
    """Make a platform available.

    Args:
        provider_id: Stored in ``channels.provider``. Lowercase and stable —
            renaming it orphans every channel already saved under the old name.
        factory: Builds the provider from its configuration.
        caps: Declared up front so the scheduler can plan without constructing
            anything, and so ``livepulse provider list`` works with no credentials.
        description: One line for the UI.
        url_patterns: Regexes with a named ``handle`` group, used by
            :func:`identify`. Order matters only within one provider.
    """
    key = provider_id.strip().lower()
    _PROVIDERS[key] = _Registration(
        provider_id=key,
        factory=factory,
        caps=caps,
        description=description,
        url_patterns=tuple(re.compile(pattern, re.IGNORECASE) for pattern in url_patterns),
    )


def create_provider(provider_id: str, config: ProviderConfig | None = None) -> Provider:
    """Construct a provider.

    Raises:
        ConfigError: no such provider, listing the registered ids.
    """
    key = provider_id.strip().lower()
    registration = _PROVIDERS.get(key)
    if registration is None:
        raise ConfigError(
            f"unknown provider {provider_id!r}; expected one of "
            f"{', '.join(sorted(_PROVIDERS)) or '<none registered>'}"
        )

    return registration.factory(config or ProviderConfig(provider_id=key))


def capabilities(provider_id: str) -> Capabilities:
    """A provider's declared capabilities, without constructing it."""
    key = provider_id.strip().lower()
    registration = _PROVIDERS.get(key)
    if registration is None:
        raise ConfigError(f"unknown provider {provider_id!r}")
    return registration.caps


def available_providers() -> list[str]:
    """Registered provider ids, sorted."""
    return sorted(_PROVIDERS)


def describe_providers() -> dict[str, str]:
    """Provider ids mapped to their descriptions, for the UI."""
    return {key: reg.description for key, reg in sorted(_PROVIDERS.items())}


def identify(text: str) -> tuple[str, str] | None:
    """Work out which platform a pasted link or handle belongs to.

    Returns:
        ``(provider_id, handle)``, or ``None`` when nothing matches — a bare
        username, say, which could belong to any platform and so must not be
        guessed. The caller asks the user which platform they meant.

    >>> identify("https://twitch.tv/ironmouse")     # doctest: +SKIP
    ('twitch', 'ironmouse')
    """
    candidate = text.strip()
    if not candidate:
        return None

    for registration in _PROVIDERS.values():
        for pattern in registration.url_patterns:
            match = pattern.search(candidate)
            if match:
                handle = match.group("handle")
                return registration.provider_id, handle_from_url(handle)
    return None


def resolve_input(text: str, default_provider: str | None = None) -> tuple[str, str]:
    """Identify a pasted value, falling back to a stated platform.

    Raises:
        ConfigError: the input matches no known link shape and no default was
            given. Guessing would silently file a YouTube channel under Twitch,
            which is what V1 did to every channel it ever added.
    """
    identified = identify(text)
    if identified is not None:
        return identified

    if default_provider is None:
        raise ConfigError(
            f"cannot tell which platform {text!r} belongs to; "
            f"paste a full channel URL or name a provider "
            f"({', '.join(available_providers())})"
        )

    key = default_provider.strip().lower()
    if key not in _PROVIDERS:
        raise ConfigError(f"unknown provider {default_provider!r}")
    return key, handle_from_url(text)
