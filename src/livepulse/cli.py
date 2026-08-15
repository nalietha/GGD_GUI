"""Command line interface.

Also the composition root: this is the one place that resolves configuration,
constructs concrete implementations, and wires them together. Everything below
receives its collaborators as arguments and depends only on protocols.

Subcommands are added as the phases land (``run``, ``leds``, ``provider``,
``migrate-v1``); ``doctor`` grows a check per subsystem.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from livepulse import __version__
from livepulse.config.settings import Settings, default_config_path, load_settings
from livepulse.core.errors import LivePulseError
from livepulse.logging import configure_logging

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

    check(
        "layout is consistent",
        settings.layout.pixel_count > 0,
        f"{settings.layout.nodes} nodes x {settings.layout.leds_per_node} = "
        f"{settings.layout.pixel_count} pixels",
    )

    typer.echo("")
    if failures:
        typer.secho(f"{failures} check(s) failed", fg=typer.colors.RED, bold=True)
        raise typer.Exit(code=1)
    typer.secho("all checks passed", fg=typer.colors.GREEN, bold=True)
