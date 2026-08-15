"""Exception hierarchy.

One root so callers can catch everything from this application without
swallowing genuine programming errors (``TypeError``, ``KeyError``, ...).
V1 caught bare ``Exception`` and printed, which hid the config-key bugs for months.
"""

from __future__ import annotations


class LivePulseError(Exception):
    """Root of every error this application raises deliberately."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class ConfigError(LivePulseError):
    """Configuration is missing, malformed, or internally inconsistent."""


class LayoutError(ConfigError):
    """The physical layout description cannot produce a valid node→pixel map."""


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


class StorageError(LivePulseError):
    """Persistence layer failure."""


class MigrationError(StorageError):
    """The schema could not be brought to the expected version."""


# --------------------------------------------------------------------------- #
# Hardware
# --------------------------------------------------------------------------- #


class DriverError(LivePulseError):
    """LED driver failure."""


class DriverUnavailableError(DriverError):
    """The requested backend exists but cannot run here.

    Raised when the platform library is missing, the kernel interface is
    disabled, or the process lacks permission. Carries a remedy so the CLI
    can tell the user what to actually do about it.
    """

    def __init__(self, backend: str, reason: str, remedy: str | None = None) -> None:
        self.backend = backend
        self.reason = reason
        self.remedy = remedy
        message = f"LED backend {backend!r} unavailable: {reason}"
        if remedy:
            message = f"{message}\n  Try: {remedy}"
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class ProviderError(LivePulseError):
    """A stream platform integration failed."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class ProviderAuthError(ProviderError):
    """Credentials are absent, invalid, or expired beyond refresh."""


class ProviderRateLimited(ProviderError):
    """The platform asked us to slow down.

    ``retry_after`` is seconds, taken from the response when the platform
    supplies it so the scheduler can back off by the amount actually requested
    rather than a guess.
    """

    def __init__(self, provider: str, retry_after: float, message: str = "rate limited") -> None:
        self.retry_after = retry_after
        super().__init__(provider, f"{message} (retry after {retry_after:.1f}s)")


class ChannelNotFoundError(ProviderError):
    """A handle could not be resolved to a channel on this platform."""
