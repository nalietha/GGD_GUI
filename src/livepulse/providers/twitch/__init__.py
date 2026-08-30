"""Twitch integration.

Importing this package registers the provider.
"""

from livepulse.providers.twitch.auth import AppTokenSource
from livepulse.providers.twitch.eventsub import EventSubWatcher
from livepulse.providers.twitch.poll import PROVIDER_ID, TwitchProvider
from livepulse.providers.twitch.userauth import DeviceCodeFlow, UserTokenSource

__all__ = [
    "PROVIDER_ID",
    "AppTokenSource",
    "DeviceCodeFlow",
    "EventSubWatcher",
    "TwitchProvider",
    "UserTokenSource",
]
