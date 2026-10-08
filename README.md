# Nova

GoComet's governed AI platform for enterprise logistics: a prototype. It has a generic YAML workflow engine on Temporal, governed LangGraph agents, micro-apps for human steps, and a tenant-isolated data layer. Design docs are in [`docs/`](docs/README.md). The build plan and the definition of "done" are in [`docs/07-build-plan.md`](docs/07-build-plan.md).

**Status:** M0 (skeleton). You can log in through Keycloak, the Next.js BFF holds the session, FastAPI validates the token, and Postgres has RLS on every tenant table. Workflows arrive in M1

## Quickstart

Needs Docker (Compose v2), [uv](https://docs.astral.sh/uv/), Node 24 + pnpm 10. On Windows, Make comes from `winget install ezwinports.make`, or you can run the commands from the `Makefile` directly.

```sh
make up        # = docker compose -f infra/docker-compose.yml --profile core up -d --build
```

The first start takes 2 to 3 minutes while Keycloak imports the realm. Then:

| What | URL |
|---|---|
| Nova web | <http://localhost:3300> |
| API docs | <http://localhost:8100/api/docs> |
| Keycloak admin | <http://localhost:8180> (`admin` / `admin`) |
| Postgres | `localhost:5433` (`nova_owner` / `nova_owner`, db `nova`) |

Dev users (password `dev`) come from [`infra/keycloak/realm-nova.json`](infra/keycloak/realm-nova.json):

| User | Tenant | Role |
|---|---|---|
| `ops@acme` | Acme | ops_exec |
| `lead@acme` | Acme | ops_lead |
| `fin@acme` | Acme | finance |
| `ctrl@acme` | Acme | controller |
| `fde@acme` | Acme | process_designer |
| `audit@acme` | Acme | auditor |
| `admin@acme` | Acme | tenant_admin |
| `ops@bolt` | Bolt | ops_exec |
| `platform` | none | platform_admin |

Login is identity-first, because Keycloak Organizations are on: you enter the username, then the password.

## Develop

```sh
uv sync && pnpm install
make test       # unit: Python + web (no Docker)
make test-int   # testcontainers: Postgres RLS, Keycloak realm/token flows
make lint       # ruff, mypy --strict, import-linter, eslint, tsc
make ci         # everything CI runs
```

To run the API or web outside Docker against the running stack, copy `.env.example` to `.env`, then run `uv run uvicorn nova_api.main:app --port 8100` or `pnpm -C apps/web dev` (port 3300; stop the `web` container first).

**Windows / WSL2:** later profiles (`data`, `ai`) need memory. Give WSL at least 12 GB in `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=12GB
```

## Layout

`apps/web` (Next.js BFF + UI) · `services/api` (FastAPI, Alembic migrations, seed) · `packages/nova_core` (settings, db + tenancy, auth, logging, telemetry) · `packages/nova_dsl` (M1) · `infra/` (compose, Postgres init, Keycloak realm, OpenFGA model) · `tests/`. Rules for contributors are in [`CLAUDE.md`](CLAUDE.md).
