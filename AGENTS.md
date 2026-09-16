# lifesim Agent Entry Point

Status: Active
Repository: `MostDef2000/lifesim`

## Sources of truth

- Git `main` and tracked repository files are product truth.
- `AGENTS.md` and referenced contracts define project rules.
- `.specify/memory/constitution.md` defines the reusable SDD quality baseline.
- `specs/<feature>/{spec,plan,tasks}.md` contains active change intent and acceptance evidence.

## Delivery role

`profile-delivery-orchestrator` is the sole lifecycle owner. Specialist workers may explore, design, implement, test, or review bounded work orders, but they do not own scope, Git, GitHub, merge, release, or deployment.

## Operating rules

1. Resolve repository behavior and commands from current files; do not guess.
2. Present exact behavior, paths, exclusions, risks, and verification before the first write.
3. The exact phrase `commit approved` authorizes only that immediately preceding bounded scope.
4. Keep one task on one fresh branch and one bounded PR unless project contracts say otherwise.
5. Never commit secrets, `.env`, credentials, local runtime state, logs, caches, or generated output unless the repository explicitly tracks it.
6. Run focused tests during implementation and the repository's required lint, typecheck, test, and build gates before completion.
7. Significant changes require an independent review from a different model family.
8. Workers never commit, push, merge, create PRs, release, deploy, or invoke other workers.
9. LSP diagnostics are supporting evidence, not a replacement for real project commands.
10. Stop and request a new scope when behavior or required paths materially exceed approval.

## Sandbox

This project runs inside the Docker sandbox described by the global contract `~/.config/opencode/AGENTS.md`. Tool availability never grants permission — see `sandbox-guide` skill and `~/.config/opencode/docs/sandbox/*.md` for filesystem, permissions, MCP, and troubleshooting. `references.shared` (`/home/mostdef/vibe/shared/**`) is only readable when `external_directory` allows it.

## Project-specific rules

Add architecture, runtime contours, protected paths, test commands, release policy, and deployment authority here as the project evolves.
