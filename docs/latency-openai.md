# PAI latency and OpenAI migration

Normal counseling now streams without a tool-decision call. Live research uses
the existing deterministic classifier and a bounded tool loop (one round by
default), followed by streaming. Context construction and semantic recall still
run concurrently. The 180-character reasoning-leak guard remains active.

## Configure

Copy the LLM settings from `.env.example` into your deployment environment.
Existing `.env` values override Python defaults. Set `OPENAI_API_KEY` to a key
with access to the configured models. This is independent of a Codex subscription.

- Counselor: `gpt-5.6-terra`, low reasoning and verbosity.
- Standalone greetings/thanks: `gpt-5.6-luna`, no reasoning. Contextual replies
  such as “yes” retain Terra.
- Vault and document text extraction: `gpt-5.6-luna`, no reasoning.
- Goal analysis: `gpt-5.6-terra`, medium reasoning.
- Difficult integrated goal analysis: one `gpt-5.6-sol` escalation, medium reasoning.
- Embeddings remain `text-embedding-3-small`. The separate existing scanned-page
  OCR provider/model remains configurable and unchanged.

The gateway owns routing; the counselor has no OpenAI transport dependency.
Responses uses final `output_text` events, encrypted reasoning-item replay on
tool continuations, `store=false`, and a stable task/version `prompt_cache_key`.
System policy and profile remain ahead of recent turns. Caching is automatic;
the cache key does not guarantee a hit.

Closed output contracts use strict JSON Schema Structured Outputs. Existing
contracts with arbitrary maps/Any use JSON mode plus Pydantic validation, since
closing those schemas would discard data. Provider errors, refusals, incomplete
responses and truncated SSE streams fail explicitly. Reasoning and tool argument
deltas are never exposed as counselor text.

`LLM_COUNSELING_TIMEOUT_SECONDS=30` sets the counseling HTTP timeout (stream read
inactivity, not an end-to-end wall-clock deadline).
The configured output budget receives 1,024 additional tokens when reasoning is
enabled, since Responses counts reasoning in its output limit. Tune budgets and
timeouts from measurements; this is not a guarantee of a 1–3 second reply.

To roll back text inference, explicitly set `LLM_DEFAULT_PROVIDER=deepseek` and
all text workload model settings (`LLM_COUNSELING_MODEL`,
`LLM_SIMPLE_COUNSELING_MODEL`, `LLM_EXTRACTION_MODEL`, `LLM_DOCUMENT_MODEL`,
`LLM_GOAL_MODEL`, `LLM_COMPLEX_MODEL`) to a supported DeepSeek model. Provider
switching does not silently replace model IDs or retry student requests elsewhere.

## Separate processes

Apply database migrations using the existing deployment procedure before starting
services. All services use the same PostgreSQL/Supabase configuration.

```sh
uvicorn pai.app:create_app_from_env --factory --host 0.0.0.0 --port 8000
python -m pai.interfaces.workers intelligence
python -m pai.interfaces.workers goals
python -m pai.interfaces.workers documents
```

Run these in four terminals/process-supervisor services, or use:

```sh
docker compose -f compose.workers.yml up --build -d
```

`compose.workers.yml` expects the database specified in `.env` to be reachable
from containers. It does not start or migrate a database. The existing
`docker-compose.yml` remains a local test-database helper.

`RUN_WORKERS_IN_API=false` is the default. Start all three worker processes;
otherwise queued work remains pending. The `ENABLE_*_WORKER` flags only select
embedded loops when `RUN_WORKERS_IN_API=true`, a development opt-in. Dedicated
worker commands select their own worker regardless of those flags. SIGINT/SIGTERM
allow 30 seconds to finish before cancellation; existing leases/retries recover
interrupted work. Configure the supervisor/container grace period above 30 seconds.

## Goal consolidation rollout

`ENABLE_INTEGRATED_GOAL_ANALYSIS=false` preserves the existing production
pipeline until quality is evaluated. Set it to true to use raw research evidence
followed by one validated call producing research, assessment, gaps, plan and brief.
Assessment refreshes reuse stored research with the same integrated call.

Confidence below 0.5 with available evidence, or contradictory evidence, triggers
at most one Sol call when `ENABLE_GOAL_ESCALATION=true`. Missing research alone
does not trigger a costly retry. Missing/contradictory evidence and failed
escalation remain `partial`. Model-supplied URLs cannot replace retrieved sources.
Research hits still need quality evaluation; retrieval is not proof of correctness.

Before enabling broadly, compare legacy and integrated results on admission, job,
internship, sparse-profile, conflicting-evidence and multilingual cases. Check
grounding, preserved unknowns, dependency validity, useful next steps and brief
quality, as well as latency/cost. Unit tests validate contracts and call counts;
they do not establish counseling quality. Reusing embeddings across discovery,
goal relevance and memory is a later change; keyword discovery remains intact.

## Measure latency

Enable INFO logging for `pai.platform.latency`. Each request has a generated
`X-Request-ID`; timing logs use that ID and never include prompts or credentials.
Pure ASGI middleware keeps the correlation alive through the entire SSE response.

Stages: `request_received`, `auth`, `person_lookup`, `conversation_setup`,
`context`, `semantic_recall`, `memory_embedding`, `memory_vector_search`,
`tool_decision`, `tool_execution`, `web_search`, `llm_first_byte` (first parsed
OpenAI SSE line), `llm_first_token`, `llm_total`, `first_sse_token`, `total_request`.
Parallel durations overlap; do not add them to infer total latency. Tool and LLM
timings may appear multiple times for one request. Skipped operations have no span.

Research streams emit a `status` event before context/research work. Clients may
display its message as a progress indicator; it is not an answer token. Measure
the first `token` event separately in the client, since server timings exclude
proxy/network buffering and browser rendering. Compare warm and cold runs and
report median/p95 first-visible-token and total time separately for normal and
research turns. Target 1–3 seconds for normal turns, subject to live benchmarking.

API implementation references:
[Responses](https://developers.openai.com/api/docs/guides/responses),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
