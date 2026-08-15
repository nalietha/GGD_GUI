"""Import V1's ``save.json`` and ``appsettings.json``.

Preserves ``privateId`` as the channel id, so every existing node binding
survives the move and the stand lights the same way it did before.

The import is defensive because the real V1 files are not clean. The deployed
``save.json`` in this repository contains empty ``ColorHex`` strings, empty
``DisplaySetting`` strings, null ``displayName`` values, and channels no node
references. V1 crashed on the first of those (``hex_to_color("")``) and silently
ignored the rest. Everything here is either mapped, or reported as a warning —
nothing is dropped quietly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, overload
from uuid import UUID, uuid4

import structlog

from livepulse.core.errors import StorageError
from livepulse.core.models import DEFAULT_COLOR, Channel, DisplayMode, Node
from livepulse.leds.color import parse_hex
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

__all__ = ["ImportPlan", "apply_plan", "build_plan", "read_v1_files"]

log = structlog.get_logger(__name__)

#: V1's ``platform`` field mapped to a V2 provider id. Anything unrecognised
#: is lower-cased and kept, so a hand-edited save file is not silently lost.
_PLATFORM_TO_PROVIDER = {
    "twitch": "twitch",
    "youtube": "youtube",
    "kick": "kick",
}

#: V1's ``DisplaySetting`` values mapped to effect registry keys. V1 accepted
#: "static", "pulse", "off", and the empty string (which it treated as static).
_DISPLAY_SETTING_TO_EFFECT = {
    "": "static",
    "static": "static",
    "pulse": "pulse",
    "blink": "blink",
    "off": "off",
}


@dataclass
class ImportPlan:
    """What an import would do. Inspect before applying."""

    channels: list[Channel] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.channels and not self.nodes

    def summary(self) -> str:
        return (
            f"{len(self.channels)} channel(s), {len(self.nodes)} node(s), "
            f"{len(self.settings)} setting(s), {len(self.warnings)} warning(s)"
        )


def read_v1_files(
    save_path: Path,
    appsettings_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and parse the V1 files.

    Raises:
        StorageError: a file is missing or is not valid JSON.
    """
    save_data = _read_json(save_path, "save.json")
    app_data = _read_json(appsettings_path, "appsettings.json") if appsettings_path else {}
    return save_data, app_data


