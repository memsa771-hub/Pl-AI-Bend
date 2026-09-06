# PAI check workflow

How to run this backend and walk the student journey. Every story below says **what you send**, **what should happen**, and **what the JSON should look like**.

Interactive try-out: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
In Swagger **Authorize**, paste `data.accessToken` only — no `Bearer` prefix. Curl needs the prefix.

Envelope (every JSON API):

```json
{ "success": true, "data": { } }
```

```json
{ "success": false, "error": { "code": "ONBOARDING_INCOMPLETE", "message": "..." } }
```

---

## 0. Start the server

From the repo root, with `.env` already filled (`DATABASE_URL`, Supabase keys, `VAULT_ENCRYPTION_KEY`, LLM key):

```powershell
cd PAI-main-b-end
uv sync
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
uv run alembic upgrade head
uv run uvicorn pai.app:create_app_from_env --factory --reload --host 127.0.0.1 --port 8000
```

The process also starts the document, vault-intelligence, and goal workers. Chat does **not** wait for them.

Sanity:

```powershell
curl.exe http://127.0.0.1:8000/health/live
```

Expect `200` and `"status": "live"`. Then `GET /health/ready` should be `"status": "ready"` if Supabase Auth is reachable (`503` / `NOT_READY` if it is not).

Set these after login (PowerShell):

```powershell
$BASE = "http://127.0.0.1:8000"
$TOKEN = "paste-accessToken-here"
$H = @{ Authorization = "Bearer $TOKEN"; "Content-Type" = "application/json" }
```

---

## Story 1 — I sign up and log in

**As a new student, I create an account and get a session.**

1. `POST /api/v1/auth/signup`

```json
{
  "fullName": "Ali Khan",
  "email": "ali@example.com",
  "password": "Str0ngPass#1",
  "confirmPassword": "Str0ngPass#1"
}
```

Expect **201**. If Supabase requires email confirm, `data.session` is `null` and `data.message` tells you to verify. Confirm the email, then continue.

2. `POST /api/v1/auth/login`

```json
{ "email": "ali@example.com", "password": "Str0ngPass#1" }
```

Expect **200** plus cookies (`pai_refresh_token`, `pai_csrf_token`). Copy `data.accessToken`.

| Field | What you should see |
|---|---|
| `data.user.emailVerified` | `true` after confirm |
| `data.onboardingCompleted` | `false` |
| `data.nextPath` | `/onboarding` |

3. `GET /api/v1/auth/me` with `Authorization: Bearer <accessToken>`

Expect **200**, same onboarding flags. **401** `INVALID_TOKEN` means the token is missing or stale.

Refresh/logout need the refresh cookie **and** header `X-CSRF-Token` equal to cookie `pai_csrf_token`. Bearer chat/onboarding calls do not need CSRF.

---

## Story 2 — Chat is locked until I onboard

**As a verified user who skipped the form, I cannot talk to PAI yet.**

`POST /api/v1/chat`

```json
{ "message": "Hello" }
```

Expect **403**:

```json
{ "success": false, "error": { "code": "ONBOARDING_INCOMPLETE", "message": "..." } }
```

Same 403 on `/chat/stream`, `/goals`, `/documents`. Vault and onboarding stay allowed.

---

## Story 3 — I complete the starting profile (form path)

**As a student, I fill a small seed profile. Chat unlocks. The rest of the vault is filled later by chat and documents.**

1. `GET /api/v1/onboarding`

Expect **200** while incomplete: `onboardingCompleted: false`, `choices` (`manual` / `cv`), `requiredFields`, `enums` (use **ids**, not labels: `admission` not `"University admission"`, `PK` not `"Pakistan"`).

2. `POST /api/v1/onboarding`

```json
{
  "path": "manual",
  "phone": "+923001234567",
  "dateOfBirth": "2004-03-12",
  "nationality": "PK",
  "currentCountry": "PK",
  "currentCity": "Lahore",
  "currentStatus": "student",
  "gender": "male",
  "educationLevel": "bachelor",
  "institution": "Bahria University",
  "degree": "BSCS",
  "major": "computer_science",
  "gpa": 3.4,
  "primaryGoal": "admission",
  "goalDetail": "MS Computer Science in Germany",
  "studyCountry": "DE"
}
```

