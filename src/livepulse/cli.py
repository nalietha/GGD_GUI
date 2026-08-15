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
import time
from pathlib import Path
from typing import Annotated

import typer

from livepulse import __version__
from livepulse.config.settings import Settings, default_config_path, load_settings
from livepulse.core.errors import LivePulseError
from livepulse.core.models import DisplayMode, LiveState, StateSource
from livepulse.leds.color import BLACK, GammaTable, output_value, parse_hex
from livepulse.leds.compositor import Compositor
from livepulse.leds.driver.base import LedDriver
from livepulse.leds.driver.registry import available_backends, create_driver, deprecated_backends
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import Renderer
from livepulse.leds.state import build_render_state
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
    """List LED backends, whether each can run here, and which are retiring."""
    retiring = deprecated_backends()

    for name, ok in available_backends().items():
        mark, colour = (
            ("available", typer.colors.GREEN) if ok else ("unavailable", typer.colors.YELLOW)
        )
        suffix = "  [deprecated]" if name in retiring else ""
        typer.secho(f"  {name:<10} {mark}{suffix}", fg=colour)

    for name, migration in retiring.items():
        typer.echo("")
        typer.secho(f"{name} is deprecated", fg=typer.colors.YELLOW, bold=True)
        typer.secho(f"  {migration}", fg=typer.colors.YELLOW)


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


def _open_display(settings: Settings) -> tuple[Layout, LedDriver, GammaTable]:
    """Build the layout, driver, and gamma table the diagnostics need.

    Uses the same construction path the render loop will, so what these
    commands put on the strip is what the application would put there.
    """
    try:
        layout = Layout(settings.layout)
        driver = create_driver(layout.pixel_count, settings.leds)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    return layout, driver, GammaTable(settings.leds.gamma)


@leds_app.command("test")
def leds_test(
    config: ConfigOption = None,
    color: Annotated[str, typer.Option("--color", help="Hex colour to display.")] = "#ffffff",
    brightness: Annotated[
        int, typer.Option("--brightness", min=1, max=255, help="Master brightness.")
    ] = 64,
    seconds: Annotated[float, typer.Option("--seconds", help="How long to hold it.")] = 3.0,
) -> None:
    """Light every pixel, then clear.

    The first thing to run after wiring or rewiring: it proves power, data, and
    colour order without involving channels, effects, or the network.
    """
    settings = _load(config)
    configure_logging(level=settings.log_level, json=settings.log_json)

    try:
        wanted = parse_hex(color)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    _, driver, gamma = _open_display(settings)
    try:
        driver.fill(output_value(wanted, brightness, gamma))
        driver.show()
        typer.echo(f"{driver.pixel_count} pixels at {color} — holding {seconds:g}s")
        time.sleep(seconds)
    finally:
        # close() blanks the strip, so an interrupted test never leaves it lit.
        driver.close()

    typer.secho("cleared", fg=typer.colors.GREEN)


@leds_app.command("identify")
def leds_identify(
    config: ConfigOption = None,
    color: Annotated[str, typer.Option("--color", help="Hex colour to display.")] = "#ffffff",
    brightness: Annotated[
        int, typer.Option("--brightness", min=1, max=255, help="Master brightness.")
    ] = 64,
    dwell: Annotated[float, typer.Option("--dwell", help="Seconds to hold each node.")] = 1.0,
) -> None:
    """Light one node at a time, announcing which it is.

    This is how you verify the layout against the physical stand — especially
    after moving the data pin. Logical node 0 should light the leftmost slot.
    If the sequence runs backwards or starts in the wrong row, adjust
    ``layout.start_at_last_row`` or ``layout.first_row_reversed`` rather than
    editing any code.
    """
    settings = _load(config)
    configure_logging(level=settings.log_level, json=settings.log_json)

    try:
        wanted = parse_hex(color)
    except LivePulseError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    layout, driver, gamma = _open_display(settings)
    lit = output_value(wanted, brightness, gamma)

    try:
        for node_id in layout.nodes():
            driver.fill(BLACK)
            for pixel in layout.pixels(node_id):
                driver.set_pixel(pixel, lit)
            driver.show()

            physical = layout.physical_position(node_id)
            typer.echo(f"  node {node_id:>2}  (physical {physical:>2})  — should be lit now")
            time.sleep(dwell)
    finally:
        driver.close()

    typer.secho("done", fg=typer.colors.GREEN)


@leds_app.command("demo")
def leds_demo(
    config: ConfigOption = None,
    seconds: Annotated[float, typer.Option("--seconds", help="How long to run.")] = 10.0,
    live: Annotated[
        list[str] | None,
        typer.Option("--live", help="Pretend this channel handle is live. Repeatable."),
    ] = None,
    mode: Annotated[
        DisplayMode | None,
        typer.Option("--mode", help="Override the stored display mode.", show_default=False),
    ] = None,
    flash: Annotated[
        bool, typer.Option("--flash/--no-flash", help="Fire a go-live flash at the start.")
    ] = True,
) -> None:
    """Render the configured stand for a while, with no network.

    Reads real channels, nodes, and settings from the database and drives the
    full pipeline — state resolution, effects, compositor, driver — exactly as
    the service will. Use ``--live`` to simulate someone broadcasting.

    This is how to check that an imported V1 configuration looks right before
    any provider exists to poll.
    """
    settings = _load(config)
    configure_logging(level=settings.log_level, json=settings.log_json)
    asyncio.run(_run_demo(settings, seconds, set(live or []), mode=mode, flash=flash))


async def _run_demo(
    settings: Settings,
    seconds: float,
    live_handles: set[str],
    *,
    mode: DisplayMode | None,
    flash: bool,
) -> None:
    async with Database(settings.db_path) as db:
        repos = Repositories(db)
        store = RuntimeSettingsStore(repos.settings)
        await store.load()
        # Override in memory only — a diagnostic must not rewrite the user's mode.
        display = store.current if mode is None else store.current.model_copy(update={"mode": mode})

        channels = await repos.channels.list()
        nodes = await repos.nodes.list()

    if not nodes:
        typer.secho(
            "no nodes configured — run 'livepulse migrate-v1' first",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    by_id = {channel.id: channel for channel in channels}
    wanted = {handle.lower() for handle in live_handles}
    states = {
        channel.id: LiveState(is_live=channel.handle.lower() in wanted, source=StateSource.MANUAL)
        for channel in channels
    }

    layout = Layout(settings.layout)
    driver = create_driver(layout.pixel_count, settings.leds)
    renderer = Renderer(
        driver, Compositor(layout, GammaTable(settings.leds.gamma)), fps=settings.leds.fps
    )

    state = build_render_state(nodes, by_id, states, display)
    renderer.submit(state)

    lit = [view.node_id for view in state.nodes if view.is_live]
    suffix = " (overridden)" if mode is not None else ""
    typer.echo(f"mode {display.mode.value}{suffix}, brightness {display.brightness}")
    typer.echo(f"{len(channels)} channels, {len(nodes)} nodes, {len(lit)} live")
    if state.ambient_active:
        # Two different reasons land here; saying which avoids the reading
        # that live channels are being ignored by mistake.
        reason = (
            "ambient mode — live status is deliberately ignored"
            if display.mode is DisplayMode.AMBIENT
            else "nobody live — showing the ambient effect"
        )
        typer.echo(reason)

    with renderer:
        if flash and lit:
            renderer.compositor.add_transient(set(lit), "flash", now=0.0)
        time.sleep(seconds)

    typer.secho(
        f"rendered {renderer.stats.frames} frames ({renderer.stats.late_frames} late)",
        fg=typer.colors.GREEN,
    )


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