def _read_json(path: Path | None, label: str) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StorageError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageError(f"{label} at {path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise StorageError(f"cannot read {label} at {path}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise StorageError(f"{label} at {path} should contain an object")
    return parsed


def build_plan(save_data: dict[str, Any], app_data: dict[str, Any] | None = None) -> ImportPlan:
    """Translate V1 data into V2 models without touching the database.

    Pure, so the CLI can show a dry run and the tests can assert on the mapping
    without any storage involved.
    """
    plan = ImportPlan()
    app_data = app_data or {}

    by_private_id = _import_channels(save_data, plan)
    _import_nodes(save_data, by_private_id, plan)
    _import_settings(app_data, plan)

    return plan


def _import_channels(save_data: dict[str, Any], plan: ImportPlan) -> dict[str, UUID]:
    """Map V1 streamers to channels, returning ``privateId`` → channel id."""
    by_private_id: dict[str, UUID] = {}
    seen_refs: set[tuple[str, str]] = set()

    for index, streamer in enumerate(save_data.get("streamers", [])):
        private_id = streamer.get("privateId") or ""
        handle = (streamer.get("name") or "").strip()
        platform_ref = (streamer.get("publicStreamerId") or "").strip()

        if not handle:
            plan.warnings.append(f"streamer #{index} has no name; skipped")
            continue

        provider = _map_provider(streamer.get("platform"))

        if not platform_ref:
            # V1 stored these with an empty id when its refresh never ran. The
            # channel is still worth keeping; the provider can resolve it later.
            platform_ref = f"unresolved:{handle.lower()}"
            plan.warnings.append(
                f"{handle!r} has no platform id; imported as unresolved — "
                f"run 'livepulse provider resolve' to fix"
            )

        ref = (provider, platform_ref)
        if ref in seen_refs:
            plan.warnings.append(f"{handle!r} duplicates an earlier {provider} entry; skipped")
            continue
        seen_refs.add(ref)

        channel_id = _parse_uuid(private_id) or uuid4()
        if private_id and channel_id != UUID(private_id or str(channel_id)):  # pragma: no cover
            plan.warnings.append(f"{handle!r} had an unusable privateId; a new id was assigned")

        plan.channels.append(
            Channel(
                id=channel_id,
                provider=provider,
                platform_ref=platform_ref,
                handle=handle,
                display_name=(streamer.get("displayName") or None),
                url=_build_url(provider, handle),
                color=_safe_color(streamer.get("streamerColor"), DEFAULT_COLOR, handle, plan),
                alt_color=_safe_color(streamer.get("altColorHex"), None, handle, plan),
                enabled=True,
            )
        )
        if private_id:
            by_private_id[private_id] = channel_id

    return by_private_id


def _import_nodes(
    save_data: dict[str, Any],
    by_private_id: dict[str, UUID],
    plan: ImportPlan,
) -> None:
    for entry in save_data.get("nodes", []):
        node_id = entry.get("NodeId")
        if not isinstance(node_id, int) or node_id < 0:
            plan.warnings.append(f"node with invalid NodeId {node_id!r}; skipped")
            continue

        linked = (entry.get("LinkedStreamerId") or "").strip()
        channel_id = by_private_id.get(linked) if linked else None
        if linked and channel_id is None:
            # V1 left these dangling and then skipped the node entirely, so it
            # was never driven and never cleared. Import it unbound instead.
            plan.warnings.append(
                f"node {node_id} referenced unknown streamer {linked}; imported unbound"
            )

        raw_setting = (entry.get("DisplaySetting") or "").strip().lower()
        effect = _DISPLAY_SETTING_TO_EFFECT.get(raw_setting)
        if effect is None:
            effect = "static"
            plan.warnings.append(
                f"node {node_id} had unknown DisplaySetting {raw_setting!r}; using 'static'"
            )

        plan.nodes.append(
            Node(
                id=node_id,
                channel_id=channel_id,
                effect=effect,
                color=_safe_color(entry.get("ColorHex"), DEFAULT_COLOR, f"node {node_id}", plan),
            )
        )


def _import_settings(app_data: dict[str, Any], plan: ImportPlan) -> None:
    """Carry over the GGDDisplay section, resolving V1's mode confusion.

    V1 spelled 'mode' three incompatible ways. What actually determined
    behaviour was the ``StreamerModeEnabled`` boolean, so that is what maps.
    """
    section = app_data.get("GGDDisplay") or app_data.get("GGD_Display") or {}
    if not isinstance(section, dict):
        plan.warnings.append("GGDDisplay section is not an object; settings not imported")
        return

    brightness = section.get("Brightness")
    if isinstance(brightness, int) and 0 <= brightness <= 255:
        plan.settings["brightness"] = brightness
    elif brightness is not None:
        plan.warnings.append(f"ignoring out-of-range Brightness {brightness!r}")

    streamer_mode = section.get("StreamerModeEnabled")
    if isinstance(streamer_mode, bool):
        plan.settings["mode"] = (
            DisplayMode.STREAMER if streamer_mode else DisplayMode.AMBIENT
        ).value

    if section.get("AdultContentCheckEnabled"):
        plan.warnings.append(
            "AdultContentCheckEnabled was set, but V1's platform plugins never "
            "performed a check. Configure a generic HTTP provider instead."
        )


async def apply_plan(
    plan: ImportPlan,
    repos: Repositories,
    settings_store: RuntimeSettingsStore | None = None,
) -> ImportPlan:
    """Write a plan to the database.

    All-or-nothing: channels and their node bindings land together, because a
    half-applied import that creates channels but not bindings is worse than
    none at all.

    Raises:
        StorageError: the database already holds channels, or a write failed.
    """
    existing = await repos.channels.list()
    if existing:
        raise StorageError(
            f"refusing to import over {len(existing)} existing channel(s); "
            "start from an empty database"
        )

    for channel in plan.channels:
        await repos.channels.add(channel)

    if plan.nodes:
        await repos.nodes.ensure(max(node.id for node in plan.nodes) + 1)
        for node in plan.nodes:
            await repos.nodes.update(node)

    if settings_store is not None and plan.settings:
        await settings_store.update(**plan.settings)

    log.info("v1 import applied", summary=plan.summary())
    return plan


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _map_provider(platform: Any) -> str:
    text = str(platform or "twitch").strip().lower()
    return _PLATFORM_TO_PROVIDER.get(text, text or "twitch")


def _build_url(provider: str, handle: str) -> str | None:
    """Best-effort channel URL.

    V1's UI hardcoded ``https://twitch.tv/{name}`` in JavaScript with a TODO
    to store it per-platform. This is that TODO.
    """
    templates = {
        "twitch": "https://twitch.tv/{handle}",
        "youtube": "https://youtube.com/@{handle}",
        "kick": "https://kick.com/{handle}",
    }
    template = templates.get(provider)
    return template.format(handle=handle) if template else None


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


@overload
def _safe_color(value: Any, fallback: str, context: str, plan: ImportPlan) -> str: ...


@overload
def _safe_color(value: Any, fallback: None, context: str, plan: ImportPlan) -> str | None: ...


def _safe_color(
    value: Any,
    fallback: str | None,
    context: str,
    plan: ImportPlan,
) -> str | None:
    """Validate a V1 colour, falling back rather than failing the import.

    The deployed save.json holds ``"ColorHex": ""`` on two nodes, which is what
    made V1's ``hex_to_color`` raise.
    """
    if value is None or not str(value).strip():
        return fallback
    try:
        parse_hex(str(value))
    except Exception:
        plan.warnings.append(f"{context} had invalid colour {value!r}; using {fallback}")
        return fallback
    return str(value).strip()
