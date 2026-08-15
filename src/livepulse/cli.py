"""Command line interface.

Also the composition root: this is the one place that resolves configuration,
constructs concrete implementations, and wires them together. Everything below
receives its collaborators as arguments and depends only on protocols.

Subcommands are added as the phases land (``run``, ``leds``, ``provider``,
``migrate-v1``); ``doctor`` grows a check per subsystem.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from livepulse import __version__
from livepulse.config.settings import Settings, default_config_path, load_settings
from livepulse.core.errors import LivePulseError
from livepulse.leds.driver.registry import available_backends
from livepulse.leds.layout import Layout
from livepulse.logging import configure_logging
from livepulse.storage.db import Database
from livepulse.storage.import_v1 import ImportPlan, apply_plan, build_plan, read_v1_files
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

app = typer.Typer(
    name="livepulse",
    help="Platform-agnostic live-stream status LED display.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config.toml.", show_default=False),
]


def _load(config: Path | None) -> Settings:
    """Resolve settings or exit with a readable message.

    Configuration errors are user errors, not crashes: print the problem and
    exit non-zero rather than showing a traceback.
    """
    try:
        return load_settings(config)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(f"livepulse {__version__}")


@app.command(name="config")
def show_config(
    config: ConfigOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show the effective configuration after all layers are merged.

    Answers "why is it doing that?" without guessing which file won.
    """
    settings = _load(config)
    source = config or default_config_path()

    if as_json:
        typer.echo(json.dumps(settings.model_dump(mode="json"), indent=2))
        return

    typer.secho(f"config file : {source or '<none — defaults + environment>'}", bold=True)
    typer.echo(f"data dir    : {settings.data_dir}")
    typer.echo(f"database    : {settings.db_path}")
    typer.echo(f"log level   : {settings.log_level}")
    typer.echo("")
    typer.secho("layout", bold=True)
    typer.echo(f"  nodes         : {settings.layout.nodes}")
    typer.echo(f"  leds per node : {settings.layout.leds_per_node}")
    typer.echo(f"  pixels        : {settings.layout.pixel_count}")
    typer.echo(f"  order         : {settings.layout.order} ({settings.layout.rows} rows)")
    typer.echo("")
    typer.secho("leds", bold=True)
    typer.echo(f"  backend    : {settings.leds.backend}")
    typer.echo(f"  fps        : {settings.leds.fps}")
    typer.echo(f"  gamma      : {settings.leds.gamma}")
    typer.echo("")
    typer.secho("web", bold=True)
    typer.echo(f"  listen : {settings.web.host}:{settings.web.port}")


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Check that this machine can actually run LivePulse.

    Exits non-zero if anything is wrong, so it is usable as a healthcheck.
    """
    settings = _load(config)
    configure_logging(level=settings.log_level, json=settings.log_json)

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        mark, colour = ("ok", typer.colors.GREEN) if ok else ("FAIL", typer.colors.RED)
        if not ok:
            failures += 1
        typer.secho(f"[{mark:>4}] {label}" + (f" — {detail}" if detail else ""), fg=colour)

    typer.secho("livepulse doctor", bold=True)

    check(
        "python >= 3.11",
        sys.version_info >= (3, 11),
        f"running {sys.version_info.major}.{sys.version_info.minor}",
    )
    check(
        "config loads",
        True,
        str(config or default_config_path() or "defaults + environment"),
    )

    data_dir = settings.data_dir
    if data_dir.exists():
        # A directory that exists but is not writable is the common systemd
        # failure; probe for real rather than trusting the mode bits.
        probe = data_dir / ".livepulse-write-test"
        try:
            probe.touch()
            probe.unlink()
            check("data dir writable", True, str(data_dir))
        except OSError as exc:
            check("data dir writable", False, f"{data_dir}: {exc}")
    else:
        check("data dir exists", False, f"{data_dir} — run: mkdir -p {data_dir}")

    try:
        layout = Layout(settings.layout)
        check(
            "layout builds",
            True,
            f"{layout.node_count} nodes x {layout.leds_per_node} = {layout.pixel_count} pixels",
        )
    except LivePulseError as exc:
        check("layout builds", False, str(exc))

    backends = available_backends()
    usable = [name for name, ok in backends.items() if ok]
    check(
        "an LED backend is usable",
        bool(usable),
        ", ".join(f"{name}{'' if ok else ' (unavailable)'}" for name, ok in backends.items()),
    )
    if settings.leds.backend != "auto" and not backends.get(settings.leds.backend, False):
        check(
            f"configured backend {settings.leds.backend!r} is available",
            False,
            "auto-selection would fall back; this configuration will fail at startup",
        )

    typer.echo("")
    if failures:
        typer.secho(f"{failures} check(s) failed", fg=typer.colors.RED, bold=True)
        raise typer.Exit(code=1)
    typer.secho("all checks passed", fg=typer.colors.GREEN, bold=True)


# --------------------------------------------------------------------------- #
# leds
# --------------------------------------------------------------------------- #

leds_app = typer.Typer(help="Inspect and exercise the LED hardware.", no_args_is_help=True)
app.add_typer(leds_app, name="leds")


@leds_app.command("backends")
def leds_backends() -> None:
    """List LED backends and whether each can run on this machine."""
    for name, ok in available_backends().items():
        mark, colour = (
            ("available", typer.colors.GREEN) if ok else ("unavailable", typer.colors.YELLOW)
        )
        typer.secho(f"  {name:<10} {mark}", fg=colour)


@leds_app.command("layout")
def leds_layout(config: ConfigOption = None) -> None:
    """Show how logical nodes map onto the strip.

    Use this to check the config against the actual wiring: logical node 0 is
    the leftmost slot on the stand, and its physical position is how far along
    the data line it sits. V1 buried this in a copy-pasted expression, so
    verifying it meant reading three files in two languages.
    """
    settings = _load(config)
    try:
        layout = Layout(settings.layout)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.secho(f"{'logical':>8}  {'physical':>8}  pixels", bold=True)
    for logical, physical, pixels in layout.describe():
        typer.echo(f"{logical:>8}  {physical:>8}  {pixels.start}..{pixels.stop - 1}")


# --------------------------------------------------------------------------- #
# migrate-v1
# --------------------------------------------------------------------------- #


@app.command("migrate-v1")
def migrate_v1(
    save: Annotated[Path, typer.Argument(help="Path to V1's save.json.")],
    appsettings: Annotated[
        Path | None,
        typer.Option("--appsettings", help="Path to V1's appsettings.json.", show_default=False),
    ] = None,
    config: ConfigOption = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Write to the database instead of previewing.")
    ] = False,
) -> None:
    """Import a V1 configuration.

    Previews by default. Node bindings survive because V1's ``privateId`` becomes
    the channel id unchanged, so the stand lights exactly as it did before.
    """
    settings = _load(config)
    configure_logging(level=settings.log_level, json=settings.log_json)

    try:
        save_data, app_data = read_v1_files(save, appsettings)
        plan = build_plan(save_data, app_data)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.secho(f"channels ({len(plan.channels)})", bold=True)
    for channel in plan.channels:
        typer.echo(f"  {channel.label:<20} {channel.provider:<10} {channel.color}")

    bound = sum(1 for node in plan.nodes if node.channel_id)
    typer.echo("")
    typer.secho(f"nodes ({len(plan.nodes)})", bold=True)
    typer.echo(f"  {bound} bound, {len(plan.nodes) - bound} unbound")

    if plan.settings:
        typer.echo("")
        typer.secho("settings", bold=True)
        for key, value in sorted(plan.settings.items()):
            typer.echo(f"  {key} = {value}")

    if plan.warnings:
        typer.echo("")
        typer.secho(f"warnings ({len(plan.warnings)})", bold=True, fg=typer.colors.YELLOW)
        for warning in plan.warnings:
            typer.secho(f"  {warning}", fg=typer.colors.YELLOW)

    typer.echo("")
    if not apply:
        typer.secho(f"dry run — {plan.summary()}", bold=True)
        typer.echo("Re-run with --apply to write to the database.")
        return

    asyncio.run(_apply_import(plan, settings))
    typer.secho(f"imported — {plan.summary()}", fg=typer.colors.GREEN, bold=True)


async def _apply_import(plan: ImportPlan, settings: Settings) -> None:
    """Open the database, migrate the schema, and write the plan."""
    try:
        async with Database(settings.db_path) as db:
            repos = Repositories(db)
            store = RuntimeSettingsStore(repos.settings)
            await store.load()
            await apply_plan(plan, repos, store)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
