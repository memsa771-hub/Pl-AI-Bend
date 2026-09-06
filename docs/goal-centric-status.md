# Goal-centric PAI — what’s working vs what to fix

Status after live testing (`musawirkhalid59@gmail.com`) against the local API. No code changes in this document; it is the punch list for the next implementation slice.

---

## Product intent (reminder)

PAI should:

1. Create and keep **canonical goal records** (not one blob of “Germany or China”).
2. Chat **immediately**; research/assessment/gaps/plan run in the **background**.
3. Counselor uses a short **`counselor_brief`** + gaps for the **active thread goal**.
4. When the user fills a gap in chat (IELTS, internship, passport, …), **Vault updates** and **goal intelligence gaps refresh** — the system of record actually progresses.

---

## What is working

### Chat stays fast

- `POST /api/v1/chat` returns a reply without waiting for Research/Assessment.
- `intelligencePending: true` on turns that queue background extraction is expected.

### Goal records and intelligence pipeline (first pass)

- Goals are created and listed: `GET /api/v1/goals`.
- Detail and active endpoints return intelligence when a job has completed:
  - `GET /api/v1/goals/{id}`
  - `GET /api/v1/goals/active`
- Background pipeline can reach `intelligenceStatus: ready` with:
  - `counselor_brief` (within the 5–10 line idea)
  - typed `gaps` (`item`, `category`, `blocking`, `action`)
  - `overallFit`, `planStepCount`

### Counselor actually uses the brief

Live example — active goal **University of Bologna**:

- Stored brief called out: moderate fit, GPA 3.35, IELTS 7.5, thin work/projects, map transcript, CILS if Italian, reference, motivation letter, deadlines.
- User asked: *What are my biggest gaps and what should I do next for Bologna?*
- Reply matched that brief almost line-for-line (work/project depth, prerequisite mapping, reference, motivation letter, deadlines).

So **read path** (brief → counselor → reply) works when `conversations.active_goal_id` is set and intelligence is `ready`.

### Conversation-level gap filling (talk only)

If the user then states facts in chat (internship, projects, CILS B2, passport expiry), the counselor **verbally** treats those as covered and narrows remaining steps.

That is useful counseling. It is **not** the same as updating `goal_intelligence`.

### APIs that exist

| Endpoint | Role |
|----------|------|
| `GET /api/v1/goals` | List (intelligence is `null` on each item) |
| `GET /api/v1/goals/active` | Thread active goal + intelligence |
| `GET /api/v1/goals/{id}` | Detail + intelligence |
| `POST /api/v1/goals/{id}/activate` | Sets thread pointer + can enqueue if stale |
| Chat + document/intelligence workers | Unchanged fast path |

---

## What is broken or incomplete

### 1. Stored gaps do not update when the user fills them (critical)

**Expected:** internship / CILS / passport in chat → Vault write → `assessment_refresh` → those gaps disappear from `goal_intelligence.gaps`, `updatedAt` moves.

**Observed (Bologna):**

- Chat acknowledged the facts.
- After 45–65+ seconds, `intelligence.updatedAt` was **unchanged** (`2026-08-24T08:58:27Z`).
- Stored gaps still included: no work experience, limited projects, no Italian, passport/visa.

**Root cause (two layers):**

1. **Vault often never gets the facts.** After the fill turn, `/vault` still had no work experiences/projects; `vaultUpdates` on the chat payload was empty. Extraction/apply did not persist internship, CILS, passport as typed/Vault fields.
2. **Refresh is too narrow even when Vault does change.** `mark_intelligence_stale_for_vault_update` only maps a small set of keys (e.g. `application.test_scores`, education, nationality). Work, projects, certifications, passport are not in that map, so Assessment/Gaps/Planning never re-run.

Until both are fixed, PAI cannot “complete” a goal in the database.

### 2. Active goal pointer is inconsistent