Expect **200**. This response is **compact** — no `enums` / `requiredFields`.

| Field | Expected |
|---|---|
| `onboardingCompleted` | `true` |
| `onboardingPath` | `manual` |
| `nextPath` | `/` (home, not `/onboarding`) |
| `identity.phone` | `+923001234567` |

Incomplete body → **422** `VALIDATION_ERROR`. Submit again is idempotent (same `onboardingCompletedAt`).

3. Check what was written

- `GET /api/v1/person/me` → `onboardingCompleted: true`, phone set
- `GET /api/v1/person/educations` → one row, `institution` is **Bahria University**, not `BSCS`
- `GET /api/v1/goals` → one item, `goalType: "admission"`, title from `goalDetail`
- `GET /api/v1/vault` → seed fields filled; many optional keys still empty (that is correct)

---

## Story 4 — I upload a CV instead of the form

**As a student, I skip the form and send a text PDF/DOCX.**

`POST /api/v1/onboarding/cv` as `multipart/form-data` field `file`.

Expect **200**, `onboardingCompleted: true`, `onboardingPath: "cv"`, chat unlocked immediately. Extraction continues in the worker. Do **not** POST the form after a successful CV.

Scanned image-only PDFs extract poorly. Use a text-based file.

---

## Story 5 — I open chat and PAI already knows me

**As an onboarded student, I load history and see one counselor thread, not a ChatGPT sidebar.**

`GET /api/v1/chat/messages`

Expect **200**:

```json
{
  "success": true,
  "data": {
    "conversationId": "...",
    "items": [
      { "id": "...", "role": "assistant", "content": "Hi … I'm PAI …", "createdAt": "..." }
    ],
    "total": 1,
    "hasOlder": false
  }
}
```

The first assistant line is an **opening** from your profile (name, education, goal). It is not a live LLM turn.

---

## Story 6 — I tell PAI my goal in chat

**As a student, I state a life aim. PAI replies immediately. Goal/vault work is queued.**

Prefer stream (what the product uses):

`POST /api/v1/chat/stream`

```json
{ "message": "I want to do MS CS in Germany" }
```

SSE, in order:

| Event | Meaning |
|---|---|
| `event: token` | One chunk of the counselor reply. Reply starts **before** vault/goal jobs finish. |
| `event: reply` | Saved `messageId`, full `reply`, `conversationId` |
| `event: done` | Same payload as non-stream `/chat` |

Non-stream `POST /api/v1/chat` is the same turn as one JSON body.

Expect **200** `data`:

| Field | What to expect |
|---|---|
| `reply` | Counselor prose about MS CS / Germany. Not raw JSON, not ```` fences. |
| `intelligencePending` | Often `true` — vault extract + goal intelligence run **after** the reply |
| `vaultUpdates` | Usually `[]` on this response (apply is deferred) |
| `conversationId` / `messageId` | UUIDs |

Wait a few seconds, then:

- `GET /api/v1/goals` → a goal titled around MS CS / Germany, `goalType: "admission"`, `lifecycleStatus: "active"`
- `GET /api/v1/goals/active` → that same goal
- `GET /api/v1/goals/{id}` → `intelligenceStatus` moves `pending` → `ready` (or `partial` / `failed`). When ready, `intelligence.counselorBrief` and `intelligence.gaps` are filled.

A **greeting** (`Hi`, `Thanks`) should still get a short reply, but should **not** mint a new goal.

A **turn action** (`I'll attach my transcript later`) should **not** become a goal. A goal is stored only when the model classifies a **life aim** whose evidence is actually in your message.

---

## Story 7 — I send a profile fact in chat

**As a student, I mention marks / city / tests. Chat still answers first.**

```json
{ "message": "I completed FSc Pre-Medical 877/1100 from Punjab College" }
```

Expect a normal counselor `reply` and `intelligencePending: true`.

After the intelligence worker runs:

