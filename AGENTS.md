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

General delivery gates follow the global contract `~/.config/opencode/AGENTS.md` (durable source: profiles/global/** in the workspace repo ai-playground); scope admission phrase: `commit approved`.

1. Resolve repository behavior and commands from current files; do not guess.
2. Keep one task on one fresh branch and one bounded PR unless project contracts say otherwise.
3. Never commit secrets, `.env`, credentials, local runtime state, logs, caches, or generated output unless the repository explicitly tracks it.
4. Run focused tests during implementation and the repository's required lint, typecheck, test, and build gates before completion.
5. Workers never commit, push, merge, create PRs, release, deploy, or invoke other workers.
6. LSP diagnostics are supporting evidence, not a replacement for real project commands.

## Sandbox

This project runs inside the Docker sandbox described by the global contract `~/.config/opencode/AGENTS.md`. Tool availability never grants permission — see `sandbox-guide` skill and `~/.config/opencode/docs/sandbox/*.md` for filesystem, permissions, MCP, and troubleshooting. The single shared dir for all projects is /home/mostdef/vibe/projects/ai-playground/shared (inside the workspace, always readable).

## Project-specific rules

Add architecture, runtime contours, protected paths, test commands, release policy, and deployment authority here as the project evolves.

## Deploy contract

- Lane A (routine app deploys): dispatch of `.github/workflows/deploy.yml` (SSH forced command on the VPS) per the DEPLOY-TRIGGER protocol after explicit owner confirmation in chat — full 40-hex SHA from origin/main, green CI, acceptance probes, rollback.
- Transport (T2): minimal privileged launcher `/usr/local/sbin/lifesim-deploy-wrapper` (no deploy logic) + versioned unprivileged deploy-engine `deploy/deploy-engine` (all deploy logic, auto-updates itself via every lane-A deploy, fails closed without the launcher's lock fd). Verbs unchanged: `lifesim-deploy-v1 <sha>`, `lifesim-status-v1` (`== transport ==` block = launcher/engine sha256 + interface for repo↔host drift check). Transport internals и ЛЮБЫЕ изменения транспорта — только lane B: `deploy/README.md` §9.1 (включая version-bump cutover `-v1`→`-v2`: engine bump в repo PR → launcher Lane B → смена verb'а в workflow, одно окно; drift-check — `engine_interface`/`launcher_interface` в `lifesim-status-v1`).
- The unified deploy contract is documented in the workspace repo: `specs/deploy-unification/`.
- Infra/stateful changes (systemd, nginx, migrations, secrets) — sysadmin lane B.
