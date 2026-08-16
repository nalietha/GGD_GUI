"""Raspberry Pi hardware detection.

Answers the questions backend selection needs and cannot guess: which Pi is
this, and is the SPI peripheral actually enabled?

Everything here reads from injectable paths so the tests can describe a Pi 5, a
Pi 3, or a laptop without any of them being present.

What this deliberately *cannot* detect is which GPIO pin the strip's data line
is soldered to. That is a physical fact with no software signal, which is why
:mod:`livepulse.leds.driver.registry` refuses to infer it — see
``docs/hardware.md``.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

__all__ = [
    "DEVICE_TREE_MODEL",
    "conflicting_led_processes",
    "pi_generation",
    "pi_model",
    "spi_devices",
    "supports_pwm_ws281x",
]

#: The kernel exposes the board name here on every Raspberry Pi OS install.
DEVICE_TREE_MODEL = Path("/proc/device-tree/model")

DEV_DIR = Path("/dev")

#: "Raspberry Pi 5 Model B Rev 1.0" → 5. Deliberately does not match the Zero
#: family ("Raspberry Pi Zero 2 W"), whose "2" is a revision, not a generation.
_MODEL_GENERATION = re.compile(r"^Raspberry Pi (\d+)\b")

#: Generation at which the PWM/DMA peripherals that ``rpi_ws281x`` drives moved
#: behind the RP1 southbridge. The library cannot work from here on.
FIRST_UNSUPPORTED_PWM_GENERATION = 5


def pi_model(source: Path = DEVICE_TREE_MODEL) -> str | None:
    """The board name, or ``None`` when this is not a Raspberry Pi.

    The device-tree value carries a trailing NUL byte, which is stripped.
    """
    try:
        raw = source.read_bytes()
    except (OSError, ValueError):
        return None
    return raw.decode("utf-8", errors="replace").strip("\x00").strip() or None


def pi_generation(source: Path = DEVICE_TREE_MODEL) -> int | None:
    """The numbered generation (3, 4, 5, ...), or ``None`` if not determinable.

    ``None`` covers laptops, Compute Modules, and the Zero family. Callers must
    treat it as "unknown", never as "old" or "new".
    """
    model = pi_model(source)
    if model is None:
        return None
    match = _MODEL_GENERATION.match(model)
    return int(match.group(1)) if match else None


def supports_pwm_ws281x(source: Path = DEVICE_TREE_MODEL) -> bool:
    """Whether ``rpi_ws281x`` can drive this board at all.

    False only where we positively know it cannot — a Pi 5 or newer. Unknown
    hardware is allowed through, because refusing to run on a board we merely
    failed to recognise would be worse than letting the driver report its own
    failure with a real error message.
    """
    generation = pi_generation(source)
    return generation is None or generation < FIRST_UNSUPPORTED_PWM_GENERATION


#: V1 executables, matched against argv[0]'s basename.
V1_BINARIES = frozenset({"GGD_Display"})

#: V1 Python entry points, matched against argv[1]'s basename when argv[0] is a
#: Python interpreter.
V1_SCRIPTS = frozenset({"led_listener.py"})

PROC_DIR = Path("/proc")


def conflicting_led_processes(proc_dir: Path = PROC_DIR) -> list[tuple[int, str]]:
    """Find running V1 processes that would fight us for the LED hardware.

    Only one process can drive the strip: ``rpi_ws281x`` claims a DMA channel
    and the PWM peripheral, and the SPI device is likewise exclusive. Two
    writers produce flicker, garbled colour, or a driver that simply fails to
    start — none of which look like "you forgot to stop the old service".

    Scans ``/proc`` rather than asking systemd, because unit names differ
    between installs and a process someone started by hand would be missed.

    Matching is **positional, not a substring search**. It inspects only
    argv[0] (the executable) and, for a Python interpreter, argv[1] (the
    script). A plain substring search over the whole command line reports every
    shell, editor, or grep that merely *mentions* the file — including the very
    command someone runs while diagnosing this, which is how the false positive
    was found.

    Returns:
        ``(pid, command)`` for each match. Empty when nothing conflicts.
    """
    found: list[tuple[int, str]] = []

    try:
        entries = list(proc_dir.iterdir())
    except OSError:
        return found

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            # The process exited between listing and reading, or we lack
            # permission. Neither is worth reporting.
            continue

        argv = [part for part in raw.decode("utf-8", errors="replace").split("\x00") if part]
        if _is_v1_process(argv):
            found.append((int(entry.name), " ".join(argv)))

    return found


def _is_v1_process(argv: list[str]) -> bool:
    """Whether this argv is a V1 process, judged by position."""
    if not argv:
        return False

    executable = PurePosixPath(argv[0]).name
    if executable in V1_BINARIES:
        return True

    # `python3 /path/to/led_listener.py`. Requiring the interpreter in argv[0]
    # is what stops `vim led_listener.py` from counting.
    if executable.startswith("python") and len(argv) > 1:
        return PurePosixPath(argv[1]).name in V1_SCRIPTS

    return False


def spi_devices(dev_dir: Path = DEV_DIR) -> list[Path]:
    """SPI character devices the kernel is exposing.

    Empty means SPI is off in ``/boot/firmware/config.txt``, so the SPI backend
    cannot possibly be driving the strip — a far stronger signal than merely
    finding the Python library installed.
    """
    try:
        return sorted(dev_dir.glob("spidev*"))
    except OSError:
        return []
