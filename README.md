# Placement AI Backend

FastAPI backend for student counseling, authentication, structured profiles,
semantic memory, goal analysis, and document processing.

## Requirements

- Python 3.12 and uv
- PostgreSQL with pgvector, plus a configured Supabase project
- OpenAI API credentials; Tavily credentials for live web research

## Local development

```powershell
uv sync --locked
Copy-Item .env.example .env
# Fill in your credentials and database connection in .env.
py run.py
```

The launcher uses `.venv`, applies migrations, starts the API and three background
workers, and opens Swagger at http://127.0.0.1:8000/docs. Ctrl+C stops all services.
Use `py run.py --skip-migrations` when the schema is already current, or
`--no-browser` to leave browser navigation manual. Do not replace an existing
configured `.env` with the example.

## FastAPI Cloud

The repository uses an installable `src` layout. The entry point is already set
in `pyproject.toml`:

```toml
[tool.fastapi]
entrypoint = "pai.main:app"
```

1. Connect this GitHub repository and select the `main` branch and repository root.
2. Add environment variables in the FastAPI Cloud dashboard. Use `.env.example`
   as a reference; real credentials are deliberately absent from this repository.
3. Set the required Supabase variables, `DATABASE_URL`, `VAULT_ENCRYPTION_KEY`,
   `EMAIL_VERIFICATION_REDIRECT_URL`, and `PASSWORD_RESET_REDIRECT_URL`.
   Use a `postgresql+asyncpg://` database URL. Redirect URLs must point to dedicated
   frontend callback paths, and their origins must appear in `CORS_ORIGINS`.
4. Set `OPENAI_API_KEY` and, for live research, `TAVILY_API_KEY` as secrets.
5. Set `APP_ENV=production`, `ENABLE_API_DOCS=true`, and `COOKIE_SECURE=true`.
   Configure `TRUSTED_HOSTS` for the deployed hostname, or use `*` for tester previews.
6. Apply `uv run alembic upgrade head` against the target database before serving
   requests. The cloud application entry point does not run migrations automatically.
7. Deploy, then open `https://<your-app-host>/docs`.

The API requires workers to process queued profile, goal, and document updates.
For a single-service tester preview, set `RUN_WORKERS_IN_API=true` and keep all
three `ENABLE_*_WORKER` flags true. Those loops share the API process, and queued
jobs wait whenever the service is stopped or scaled down. For sustained use and
isolated chat latency, keep `RUN_WORKERS_IN_API=false` and run the dedicated worker
commands below on persistent worker infrastructure using the same database.

`ENABLE_API_DOCS=false` disables Swagger, ReDoc, and the OpenAPI endpoint when
public API documentation is no longer needed. API authentication is enforced
independently of documentation visibility.

See the official [existing-project deployment guide](https://fastapicloud.com/docs/getting-started/existing-project/)
and [environment variable guide](https://fastapicloud.com/docs/builds-and-deployments/environment-variables/).

## Swagger testing

1. Register and verify an account, or call `POST /api/v1/auth/login` with an existing one.
2. Copy `data.accessToken`, click **Authorize**, and paste the token without `Bearer`.
3. Complete onboarding using `/api/v1/onboarding` if needed.
4. Call `POST /api/v1/chat` or `POST /api/v1/chat/stream`:

```json
{
  "message": "I got 3.4 CGPA. What should I do next?",
  "attachmentIds": []
}
```

Normal counseling streams directly. Requests for current external information
use a bounded research tool loop. Swagger displays the API schemas at the bottom.
The streaming endpoint emits `status`, `token`, `reply`, and `done` events as
appropriate; clients should measure the first `token` separately from completion.

Health endpoints: `/health/live` checks the API process; `/health/ready` checks
the authentication provider. Neither endpoint certifies database migrations or
worker availability.

## Dedicated processes

```sh
uv run uvicorn pai.app:create_app_from_env --factory --host 0.0.0.0 --port 8000
uv run python -m pai.interfaces.workers intelligence
uv run python -m pai.interfaces.workers goals
uv run python -m pai.interfaces.workers documents
```

Run each command as a separate service. Alternatively, `compose.workers.yml`
starts all four containers against the external database configured in `.env`:

```sh
docker compose -f compose.workers.yml up --build -d
```

## Model routing and observability

- Counselor: GPT-5.6 Terra with low reasoning and verbosity.
- Standalone greetings and extraction: GPT-5.6 Luna without reasoning.
- Goal analysis: GPT-5.6 Terra with medium reasoning.
- Difficult integrated goal analysis: one bounded GPT-5.6 Sol escalation.

The gateway uses OpenAI Responses, strict Structured Outputs for closed schemas,
validated JSON for open-ended schemas, prompt caching, and final-text streaming.
The reasoning-leak guard remains enabled. Model IDs and reasoning are configurable.

`ENABLE_INTEGRATED_GOAL_ANALYSIS` is disabled until quality evaluation is complete.
Request-correlated timing logs separate context, recall, research, model generation,
and the first SSE token. See [latency configuration](docs/latency-openai.md).

## Validation

```sh
uv sync --locked
uv run pytest -q
uv build
```

Database integration tests use `TEST_DATABASE_URL` and a disposable test database;
their fixtures reset its contents. They are skipped when that database is absent.
Live provider tests are opt-in. No production credentials are needed for unit tests.

The repository excludes local secrets, virtual environments, caches, generated
analysis, and response dumps. Only `.env.example` supplies placeholder configuration.

## Realtime and production guards

Chat context and semantic recall start together. Turn Understanding begins as soon
as context is ready; recall has a 0.9-second deadline and understanding a
1.2-second deadline. Unknown research decisions stream a cautious response without
an extra tool-selection call. Embeddings use the existing HTTP dependency.

Apply `uv run alembic upgrade head` before deploying this revision, then restart
the API and all enabled worker processes. Migration 016 creates the shared usage
counters and worker heartbeats. `/health/ready` checks Auth, PostgreSQL, the exact
schema revision, enabled worker progress and queue age; `/health/live` is independent.
Use liveness for process restarts and readiness for traffic admission.

Rate limits use PostgreSQL atomic counters across replicas. Configure request,
per-user upload, LLM call and token reservation limits in `.env.example`. Failed
model requests keep their reservations to bound retries; these counters are
conservative reservations, not provider billing totals. The peer address is used
for anonymous rate limits: configure trusted proxy handling at the ASGI server,
and never trust arbitrary forwarded headers. Counter-backend failures allow the
request by default so a slow database cannot take down every route; readiness and
rate-limited error logs expose the fault. Set `RATE_LIMIT_FAIL_CLOSED=true` only
when rejecting traffic during a counter outage is the intended policy. Test
environments disable counters.

Goal analysis freshness records supplied input keys and row IDs. Legacy results
without an input manifest are invalidated conservatively. Empty inputs are recorded
so newly supplied facts invalidate assessments too. Memory uses vector relevance;
an optional configured rerank endpoint can refine the candidates within a short
sub-budget. It receives the selected memory text, so configure only an approved
provider. Without it, vector ranking works directly.
