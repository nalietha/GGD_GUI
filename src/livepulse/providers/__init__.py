"""Stream platforms.

Importing this package registers the built-in providers, so callers only need
:func:`~livepulse.providers.registry.create_provider`.
"""

from livepulse.providers import manual  # noqa: F401  - registers on import
from livepulse.providers.base import (
    BaseProvider,
    Capabilities,
    Provider,
    ProviderConfig,
    PushProvider,
    batched,
)
from livepulse.providers.registry import (
    available_providers,
    capabilities,
    create_provider,
    describe_providers,
    identify,
    register_provider,
    resolve_input,
)

__all__ = [
    "BaseProvider",
    "Capabilities",
    "Provider",
    "ProviderConfig",
    "PushProvider",
    "available_providers",
    "batched",
    "capabilities",
    "create_provider",
    "describe_providers",
    "identify",
    "register_provider",
    "resolve_input",
]
