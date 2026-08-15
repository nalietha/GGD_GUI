# LivePulse V2 — Analysis & Rewrite Plan

**Status:** Draft for review · **Date:** 2026-08-15 · **Target:** Full Python rewrite on Raspberry Pi

---

## 1. What V1 actually is

Four projects, two languages, one shared JSON file used as an IPC bus.

| Project | Language | Role | Verdict |
|---|---|---|---|
| `GGD_Display` | C# / ASP.NET Core 8 Razor Pages | Web UI, owns `save.json` + `appsettings.json`, runs its own Twitch poller, hosts a SignalR hub | Rewrite |
| `GDD_Twitch_API` | C# class library | Twitch Helix client (app-access token) | Rewrite as provider |
| `GGD_LightController` | Python + `rpi_ws281x` | Drives WS2811 LEDs, **also** polls Twitch independently | Rewrite (concepts survive) |
| `GGD_Flask` | Python | Untouched Visual Studio Flask template — zero project code | **Delete** |

### Data flow today

```
                 ┌─────────────────────────────┐
Twitch Helix ◄───┤ RefreshTwitchStatusService  │  (C#, 30s)  writes isLive →┐
                 └─────────────────────────────┘                            │
                 ┌─────────────────────────────┐                            ▼
Twitch Helix ◄───┤ led_listener.run_poll_loop  │  (Py, 10s)  reads ────► save.json
                 └──────────────┬──────────────┘                            ▲
                                ▼                                           │
                          WS2811 strip                Razor Pages UI ───────┘
```

Two independent processes poll the same Twitch API on different intervals and read/write the same file with no locking. There is no actual channel between the UI and the LEDs other than "notice the file changed on the next 10-second tick."

---

## 2. What is worth keeping

These are good ideas; V2 should carry them forward, just implemented properly.

- **The data model.** `privateId` (stable local UUID) decoupled from `publicStreamerId` (platform-assigned ID), with `platform` already a field on the streamer. That is exactly the right shape for multi-platform — V1 just never used it.
- **Per-node config**: primary colour, alt colour, display effect, linked streamer.
- **The strategy split** (`StreamerModeStrategy` vs `DisplayEffectStrategy`) — streamer-driven vs. standalone ambient. Keep as a mode concept.
- **Ambient fallback**: when nobody is live, run a gradient instead of going dark. Good UX instinct.
- **Go-live flash**: a transient animation on the offline→live edge, then settle to the streamer colour.
- **Physical layout mapping** (`15 - x if x < 8 else x - 8`) — the serpentine wiring is real, it just needs to be data instead of a magic expression repeated in three files.
- **Declarative platform plugins** (`plugins/adult_sites.json`) — the *shape* is right (platform + URL + needs-key). V2 generalises this into a real, working generic provider.

---

## 3. Concrete defects found

These matter mainly because they explain *why* it limps, and because V2 must not reproduce them.

### Settings never reach the LEDs at all

| # | Where | Problem |
|---|---|---|
| 1 | `led_listener.py:117` | Reads `GGD_Display.StreamerEnabled`. Commit `c4158bd` renamed the section to `GGDDisplay` and the key to `StreamerModeEnabled`. Lookup always misses → **streamer mode is permanently `True`** regardless of the UI toggle. |
| 2 | `led_listener.py:160` | `config.get("Mode.StreamerEnabled")` — there is no `Mode` section in `appsettings.json`. Always returns the default. |
| 3 | `led_utilities.py:13`, `config_manager.py` | The Python side opens `appsettings.json` by **relative** path (cwd = `/home/piDev/GamerDisplayWithNet` per its systemd unit), while the web app writes `appsettings.json` under `/var/www/ggd_display`. They are two different files. `save.json` is hardcoded absolute to the web app's copy — so node data crosses, settings never do. |

### Persistence corruption

