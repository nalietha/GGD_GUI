<!-- Title must follow Conventional Commits: type(scope): summary -->

## What and why

<!-- The diff says what changed. Say why it needed to. -->

Refs: #

## Checklist

- [ ] Branch is `<type>/<scope>/<slug>` and targets `develop` (or `master` for a hotfix)
- [ ] One concern only
- [ ] Rebased onto the target branch
- [ ] `ruff check . && ruff format --check . && mypy && pytest` all pass locally
- [ ] New behaviour has tests; new subsystems have a contract test
- [ ] No new hardware constant, colour parse, or node→pixel transform outside its owning module
- [ ] Adding a provider / effect / LED backend required **no** edits to core dispatch code

## Hardware impact

<!-- Delete if none. Note wiring changes, GPIO pin moves, or Pi model requirements. -->
