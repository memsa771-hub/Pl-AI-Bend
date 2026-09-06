# Placement AI (PAI) — Developer Handoff

Short guide for engineers joining the project.

## What this repo is

A **standalone FastAPI backend** for Placement AI:

- Auth (Supabase behind `AuthProvider`)
- Person Vault + typed profile
- Chat counselor (persistent per Person)
- Vault Intelligence (chat/document extraction)
- Documents, tasks, semantic memory, goals

```
┌─────────────┐     HTTPS      ┌──────────────────┐     HTTPS      ┌─────────────┐
│   Frontend  │ ────────────► │  PAI Backend     │ ────────────► │ Supabase    │
│  (any app)  │  /api/v1/*    │  (this repo)     │  Auth + DB    │ + DeepSeek  │
└─────────────┘               └──────────────────┘               └─────────────┘
```

## Repository layout

```text
src/pai/
  app.py                 # FastAPI factory, lifespan, middleware, worker start
  main.py                # ASGI entry (`pai.main:app`)
  config.py              # Settings from .env
  interfaces/            # HTTP API, background worker loops
    api/                 # Routes + FastAPI deps, OpenAPI, HTTP schemas
    workers/             # Start/consume loops only
  workflows/             # Cross-domain processes (onboarding today)
  kernel/                # Shared errors, contracts, evidence, policy, write gates
  intelligences/         # Reasoning: counselor, vault, documents, goals, research, planner
  domains/               # Persistent truth: student, conversations, documents, goals, …
  capabilities/          # Generic actions (search today)
  integrations/          # External providers (Tavily search, connectors)
  platform/              # LLM, database, jobs, storage, auth
tests/
```

Do not add code under old trees (`api/`, `services/`, `orchestration/`, `tools/`, `llm/`, `data/`, `ingestion/`, `auth/`, `core/`). Those folders were removed.

Mental model:

```text
Interface receives
  → Workflow coordinates when needed
  → Intelligence thinks
  → Kernel validates (write gates)
  → Domain owns truth
  → Capability acts
  → Integration connects outside
  → Platform powers everything
```

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
python -m uvicorn pai.app:create_app_from_env --factory --reload --host 127.0.0.1 --port 8000
```

Swagger: `http://127.0.0.1:8000/docs`

Authorize with **accessToken only** (no `Bearer` prefix in the Swagger box).

## Chat model (important)

- After first verified login, choose **Complete Onboarding** or **Upload My CV**
- Onboarding writes into the same Person Vault (not a separate profile)
- PAI is **one counselor per Person**
- There is **one chat transcript**, not a ChatGPT sidebar
- `GET /api/v1/chat/messages` loads history (paginated)
- `POST /api/v1/chat` and `POST /api/v1/chat/stream` return as soon as the counselor finishes; Vault/goal intelligence is queued in the background (`intelligencePending`)
- Canonical current goal lives in `domains/goals`. Journey only records goal created/changed/paused/completed events.
- Onboarding is a workflow (`workflows/onboarding`), not a domain. The HTTP route stays at `interfaces/api/onboarding.py`.

## Key modules

| Concern | Path |
|---------|------|
| Chat entry | `pai/interfaces/api/chat.py` |
| Auth deps / JWT / onboarding gate | `pai/interfaces/api/dependencies.py` |
| Onboarding process | `pai/workflows/onboarding/` |
| Turn graph | `pai/intelligences/counselor/` |
| Vault learn | `pai/intelligences/vault/` (sources: chat, document, …) |
| Document ingest | `pai/intelligences/documents/ingest.py` |
| Document persistence | `pai/domains/documents/` |
| Goal records | `pai/domains/goals/` |
| Goal reasoning | `pai/intelligences/goals/` |
| Live web facts | `pai/intelligences/research/` → `pai/capabilities/search/` |
| Vault writes | `pai/kernel/gates/`, `pai/kernel/evidence/vault_apply.py`, `pai/domains/student/typed_apply.py` |
| Catalog | `pai/domains/student/vault/catalog.py` |

## Docker

```bash
docker build -t pai-backend .
docker run --env-file .env -p 8000:8000 pai-backend
```

## Tests

```bash
python -m pytest tests/ -q
```
