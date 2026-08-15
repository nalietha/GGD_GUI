# Hardware & the PWM → SPI migration

LivePulse supports **both** LED backends. Nothing forces you to rewire, and
nothing rewires itself. This describes what the two backends are, how the
software decides between them, and how to move when you're ready.

## The two backends

| | `ws281x` (PWM) | `spi` |
|---|---|---|
| Data pin | **GPIO18** (pin 12) | **GPIO10 / SPI0 MOSI** (pin 19) |
| Needs root | Yes (`/dev/mem`) | No — `spi` group is enough |
| Raspberry Pi 5 | **Does not work** | Works |
| Pi 4 / 3 / Zero 2 W | Works | Works |
| Onboard audio | Conflicts (shares the PWM peripheral) | Untouched |
| Install | `pip install 'livepulse[pwm]'` | `pip install 'livepulse[spi]'` |
| Status | **Deprecated** — supported, not recommended | Recommended |

`rpi_ws281x` drives the PWM and DMA peripherals directly. On the Pi 5 those moved
behind the RP1 southbridge, and the library cannot reach them. This is not a bug
anyone can fix in LivePulse — it's why the migration exists.

## How `auto` chooses

`leds.backend = "auto"` (the default) prefers, in order:

1. **`ws281x`** — if the library is installed *and* the board is a Pi 4 or earlier
2. **`spi`** — if the CircuitPython libraries are installed *and* the kernel is
   exposing a `/dev/spidev*` device
3. **`sim`** — always available; the app starts and logs a warning

**PWM is preferred first on purpose, even though SPI is the better backend.**

Which pin your strip is soldered to is a physical fact with no software signal.
LivePulse cannot detect it, so it refuses to guess — and guessing wrong means
silent darkness, not an error message you could act on.

What it *can* observe is intent. `rpi_ws281x` is an optional extra, so its
presence means somebody deliberately installed the PWM backend — which is
exactly what a working GPIO18 stand looks like. Preferring it guarantees that
upgrading LivePulse never moves the pin out from under a stand that works.

The consequence: **a new build needs no configuration** (it only installs
`[spi]`, so SPI wins), **a Pi 5 needs no configuration** (the PWM probe fails),
and **an existing PWM build needs no configuration** (PWM keeps winning). Only
the deliberate act of rewiring needs a config change.

Check what would be selected:

```bash
livepulse leds backends
livepulse doctor
```

## Migrating an existing stand

Do this when it suits you. The old backend keeps working until you finish.

### 1. Enable SPI

```bash
sudo raspi-config     # Interface Options → SPI → Enable
sudo reboot
ls /dev/spidev*       # must list at least one device
```

Or add `dtparam=spi=on` to `/boot/firmware/config.txt` directly.

### 2. Move the data line

Resolder the strip's data wire from **GPIO18 (physical pin 12)** to
**GPIO10 (physical pin 19)**. Ground and power do not move.

If you use a level shifter, its input moves too — GPIO10 feeds the shifter,
the shifter still feeds the strip.

### 3. Grant SPI access and drop root

```bash
sudo usermod -aG spi livepulse
```

Then remove `User=root` from the systemd unit. Log out and back in, or reboot,
for the group change to take effect.

### 4. Switch the backend

```toml
# /etc/livepulse/config.toml
[leds]
backend = "spi"
```

Setting it explicitly is better than relying on `auto` here: it documents the
wiring in a file, and it makes a misconfiguration fail loudly at startup rather
than falling back to a backend that cannot drive your strip.

### 5. Verify

```bash
livepulse leds test                 # every pixel white — proves power and data
livepulse leds test --color '#ff0000'   # all red — proves colour order
livepulse leds identify             # one node at a time, announced
```

`leds identify` walks logical node 0 upward. Node 0 should light the **leftmost
slot** on the stand. If the sequence runs backwards or starts in the wrong row,
adjust the layout in config — never the code:

```toml
[layout]
start_at_last_row = true    # data line enters at the last row
first_row_reversed = false  # flips the direction of the first emitted row
```

If `leds test --color '#ff0000'` shows green, the wire order is wrong:

```toml
[leds]
color_order = "RGB"   # default is GRB, which most WS281x parts use
```

### 6. Remove the old extra (optional)

```bash
pip uninstall rpi_ws281x
```

With it gone, `auto` selects SPI on its own and the deprecation warning stops.

## Phase-out timeline

| Version | `ws281x` |
|---|---|
| 2.0 | Supported. Preferred by `auto` when installed. Logs a deprecation notice on startup. |
| 2.1+ | Supported. No longer preferred by `auto`; must be set explicitly. |
| 3.0 | Removed. |

Nothing about `ws281x` is broken deliberately along the way. It is retired only
because it cannot run on current hardware, and because SPI does the same job
without root.

## Wiring reference

```
Raspberry Pi (40-pin header)

  pin 2   5V ──────────────► level shifter VCC (not the strip; see below)
  pin 6   GND ─────────────► strip GND  +  PSU GND  (common ground is required)
  pin 12  GPIO18 ──────────► strip DIN   [ws281x — deprecated]
  pin 19  GPIO10 / MOSI ───► strip DIN   [spi — recommended]

  5V PSU  ─────────────────► strip 5V
```

Power the strip from its own 5V supply, not the Pi's header — 112 LEDs at full
white draw well over 6 A. The Pi's ground and the PSU's ground **must** be
joined, or the data signal has no reference.

The Pi's GPIO is 3.3 V and WS281x parts expect a 5 V logic level. Many strips
tolerate 3.3 V over short runs; if yours is unreliable, a 74AHCT125 level
shifter between the GPIO pin and the strip's data input fixes it.
