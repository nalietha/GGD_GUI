# Testing V2 on a Pi that is still running V1

V1 and V2 can coexist on the same Pi. Only one thing genuinely conflicts, and
you can test most of V2 without touching V1 at all.

## What conflicts, and what does not

| V1 service | Conflicts? | Why |
|---|---|---|
| `led_listener.service` | **Yes** | Holds the LED hardware. `rpi_ws281x` claims a DMA channel and the PWM peripheral; SPI is likewise exclusive. Two writers means flicker, wrong colours, or a driver that will not start. |
| `ggd_display.service` | No | Just the web app on `:5000`. V2's web layer will use `:8080`. |

So: **stage 1 needs nothing stopped**. Only stage 2 — driving real LEDs — needs
`led_listener` down.

V2 never writes to V1's files. `migrate-v1` reads `save.json` and
`appsettings.json` read-only and defaults to a dry run; V2's own state lives in
a separate database.

## What exists to test today

Phases 0–2 are done, so there is **no `livepulse run` yet** — the long-running
daemon arrives in Phase 3 with the web UI. What you can exercise now:

| Command | Needs hardware? | What it proves |
|---|---|---|
| `livepulse doctor` | No | Python version, config, data dir, layout, backends, V1 conflicts |
| `livepulse config` | No | Which config file won, and the merged result |
| `livepulse leds backends` | No | Which drivers can run on this Pi |
| `livepulse leds layout` | No | Node → pixel mapping against your wiring |
| `livepulse migrate-v1` | No | Your real V1 config imports cleanly |
| `livepulse leds test` | **Yes** | Power, data line, colour order |
| `livepulse leds identify` | **Yes** | Layout matches the physical stand |
| `livepulse leds demo` | **Yes** | The full pipeline on your real config |

The three hardware commands accept `LIVEPULSE_LEDS__BACKEND=sim`, which runs
the identical code path and prints what *would* have been sent — useful before
you stop anything.

---

## Setup

### 1. Check Python

```bash
python3 --version      # need 3.11 or newer
```

Raspberry Pi OS **Bookworm** ships 3.11 — fine. **Bullseye** ships 3.9, which
will not work. Either upgrade the OS or install a newer Python (`uv python
install 3.12` is the least invasive route).

### 2. Get the code and install

```bash
git clone <your-repo> ~/livepulse && cd ~/livepulse
git checkout develop

sudo apt install -y python3-venv python3-dev build-essential
python3 -m venv ~/lp-venv
~/lp-venv/bin/pip install -e '.[pwm]'      # existing GPIO18 wiring
# ~/lp-venv/bin/pip install -e '.[spi]'    # after rewiring to GPIO10
```

`python3-dev` and `build-essential` are needed because `rpi_ws281x` compiles a C
extension.

### 3. Keep test state out of the way

```bash
export LIVEPULSE_DATA_DIR=~/lp-data
```

Put that in your shell profile for the session. It keeps the test database in
your home directory rather than `/var/lib/livepulse`, so nothing needs root and
nothing is left behind.

---

## Stage 1 — everything except real light (V1 untouched)

V1 keeps running throughout this stage.

```bash
export LIVEPULSE_DATA_DIR=~/lp-data
export LIVEPULSE_LEDS__BACKEND=sim
alias lp=~/lp-venv/bin/livepulse

lp doctor
lp config
lp leds backends
lp leds layout
```

`doctor` will report the V1 conflict as a failure — that is correct and
expected at this stage, and is exactly what it is for:

```
[FAIL] no other process is driving the LEDs — V1 is still running:
       pid 431 (/usr/bin/python3 /home/piDev/.../led_listener.py)
       — stop it with: sudo systemctl stop led_listener.service
```

Check `leds layout` against your stand. Logical node 0 should be the leftmost
slot. If the mapping looks wrong, that is a config fix, not a code fix — see
`docs/hardware.md`.

### Import your real V1 config

```bash
lp migrate-v1 /var/www/ggd_display/wwwroot/data/save.json \
              --appsettings /var/www/ggd_display/appsettings.json
```

Dry run by default. It prints every channel, the node bindings, the settings it
carried over, and any warnings. Nothing is written and V1's files are not
touched. When it looks right:

```bash
lp migrate-v1 /var/www/ggd_display/wwwroot/data/save.json \
              --appsettings /var/www/ggd_display/appsettings.json --apply
```

Then watch the whole pipeline run against your real data, still simulated:

```bash
lp leds demo --seconds 5 --mode streamer --live IronMouse
```

---

## Stage 2 — real LEDs

Now V1's LED process has to go.

### Record the current state first, so this is reversible

```bash
systemctl list-units --type=service --all | grep -iE 'ggd|led'
systemctl is-enabled led_listener.service ggd_display.service
```

Write down what `is-enabled` says. Then:

```bash
sudo systemctl stop led_listener.service
```

`stop` only — not `disable`. A reboot brings V1 back exactly as it was, which
is the safety net you want while V2 is unproven.

```bash
unset LIVEPULSE_LEDS__BACKEND     # let auto-selection pick the real driver
lp doctor                          # the conflict check should now pass
```

### Drive the strip

`rpi_ws281x` needs root. `sudo` drops your environment, so pass it through:

```bash
sudo -E ~/lp-venv/bin/livepulse leds test --seconds 3
sudo -E ~/lp-venv/bin/livepulse leds test --color '#ff0000'
sudo -E ~/lp-venv/bin/livepulse leds identify --dwell 1
sudo -E ~/lp-venv/bin/livepulse leds demo --seconds 20 --mode streamer --live IronMouse
```

In order, those prove: power and data reach the strip; colour order is right
(red shows red, not green); the layout matches the physical stand; and the full
state → effect → compositor → driver pipeline works on your real config.

Every command blanks the strip on exit, including on Ctrl-C.

### Restoring V1

```bash
sudo systemctl start led_listener.service
```

Or just reboot. If you did disable anything, `sudo systemctl enable --now
led_listener.service` puts it back.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `doctor` reports a conflicting process | V1's LED service is still running. Stop it. |
| Lights flicker or show garbage | Two processes driving the strip — same cause. |
| `leds test` shows green for `#ff0000` | Wire colour order. Set `leds.color_order = "RGB"`. |
| `Permission denied` / `/dev/mem` | `rpi_ws281x` needs root. Use `sudo -E`. |
| `identify` runs backwards | Layout, not code. Flip `layout.start_at_last_row`. |
| `no LED hardware detected, running simulated` | `LIVEPULSE_LEDS__BACKEND=sim` still exported, or the driver library is not installed. |
| Only some nodes light | Check `layout.nodes` and `layout.leds_per_node` match the build. |

## When Phase 3 lands

Once `livepulse run` exists you will want a systemd unit alongside V1's rather
than instead of it, so you can switch between them by starting one or the
other. That, plus the quiet-hours schedule from §16 of the plan, is what makes
the cutover a decision rather than a commitment.