`GET /api/v1/goals/active` is **only** `conversations.active_goal_id`, not “latest row in `goals`”.

- Goals can appear in the list while `/goals/active` is `null` if create went through typed apply / extraction without `switch_conversation_active_goal`.
- Manual `POST /goals/{id}/activate` always works because it writes that pointer.

Chat that “feels” about Bologna can still be detached from the thread pointer.

### 3. Goal detection is phrase-sensitive and over-creates

**Creates too easily on explicit life-aim wording** (`my goal is`, `aiming to`, `getting into HUST`) and **not** on many in-goal turns (`what about Germany`, `how do I get into TUM`, shortlisting).

**Over-creates:**

- Universities as separate goals (HUST, TUM) instead of options inside a country/program goal.
- Chat phrasing as a new goal: *“I want to focus on University of Bologna”* spawned a **draft** titled `focus on University of Bologna` while Bologna already existed.
- Duplicate titles (`MS in Italy` twice, TUM twice).
- Several rows with `lifecycleStatus: active` at once.

Onboarding still stores a **single** journey/career blob (“Admission in Germany or China…”) that is **not split** into two `admission` goals.

### 4. List vs detail contract

`GET /api/v1/goals` always returns `"intelligence": null`. Clients must hit detail/active for brief and gaps. Easy to think intelligence “isn’t there.”

### 5. Chat UI hints drift from the active goal

Starters can still push **Germany / DE** while the active goal is **Italy / Bologna**. Known-facts dump lists every career/study title, which confuses the counselor about which pursuit is current.

### 6. Worker robustness (already partially patched)

Goal worker previously crashed on async lazy-load of `Person.vault` (`MissingGreenlet`). That can leave jobs `pending`/`failed`. Confirm under load that `assessment_refresh` and full pipeline both complete.

---

## The loop that should exist

```
user fills a gap in chat
  → intelligence worker extracts + writes Vault / typed profile
  → mark affected goals stale (active goal + gaps that match that fact type)
  → assessment_refresh: Assessment → Gaps → Planning → new counselor_brief
  → next chat reads updated brief + gaps
```

Do **not** re-run Research on every IELTS/internship message. Re-run Research only when anchors change (country/program/university) or research is stale.

Do **not** ask the counselor LLM to rewrite `gaps` JSON on the same turn as the user reply.

---

## Fix order (implementation punch list)

1. **Thread pointer** — every chat-path create/switch that is “what we’re talking about” must set `conversations.active_goal_id`. `activate_goal` must pause other person-level actives; all writers must use it.
2. **Vault writes** — chat extraction must persist work, projects, certifications (e.g. CILS), passport/identity the same way IELTS/CGPA persist.
3. **Selective refresh** — on those writes, enqueue `assessment_refresh` for the active goal and any goal whose stored gap categories match. Persist new gaps/brief.
4. **Resolver** — no new goal from “focus on”, “how do I get into {uni}”, or university-only mentions; match existing admission goal and attach the thread. Split/seed onboarding “Germany or China MS” into two goals later if product still wants that.
5. **Hygiene** — list endpoint: either embed a short intelligence summary or document that clients must use detail; tighten starters to the active goal.

---

## Live scorecard (Bologna session)

| Check | Result |
|-------|--------|
| Goals list populated | Yes (noisy) |
| Detail has brief + gaps | Yes |
| `/goals/active` populated | Yes in that session (Bologna) |
| Chat uses intelligence | Yes — strongly |
| Chat tries to close gaps | Yes — in language |
| Stored intelligence updates after user fills gaps | **No** |
| Vault captures internship / CILS / passport from that chat | **No** (observed) |
| Durable progress toward goal completion | **No** |

---

## Bottom line

**Working:** background first-pass intelligence, counselor **reading** the brief, fast chat.

**Not working:** the **write-back** from chat → Vault → refreshed gaps. Until that lands, PAI sounds goal-complete in conversation and stays incomplete in `goal_intelligence`.