- `GET /api/v1/person/educations` → institution **Punjab College**, degree/major from the text, percentage ~79.7
- Institution must **not** be copied from the degree name (`FSc` must not appear as the school)

`GET /api/v1/vault` shows filled keys. Sensitive values stay masked unless `?includeSensitive=true`.

---

## Story 8 — I upload a transcript and review extracted facts

**As a student, I add a document. PAI extracts candidates. I accept or reject. Accept writes the vault.**

1. `POST /api/v1/documents` multipart: `file`, optional `documentType=transcript`

Expect **202** and `processingStatus` like `queued` / `uploaded`.

2. Poll `GET /api/v1/documents/{id}/status` until `processed` or `awaiting_review`.

3. `GET /api/v1/documents/{id}/candidates`

Each item has `fieldKey`, `value`, `evidenceText`, `confidence`, `reviewStatus` (`pending`).

4. `POST /api/v1/documents/{id}/review`

```json
{
  "acceptCandidateIds": ["<uuid>"],
  "rejectCandidateIds": []
}
```

Expect **200** `{ "message": "Review applied." }`. Accepted facts go through the vault gate in the **same** request. Then check `/vault` or `/person/educations`.

If `identityStatus` is `mismatch`, accept is **409** `DOCUMENT_IDENTITY_UNRESOLVED` until you resolve whether the document is yours.

---

## Story 9 — I switch which goal is current

**As a student with two pursuits, I activate one. Chat should follow that one.**

`GET /api/v1/goals` → pick an id.

`POST /api/v1/goals/{id}/activate` → **200**, that goal `lifecycleStatus: "active"`, others paused, `intelligenceEnqueued` may be true.

`GET /api/v1/goals/active` matches it.

There is also `GET /api/v1/person/goals` (typed rows). The **canonical** current goal for counseling is `/api/v1/goals` + `/active`.

---

## Story 10 — I patch a vault field myself

**As a student, I correct a stored fact without chatting.**

`PATCH /api/v1/vault/fields/location.current_city`

```json
{ "value": "Islamabad", "version": 1 }
```

Expect **200** with the new value. Wrong `version` → conflict. Unknown key → error. This is allowed after login (onboarding not required for vault reads/writes).

---

## What “working” looks like vs bugs

| You did | Healthy | Bug |
|---|---|---|
| Login, skip onboarding, chat | 403 `ONBOARDING_INCOMPLETE` | Chat replies anyway |
| Onboarding submit | Compact status, chat 200 | Submit returns the full enum catalog |
| Chat “I want MS CS in Germany” | Fast reply, then a goal row | Reply waits 30s+ for research; or no goal ever |
| Chat “hi” | Short reply, no new goal | A `general` goal titled “hi” |
| Education without a school name | No education row with `institution = "FSc"` | Degree stored as the university |
| Document review accept | Vault/education updates | Domain marks accepted but vault unchanged |
| Stream | `token` then `reply` then `done` | One giant wait, then a dump |

---

## Automated check (no Swagger)

Needs `.env` + Postgres for the DB-backed tests; others skip.

```powershell
uv run pytest tests -q
uv run pytest tests/test_auth_api.py tests/test_onboarding.py tests/goals tests/test_document_vault.py tests/test_document_intelligence.py tests/test_pai_orchestration.py tests/test_conversation_continuity.py -q
```

---

## Route map (student-facing)

| Area | Auth | Onboarding required? |
|---|---|---|
| `GET /health/live`, `/health/ready` | no | no |
| `/api/v1/auth/*` | varies | no |
| `GET/POST /api/v1/onboarding`, `POST .../cv` | Bearer | no |
| `/api/v1/person/*`, `/api/v1/vault/*` | Bearer | no |
| `/api/v1/chat`, `/chat/stream`, `/chat/messages` | Bearer | **yes** |
| `/api/v1/goals*` | Bearer | **yes** |
| `/api/v1/documents*` | Bearer | **yes** |

Setup detail (env, pooler, Fernet key) lives in [README.md](README.md). Layout lives in [DEVELOPER.md](DEVELOPER.md). Onboarding field ids live in [docs/onboarding-fields.md](docs/onboarding-fields.md).
