# Contributing to LivePulse

## Branching model

LivePulse uses a **stable trunk with an integration branch**, because V2 is a full rewrite
that will be incomplete for a while and `master` must keep describing something that runs.

| Branch | Purpose | Merges from | Protected |
|---|---|---|---|
| `master` | Released, working code. V1 today, V2 after the Phase 3 cutover. | `develop` (release PRs), `hotfix/*` | Yes |
| `develop` | V2 integration. Always builds, always green in CI. | `feat/*`, `fix/*`, `chore/*`, `docs/*` | Yes |
| `feat/<scope>/<slug>` | One feature. Short-lived — days, not weeks. | — | No |
| `fix/<scope>/<slug>` | Bug fix against `develop`. | — | No |
| `hotfix/<slug>` | Urgent fix branched from `master`, merged to **both** `master` and `develop`. | — | No |

Scope is the subsystem — `v2`, `leds`, `providers`, `web`, `storage`, `ci`.

```
master ──●───────────────────────────────●── v2.0.0
          \                             /
develop ───●────●────────●────●────────●
                 \      /      \      /
       feat/leds/renderer   feat/providers/twitch
```

**Rules**

1. Never commit directly to `master` or `develop`.
2. One branch, one concern. If a branch grows a second concern, split it.
3. Rebase onto `develop` before opening a PR; keep history linear.
4. A PR merges only when CI is green (`ruff`, `mypy --strict`, `pytest`).
5. Delete the branch after merge.

```bash
git switch develop && git pull
git switch -c feat/leds/renderer
# ... work ...
git fetch origin && git rebase origin/develop
git push -u origin feat/leds/renderer
```

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/). The type drives the changelog.

```
<type>(<scope>): <imperative summary, <=72 chars>

<body: why, not what — the diff already says what>

Refs: #<issue>
```

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`.
Breaking changes get a `!` after the scope and a `BREAKING CHANGE:` footer.

```
feat(providers): add Twitch EventSub WebSocket watcher

Polling burned an API call per channel per 30s and reacted up to 30s late.
EventSub pushes stream.online within ~1s over a single socket.

Refs: #61
```

## Design principles

These are enforced in review; PRs that violate them get sent back.

### SOLID

- **Single responsibility** — a module changes for one reason. `leds/layout.py` maps nodes to
  pixels; it does not talk to hardware, parse colour, or read config.
- **Open/closed** — adding a stream platform, an effect, or an LED backend means adding a class
  and registering it. It must not require editing the scheduler, the compositor, or the core.
  If you find yourself adding an `elif provider == "kick"`, the abstraction is wrong.
- **Liskov substitution** — every `LedDriver` is interchangeable. The shared contract test suite
  in `tests/contracts/` runs against *all* implementations, including `sim`.
- **Interface segregation** — small protocols. Poll-only providers implement `Provider`;
  only providers that can hold a connection implement `PushProvider`. Nobody is forced to
  stub out a method they cannot support.
- **Dependency inversion** — `core/` and `leds/` depend on protocols, never on concrete
  implementations. Concrete wiring happens in exactly one place: the composition root.

### DRY

One definition, one place. Specifically, the V1 mistakes we are not repeating:

- The node→pixel transform exists **once**, in `leds/layout.py`, derived from config.
  (V1 had `15 - x if x < 8 else x - 8` copy-pasted into three files in two languages.)
- Settings are read through `config/settings.py` only. No module opens a JSON file itself.
- Colour parsing and gamma live in `leds/color.py` only.
- Hardware constants come from config, never literals. There is no bare `16` in the codebase.

Duplication of *structure* is fine; duplication of *knowledge* is not.

## Testing

```bash
pip install -e '.[dev]'
pytest                       # all tests, no hardware needed
ruff check . && ruff format --check .
mypy
```

Every subsystem must be testable off-Pi. The `sim` LED backend and `manual` provider exist
so the entire application runs, and is tested, on a laptop with no network and no GPIO.