| # | Where | Problem |
|---|---|---|
| 4 | `FileController.cs:96` `SaveAppSettings` | Re-serialises the whole file with `PropertyNamingPolicy = CamelCase`. The `GGDDisplay` POCO gets written as `streamerModeEnabled` / `brightness`. `LoadAppSettings` deserialises case-sensitively → after the first save, **every setting reads back as its default** and the next save writes those defaults over the user's values. |
| 5 | `Settings.cshtml.cs:40,43` | `TryGetValue(...) && x.ValueKind == True \|\| x.ValueKind == False` — precedence bug. When the key is absent (see #4), the branch silently skips, the bound property stays `false`, and `OnPost` writes `false` back. Toggles reset themselves. |
| 6 | `GGD_Display.csproj:23` | `save.json` is `<CopyToOutputDirectory>Always</CopyToOutputDirectory>`. **A redeploy overwrites the user's live configuration** with the repo copy. (The two copies in the tree already differ.) |
| 7 | `FileController` | `File.WriteAllText` with no lock, no temp-file-and-rename, no schema version check, concurrent with a Python reader. Torn reads and lost writes are possible. |

### Logic bugs

| # | Where | Problem |
|---|---|---|
| 8 | `RefreshTwitchStatusService.cs:65-68` | Assigns `streamer.IsLive = isLive` *before* testing `streamer.IsLive != isLive`, which is then always false. SignalR pushes fire **only on the first run**, never on an actual transition. |
| 9 | `led_strategies.py:68-69` | `DisplayEffectStrategy` reads `node["ColorHex"]` / `node["DisplaySetting"]`, but `load_assignments` produces keys `color` / `streamerColor` / `nodeId` and never loads `DisplaySetting` at all. **Non-streamer mode is dead code** — always black, always "static". |
| 10 | `led_listener.py:35-46` | Nodes with an empty or unresolved `LinkedStreamerId` are skipped entirely — never driven, never cleared, they hold whatever they last showed. |
| 11 | `led_effects.py:149` | `startup_chase_effect` calls `Thread(...)` — `threading` is never imported. `NameError` if ever enabled (currently commented out at the call site). |
| 12 | `TwitchAPI/refresh_streamer_status.py:2` | Imports `save_manager`, which does not exist. Dead file. |
| 13 | `AddStreamers.cshtml.cs:130` | `ExtractUsernameAndPlatform` (which does understand multiple platforms) is unused by the live handler. Everything added is hardcoded `Platform = "Twitch"`. |
| 14 | `EditStreamers.cshtml.cs:57` | Removing a streamer resets the linked node's `ColorHex` to `#000000`, destroying the user's colour choice. |

### Structural

| # | Problem |
|---|---|
| 15 | **Blocking effects inside the poll loop.** `pulse_node` sleeps ~6 s, `flash_node` ~3 s. Sixteen nodes going live at once blocks the loop for over a minute. There is no frame loop — each effect owns the CPU and calls `strip.show()` itself. |
| 16 | **No gamma correction.** Raw 8-bit values to WS2811 make mid-tones look washed and colour mixing wrong. |
| 17 | **Hardcoded hardware.** `LED_COUNT = 112`, `LEDS_PER_NODE = 7`, `LED_PIN = 18`, and the literal `16` in at least six places across both languages. |
| 18 | **`rpi_ws281x` does not work on Raspberry Pi 5** — the RP1 southbridge moved the PWM/DMA peripherals. V1 is structurally locked out of current hardware. |
| 19 | **Secrets in `appsettings.json`** — the Twitch client secret sits in the same file the UI rewrites and the repo tracks. Git history contains a commit titled "Added Twitch tokens". |
| 20 | `Program.cs:9` binds `http://localhost:5000` — **the UI is not reachable from the LAN**, only from the Pi itself. `AddRazorPages()` is also called twice, and `UseHttpsRedirection()` runs with no HTTPS port bound. |
| 21 | Both systemd units run as `User=root`. |
| 22 | SignalR is wired up server-side but commented out client-side; `Program.cs:31` has a TODO to remove it. Live updates in the browser do not work — the UI is reload-driven. |
| 23 | Bootstrap **and** jQuery **and** jQuery-validate are vendored (~120 files) while the Razor markup is written in Tailwind class names. Neither framework is coherently used. |
| 24 | No tests, no CI, no pinned dependencies (`requirements.txt` is one line), `print()` for logging, no error surface in the UI. |
| 25 | "Mode" means three incompatible things: `"static"` in appsettings, `"on"/"off"` from `OnPostSetMode`, `"streamer"/"gradient"` in `led_listener.current_mode`. |
| 26 | The WiFi settings page is a non-functional mock — it `alert()`s the credentials and does nothing. |

---

## 4. V2 goals

**Primary**
1. **One Python application**, one process tree, one source of truth. No cross-language duplication.
2. **Platform-agnostic ingestion.** Twitch becomes one provider among many. Adding a service means adding a small class or a config file — not touching the core.
3. **Both ingestion modes**: efficient batched *polling* for services that only offer REST, and persistent *push/subscribe connections* for services that offer them (Twitch EventSub WebSocket first).
4. **Runs on any Pi**, including Pi 5 and Zero 2 W. The .NET/ARM constraint disappears; the LED driver constraint must not replace it.

**Secondary**
5. Non-blocking frame-based renderer so effects compose and never stall ingestion.
6. Real persistence (SQLite) with atomic writes and migrations, plus a clean import from V1 `save.json`.
7. Live-updating web UI (SSE) with a documented REST API.
8. Testable off-Pi: simulated LED backend and fake providers, so the whole thing runs on a laptop.

**Non-goals for V2.0**
- Cloud/multi-tenant anything. Single device, LAN-local.
- Rewriting the physical hardware design.
- Authentication on the web UI beyond an optional shared PIN (it lives on a home LAN).
- The hall-effect / magnetic node sensing (kept in the plan as a V2.x extension point, not built).

---

## 5. V2 architecture

### Process model

A single `livepulse` process:

- **asyncio event loop** — web server, provider pollers, push-connection watchers, event bus.
- **One dedicated render thread** — fixed ~60 fps, reads an immutable state snapshot, writes to the LED driver. Isolated because `rpi_ws281x` / SPI writes are blocking C calls and must not be at the mercy of loop scheduling.
- Communication between them: a lock-free "latest snapshot" swap plus a small transient-animation queue. The render thread never blocks the loop; the loop never touches the strip.

```
┌──────────────────── livepulse (asyncio) ─────────────────────┐
│                                                               │
│  Providers ──┐                                                │
│  twitch/ws   ├─► IngestScheduler ──► EventBus ──► StateStore  │
│  youtube     │      (poll + push)        │            │       │
│  kick        │                           │            │       │
│  generic     │                           ▼            ▼       │
│  webhook  ◄──┼──────────────────  FastAPI + SSE   SQLite(WAL) │
│              │                           ▲                    │
└──────────────┼───────────────────────────┼────────────────────┘
               │                    snapshot│
               ▼                           ▼
        (render thread)  Compositor ──► LedDriver ──► strip
```

### Package layout

```
livepulse/
├── __main__.py              # python -m livepulse
├── cli.py                   # run | migrate-v1 | leds test | provider test | doctor
├── core/
│   ├── models.py            # Channel, Node, Binding, LiveState, Effect (pydantic)
│   ├── bus.py               # async pub/sub event bus
│   ├── state.py             # in-memory authoritative state + snapshot()
│   └── errors.py
├── storage/
│   ├── db.py                # aiosqlite, WAL, connection mgmt
│   ├── migrations/          # numbered SQL migrations
│   ├── repo.py              # typed repositories
│   └── import_v1.py         # save.json + appsettings.json → SQLite
├── providers/
│   ├── base.py              # Provider protocol + Capabilities
│   ├── registry.py          # entry-point + directory discovery
│   ├── twitch/
│   │   ├── auth.py          # app token + device-code user token, refresh
│   │   ├── poll.py          # helix/streams, batched 100/req
│   │   └── eventsub.py      # WebSocket stream.online/offline
│   ├── youtube.py
│   ├── kick.py
│   ├── owncast.py
│   ├── generic_http.py      # declarative, JMESPath-driven
│   ├── webhook.py           # inbound push endpoint
│   └── manual.py            # test/mock provider
├── ingest/
│   ├── scheduler.py         # adaptive interval, jitter, batching
│   ├── ratelimit.py         # token bucket per provider
│   └── supervisor.py        # push-connection lifecycle + backoff
├── leds/
│   ├── driver/
│   │   ├── base.py          # LedDriver protocol
│   │   ├── ws281x.py        # PWM, Pi ≤4, needs root
│   │   ├── spi.py           # SPI/MOSI, no root, works on Pi 5
│   │   └── sim.py           # in-memory, feeds the web preview
│   ├── layout.py            # node→pixel mapping from a layout descriptor
│   ├── color.py             # gamma LUT, colour-space helpers
│   ├── renderer.py          # frame thread
│   ├── compositor.py        # base layer + transient layer + master brightness
│   └── effects/             # static, pulse, breathe, flash, rainbow, sparkle, gradient
├── web/
│   ├── app.py               # FastAPI
│   ├── api/v1/              # REST
│   ├── routes/              # HTML pages
│   ├── events.py            # SSE stream
│   ├── templates/           # Jinja2 + htmx
│   └── static/              # prebuilt CSS, no node toolchain on the Pi
└── config/
    ├── settings.py          # pydantic-settings, layered
    └── secrets.py           # separate 0600 store, never rendered to the UI
```

---

## 6. The provider abstraction (headline feature)

### The contract

```python
class Capabilities(NamedTuple):
    supports_push: bool  # can hold a subscription/socket
    supports_batch: bool  # can query many channels per request
    max_batch: int
    needs_auth: AuthKind  # NONE | API_KEY | OAUTH_APP | OAUTH_USER
    min_poll_interval: float  # provider-enforced floor, seconds


class Provider(Protocol):
    id: str
    caps: Capabilities

    async def resolve(self, handle: str) -> ChannelRef:
        """'twitch.tv/ironmouse' or 'ironmouse' → stable platform ID + canonical URL."""

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        """Batched. Called by the scheduler; must respect max_batch."""

    async def watch(self, refs: Sequence[ChannelRef], emit: EmitFn) -> NoReturn:
        """Optional. Long-lived; emits LiveState on change. Supervisor restarts on failure."""
```

`LiveState` carries more than a bool — `is_live`, `title`, `category`, `viewers`, `started_at`, `thumbnail_url`, `observed_at`, `source` (`poll` | `push` | `manual`). Effects can then key off viewer count or category, not just on/off.

### Ingestion strategy per provider

| Provider | Mode | Notes |
|---|---|---|
| **Twitch** | **Push** (EventSub WebSocket) + poll fallback | `stream.online` / `stream.offline` over `wss://eventsub.wss.twitch.tv/ws`. Near-instant, no polling cost. ⚠️ EventSub-over-WebSocket requires a **user** access token, not an app token — needs a one-time OAuth device-code flow and stored refresh token. The existing app-token poller stays as the fallback path and for `resolve()`. |
| **YouTube** | Poll | Data API v3, 10 000 units/day. `search.list` costs 100 units per call — **must not** be used for polling. Resolve channel once, then poll cheaply. WebSub push needs a public callback URL, which a home NAT usually blocks; leave it as an opt-in for users with a reachable host. |
| **Kick** | Poll | Public JSON endpoint, unofficial and Cloudflare-fronted. Best-effort, isolated so its breakage cannot take down the app. |
| **Owncast** | Poll | `GET /api/status` returns `{"online": bool}`. Trivial, self-hosted, an excellent integration test target. |
| **Generic HTTP** | Poll | **Fully declarative.** A TOML/JSON descriptor supplies URL template, headers, auth style, and a JMESPath expression yielding the live boolean. Adding a service becomes a config file, not code. This replaces `plugins/adult_sites.json` with something that actually works, and keeps any given site's specifics out of the repo. |
| **Inbound webhook** | Push | `POST /api/v1/hooks/{token}` with a per-channel secret. Covers OBS scripts, Streamlabs, Zapier, home automation, anything that can make an HTTP request. |
| **HLS/RTMP probe** | Poll | Does the `.m3u8` return 200 with segments? Catches self-hosted and long-tail services with no API. |
| **Manual** | — | UI toggle. Also the test double. |

### Scheduler

- Groups channels by provider, batches to `max_batch`, one request per batch.
- **Adaptive interval**: 30 s base; back off toward 5 min for channels that have been offline for hours; tighten to 15 s around a channel's historical go-live window.
- Per-provider token-bucket rate limiter; honours `Retry-After` and `Ratelimit-Remaining`.
- Jitter on every interval so N channels never stampede.
- A provider that starts failing is circuit-broken and surfaced in the UI as a degraded badge, not a silent stall.
- **Push wins**: if a channel is covered by an active `watch()`, its poll interval drops to a slow reconciliation tick (10 min) purely as a correctness backstop.

---

## 7. LED subsystem

### Driver backends (decide this early — it's a wiring decision)

| Backend | Pin | Root? | Pi 5? | Recommendation |
|---|---|---|---|---|
| `rpi_ws281x` (PWM) | GPIO18 | Yes | **No** | Keep for backward compat with existing builds |
| SPI (`neopixel_spi` / Blinka) | GPIO10 (MOSI) | No (`spi` group) | Yes | **Default for new builds** |
| `sim` | — | No | — | Dev + CI + web preview |

Moving to SPI means one wire moves from GPIO18 to GPIO10, and the process stops needing root. Given that Pi 5 cannot run `rpi_ws281x` at all, doing this now avoids a forced migration later. The driver protocol is ~4 methods (`set_pixels`, `show`, `set_brightness`, `close`), so supporting all three is cheap.

### Layout as data

```toml
[layout]
nodes = 16
leds_per_node = 7
order = "serpentine"     # or "linear", or an explicit index list
reverse_first_half = true
```

`transform_node_id` disappears from three files and becomes a lookup table built once at startup. Changing to 24 nodes or a different wiring becomes a config edit.

### Frame-based rendering

Effects stop owning the CPU. Each becomes a pure-ish function of time:

```python
class Effect(Protocol):
    def render(self, ctx: NodeCtx, t: float) -> RGB: ...
```

The compositor per frame:
1. Base layer — each node's assigned effect at the current time.
2. Transient layer — one-shot animations (go-live flash) with their own lifetime and alpha, popped when finished.
3. Ambient override — when no channel is live, the gradient takes the base layer.
4. Master fade — on/off is a smooth 300 ms ramp, not an abrupt blackout.
5. Gamma LUT + global brightness, then a single `show()`.

Result: sixteen simultaneous go-live flashes cost nothing, and ingestion is never blocked.

---

## 8. Persistence & configuration

**SQLite (WAL) via `aiosqlite`** replaces `save.json`. Numbered migrations, schema version row, foreign keys on.

```
channels(id TEXT PK, provider TEXT, platform_ref TEXT, handle TEXT,
         display_name TEXT, url TEXT, color TEXT, alt_color TEXT,
         created_at, UNIQUE(provider, platform_ref))
nodes(id INTEGER PK, effect TEXT, color TEXT, params JSON)
bindings(node_id → nodes.id, channel_id → channels.id)   -- many nodes per channel
live_state(channel_id PK, is_live, title, category, viewers, started_at, observed_at, source)
settings(key TEXT PK, value JSON)
provider_accounts(provider TEXT PK, token_ref TEXT, expires_at)
events(id, ts, channel_id, kind, payload)                -- for the "recently live" timeline
```

**Config layering** (`pydantic-settings`): built-in defaults → `/etc/livepulse/config.toml` → `LIVEPULSE_*` env vars → runtime settings in the `settings` table (what the UI edits). Changes publish on the event bus; nothing watches files, nothing re-reads JSON per tick.

**Secrets** live in `/etc/livepulse/secrets.toml` (mode `0600`, gitignored) or the environment. The API never returns them — only a `configured: true/false` flag. This closes the V1 hole where the UI rewrote the file holding the Twitch client secret.

---

## 9. Web UI & API

**FastAPI + Jinja2 + htmx**, server-rendered, with **SSE** at `/api/v1/events` for live updates. That replaces the dead SignalR path with roughly twenty lines and no client library. Bootstrap, jQuery, and jQuery-validate all go; a single prebuilt CSS file ships in the repo so the Pi never needs a Node toolchain.

Pages: **Dashboard** (16-node grid, live dots, colour pickers, drag-to-assign), **Channels** (add by URL/handle with provider auto-detect, per-channel colours), **Effects**, **Settings**, **Providers** (per-provider auth state, health, quota, last error), **System** (WiFi, updates, logs).

REST under `/api/v1/`: `nodes`, `channels`, `bindings`, `settings`, `providers`, `effects`, `preview` (returns the current frame so the browser grid mirrors the physical strip), `hooks/{token}`.

Binds `0.0.0.0:8080` by default with mDNS (`livepulse.local`) via Avahi, so it is actually reachable from the LAN — unlike V1.

**WiFi provisioning** becomes real: `nmcli` through NetworkManager, with a hotspot fallback if no known network is found within 45 s of boot. This is the one V1 page that was pure mock.

---

## 10. Deployment & operations

- **Install**: venv at `/opt/livepulse` + a single systemd unit `livepulse.service`. Dependencies pinned in `requirements.lock`; `pyproject.toml` with optional extras (`[pi]`, `[dev]`).
- **User**: dedicated `livepulse` user in the `spi` and `gpio` groups. No root (with the SPI backend).
- **Hardening**: `ProtectSystem=strict`, `ReadWritePaths=/var/lib/livepulse`, `NoNewPrivileges`.
- **Logging**: `structlog` → journald, plus a ring buffer exposed in the UI's System page.
- **Health**: `/healthz` and `livepulse doctor` (checks SPI enabled, permissions, DB writable, provider auth, driver reachable).
- **CLI**: `livepulse run | migrate-v1 <path> | leds test | leds identify <n> | provider test <id> <handle> | doctor`.

---

## 11. Testing

- `pytest` + `pytest-asyncio`. Target ≥80 % on `core`, `providers`, `leds`.
- `SimulatedStrip` backend → **golden-frame tests** for every effect (render N frames, compare to a stored fixture).
- `respx` for provider HTTP; recorded fixtures for each real API's response shape.
- A `FakeProvider` that can be scripted to go live/offline on cue, driving full end-to-end state→pixel tests with no hardware and no network.
- GitHub Actions on push: lint (`ruff`), types (`mypy`), tests. Pure-Python, so no ARM runner needed.

---

## 12. Migration from V1

`livepulse migrate-v1 /path/to/save.json --appsettings /path/to/appsettings.json`

- Preserves `privateId` as `channels.id` so existing node bindings survive.
- `publicStreamerId` → `platform_ref`, `platform: "Twitch"` → `provider: "twitch"`.
- Colours, alt colours, node links, brightness, and mode flags carry over.
- Normalises the three conflicting meanings of "mode" (§3, #25) into one explicit enum: `off | ambient | streamer`.
- Nodes with an empty `LinkedStreamerId` become explicitly unbound and render the ambient layer, instead of being silently skipped (#10).
- Dry-run by default; prints a diff and requires `--apply`.

---

## 13. Phased roadmap

Each phase ends with something runnable.

| Phase | Scope | Outcome |
|---|---|---|
| **0 — Skeleton** | Repo layout, `pyproject.toml`, CI, config loader, event bus, models, SQLite + migrations, `sim` LED backend, `livepulse doctor` | `python -m livepulse` runs on a laptop with a simulated strip |
| **1 — Render core** | Driver protocol + `ws281x` + `spi`, layout descriptor, gamma, frame thread, compositor, six effects, golden-frame tests | Real lights, real animations, no network |
| **2 — Providers (poll)** | Provider protocol, registry, scheduler with batching/jitter/rate limits, `twitch` poller, `manual`, `owncast` | Feature parity with V1's working path, correctly |
| **3 — Web** | FastAPI, dashboard, channels, settings, SSE, REST API, `migrate-v1` | V1 replaceable end-to-end; **cut over here** |
| **4 — Push** | Twitch EventSub WebSocket + device-code auth, connection supervisor, inbound webhook provider | Sub-second go-live reaction |
| **5 — Breadth** | `generic_http` declarative provider, YouTube, Kick, HLS probe, provider health UI | The actual decoupling-from-Twitch goal, delivered |
| **6 — Polish** | WiFi provisioning, hotspot fallback, mDNS, systemd hardening, install script, docs, `leds identify` | Shippable to someone who isn't you |

Phases 0–3 are the rewrite. Phases 4–6 are the new capability.

---

## 14. Decisions needed before Phase 0

1. **LED wiring** — commit to SPI (GPIO18 → GPIO10, no root, Pi 5 works) or keep PWM (no rewiring, but Pi 4 is the ceiling forever)? Recommendation: **support both, default SPI for new builds.**
2. **Twitch push auth** — the EventSub WebSocket path needs a one-time user OAuth (device-code flow, stored refresh token). Acceptable, or should V2.0 ship poll-only and defer push to V2.1?
3. **Which platforms are actually wanted** beyond Twitch, in priority order? The generic declarative provider covers the long tail, but the first-class ones are worth naming now.
4. **Node count** — stay at 16, or make the second stand a first-class multi-strip config?
5. **UI access** — open on the LAN, or gate behind a PIN?
6. **Adult-platform checks** — V1's `plugins/adult_sites.json` never worked (the endpoints listed aren't public status APIs). V2 can serve this purely through the user-supplied generic HTTP provider, keeping no site-specific code in the repo. Confirm that's the direction.

## 15. Known risks

- **Unofficial APIs** (Kick and similar) will break without notice. Mitigation: circuit breakers, per-provider isolation, declarative descriptors the user can fix without a release.
- **YouTube quota** — 10 000 units/day is easy to blow with the wrong endpoint. Budget it explicitly, surface remaining quota in the UI.
- **Push behind NAT** — WebSub-style callbacks need a reachable URL. Twitch EventSub-over-WebSocket does not, which is why it's first.
- **Pi Zero 2 W** — 512 MB RAM. Keep the frame loop allocation-free and avoid pulling in heavy deps (no pandas, no numpy unless it earns its place; a 112-pixel buffer does not need it).
- **Scope creep** — the tempting features (hall-effect sensing, multi-stand, audio reactivity) are all V2.x. Phase 3 is the cutover; resist adding to it.
