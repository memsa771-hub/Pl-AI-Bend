# Placement AI — Global architecture and intelligence audit

Date: 2026-09-06. Audited local working tree based on commit `610bbc1643c3c3f81c8584bbff27c834ce563530`, including the pre-existing uncommitted latency/provider changes.

This is an audit and implementation plan, not an implementation. Production code, migrations, configuration and existing edits were left unchanged. No root AGENTS.md was found. The constitution in the attachment is treated as the intended direction; a proposed concise constitution appears below for a later implementation PR.

## Decision

Evolve the existing architecture. First fix canonical-write safety, failure propagation, deletion and concurrent processing; then replace semantic shortcuts. Merely adding Turn Understanding would leave unsafe writers underneath it. Moving keyword lists into JSON would not solve semantic hardcoding either.

The most consequential verified failure is that **pending** does not reliably mean **not applied**. A mocked persistence probe changed an education GPA from 3.0 to 1.7 while returning pending; another pending write superseded the existing active sparse value. Separately, short-message boosters assigned negated or third-party facts to the student without calling a model.

There are 16 grouped semantic findings and 17 other grouped defects/risks. High means credible canonical correctness, privacy, or major global-behavior impact; Medium means material reliability/quality or scale risk. No Critical exploit was established in this local audit. Related findings overlap and are not counts of independent production incidents.

## Scope and evidence limits

Repository-wide searches covered source, tests, prompts, JSON policies/taxonomy, migrations, scripts, packaging and operational files. The source inventory contains 204 Python files and tests contain 42 Python files. The eleven requested flows were traced through their principal implementations. This is not a claim that every line, dependency, or deployment configuration was exhaustively verified.

Read-only function probes used synthetic values and mocked persistence; they did not mutate a real database or call providers. The existing suite produced **377 passed, 47 skipped, 3 warnings in 30.36 seconds**. TEST_DATABASE_URL was deliberately set to an unavailable localhost port, with live DeepSeek disabled. Database integration and live-provider coverage therefore remain unverified. Tests can truncate the database they target; a dedicated disposable database is mandatory for the next phase.

No dependency-vulnerability, remote deployment, issuer-authenticity, or live multilingual model-quality certification is implied. Concurrency findings below are static schedules to reproduce in PostgreSQL, not measured incidents.

## A. Current architecture map

1. **Student message → counselor.** API dependencies validate bearer JWT, resolve owned Person, require onboarding. `interfaces/api/chat.py` gets/creates the person's conversation; `domains/conversations/service.py:begin_chat_turn` commits user message and run. Nonstreaming invokes `followup.handle_user_message` → `PAIOrchestrator` graph; SSE assembles state manually and uses `iter_reply_tokens`. Reply persistence and intelligence queueing happen after generation.
2. **Counselor context.** `context.build_counselor_context` combines Vault completion, typed records, goal facts, recent conversation, pending values, verification cases, discovery and stance. `orchestrator.node_load_student_context` runs this alongside person-scoped semantic recall, then adds attachment metadata. A process cache uses person ID/Vault version. Output is a compact profile prompt.
3. **Stance/routing.** `routing.py` regexes decide turn kind, extraction suppression, tool availability and greeting limits. `conversation_stance.py` and context's pressure regex produce advisory posture. These do not themselves authorize external actions.
4. **Next question.** Vault catalog/completion enumerate missing fields; `profile_depth.py` invents earlier-education gaps; `discovery.py` ranks fields/depth by fixed formula. Orchestrator records the nominated gap if a question mark appears.
5. **Memory.** `PersonMemoryService` → `AsyncPostgresMemoryStore` embeds query, retrieves bounded person-scoped vector candidates, then ranks through `formation.py`; lexical fallback scans bounded recent rows. Accepted/pending/conflicting/observed candidates form memories with separate claim formatting. Embedding backfill after Vault commit is a useful existing safeguard.
6. **Chat Vault.** Deferred `PersonJob` → intelligence worker → `run_intelligence_followup` → `finish_intelligence`. Chat source combines boosters and optionally omnibus LLM. LLM candidates pass source-span grounding; boosters bypass that path. Normalize/merge → partition → candidate evaluation → kernel policy → sparse or typed writer. Memory and Journey are side effects in this flow.
7. **Goals.** Extraction yields current_goal; resolver checks life-aim evidence then chooses create/reinforce/switch/secondary via GoalService. The career-interest typed writer separately upserts goals and can fall back to legacy mutation. GoalJob worker loads profile/research, runs research→assessment→gaps→plan→brief (five model stages in the default full path), and writes GoalIntelligence. The optional integrated analysis path is disabled by default.
8. **Documents.** Upload reads bytes, validates/sniffs/scans, uploads private storage object, persists Document/Version/job. Worker digitizes native/OCR, classifies, extracts typed/omnibus candidates, normalizes, matches identity, grounds spans, computes reconciliation and stores facts/cases. Safe/confirmed candidates reach kernel application. Student review/resolution can apply a document fact. CV onboarding invokes the same analysis directly after queueing it.
9. **Workers.** Person, document and goal DB queues claim with SKIP LOCKED plus advisory locks, commit processing state, execute work, then finish/retry. Locks are scoped differently; leases lack fencing. Workers run separately or optionally in the API process.
10. **Research.** Counselor tool route → registry/web_search → research intelligence → generic search capability → Tavily integration. Goal research uses the same research service then model structures search evidence. Missing/failed search is represented explicitly, but official-source and per-requirement verification are not implemented.
11. **Tasks/planning.** Counselor proposals → `planner.plan_next_actions` passthrough → action service filters profile-write tasks and deduplicates titles → StudentTask proposed rows. Goal plan JSON is separate. No university-application executor or general opportunity-matching engine was found.

The existing interfaces/workflows/intelligences/domains/kernel/capabilities/integrations/platform split is useful. Preserve it. ORM use of platform Base is normal infrastructure coupling, not a reason for a rewrite. More substantive boundary leaks are intelligence workers directly persisting goal analysis/anchors, orchestrator owning commit points, and domain memory obtaining embedding providers rather than an injected interface. Address these only as part of the fixes that need them.

## B. Semantic hardcoding inventory

Each entry names the actual functions and the replacement boundary. “Why it exists” is inferred from code/comments and behavior, not author testimony.
### S01 — High: Boosters assign meaning and bypass multilingual understanding

**File/function:** [intelligences/vault/boosters.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/vault/boosters.py:73>) — `run_deterministic_boosters`; [intelligences/vault/sources/chat.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/vault/sources/chat.py:16>) — `ChatSourceDomain.extract`; [intelligences/vault/merge.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/vault/merge.py:35>) — `merge_candidates`; [kernel/evidence/candidate_eval.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/kernel/evidence/candidate_eval.py:40>) — `evaluate_candidate`.

**Current behavior:** Any booster hit in a message of six whitespace-delimited words or fewer bypasses the model. City, school, scholarship, project and country mentions become explicit student assertions. Confidence-based merge can retain those assertions over model interpretation.

**Why it exists:** Latency and extraction-recall optimization.

**Global/production risk:** Negation, attribution, hypothetical plans and non-whitespace languages are mishandled before write gates. Gate acceptance is demonstrated; actual typed persistence depends on an existing identifiable education record.

**Decision:** Remove semantic authority.

**Permanent replacement:** Only a validated observation with subject, assertion, temporality and exact source span may propose a fact. Deterministic parsers may parse a semantically identified span, never infer its owner or meaning.

**Migration risk:** Disabling boosters lowers short-term recall; keep uncertain observations for retry, not accepted guesses.

**Tests required:** Negated city; sister GPA; hypothetical projects; mixed languages; short CJK turns; model timeout; competing model/booster outputs.

**Evidence:** Local probes: 'I never lived in Berlin' => city Berlin, accept; 'My sister GPA is 3.4' => student bachelor GPA 3.4/4, accept; zero LLM calls.

### S02 — High: Grade guessing spans every ingestion and presentation layer

**File/function:** `parse_gpa / gpa_on_4` in intelligences/documents/normalization/gpa.py; `TranscriptExtraction / to_field_map` in intelligences/documents/extraction/schemas/transcript.py; `_normalize_value` in intelligences/vault/normalize.py; `run_deterministic_boosters` in intelligences/vault/boosters.py; `_existing_belief` in intelligences/documents/pipeline.py; `build_known_facts` in intelligences/counselor/context.py; `OnboardingSubmit` in workflows/onboarding/contracts.py.

**Current behavior:** Missing scales become 4.0; boosters overwrite explicit 3.4/5 with 3.4/4 and add bachelor. Onboarding GPA is constrained to 0–4 with no scale field.

**Why it exists:** Convenient US-style GPA representation.

**Global/production risk:** Wrong native grades propagate into counseling and reconciliation; valid grades above 4 are rejected.

**Decision:** Refactor.

**Permanent replacement:** Use one lossless grade contract: original text, value/classification, scale nullable, direction nullable, grading-system reference, evidence and confirmation state. Unknown scale stays unknown across all consumers.

**Migration risk:** Do not blanket-null all stored /4 values: separate explicit evidence from historical defaults; quarantine unresolved values and invalidate affected summaries.

**Tests required:** 8.2 unknown; 3.4/5; 0; decimal comma; letter grades; UK 2:1; German 1.7; multiple records; presentation and onboarding round trips.

**Evidence:** Local probes reproduced unknown 8.2 => /4 and explicit 3.4/5 => /4.

### S03 — High: Linear grade conversion is treated as equivalency

**File/function:** [intelligences/documents/reconciliation/comparators.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/reconciliation/comparators.py:13>) — `values_equivalent / relative_delta`; [intelligences/documents/normalization/gpa.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/normalization/gpa.py:38>) — `gpa_on_4`.

**Current behavior:** Different scales are linearly converted to /4; distance below .05 means equivalent and relative delta drives conflict severity.

**Why it exists:** Comparing document grades with stored values.

**Global/production risk:** Numerically proportional grades from unrelated systems can confirm one another; scale, direction, classification and equivalency provenance are absent.

**Decision:** Remove implicit equivalency.

**Permanent replacement:** Compare only within an evidenced grading context; cross-system results are unknown unless a versioned, applicable equivalency source explicitly supports conversion.

**Migration risk:** Existing confirmed comparisons may need re-review; preserve original evidence and decisions.

**Tests required:** 80/100 versus 3.2/4 must not automatically confirm; reverse-direction scales; unknown scale; native same-context equality.

**Evidence:** Local probe: 80/100 and 3.2/4 compare equivalent.

### S04 — High: Education gap detection assumes a universal ladder

**File/function:** [intelligences/counselor/profile_depth.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/profile_depth.py:106>) — `classify_rung / _ladder_gaps / _row_text`.

**Current behavior:** Regex maps degree, major and even institution text into five rungs; diploma maps to higher_secondary; every lower rung becomes a gap.

**Why it exists:** Ask for earlier education needed for applications.

**Global/production risk:** Vocational, professional, integrated and discontinuous paths become false missing prerequisites. A word in an institution name can influence level.

**Decision:** Refactor.

**Permanent replacement:** Preserve qualification names and model an extensible set of qualification records with optional reference-backed level/framework mappings. Derive requested history from an actual decision or requirement.

**Migration risk:** Keep existing API level as a compatibility projection, not canonical authority.

**Tests required:** Ausbildung, Baccalauréat, professional diploma, integrated degree, incomplete study, multiple qualifications and institution-name false positives.

**Evidence:** Local probe: 'Postgraduate Diploma' maps to master.

### S05 — High: Research access depends on English and Roman Urdu keywords

**File/function:** [intelligences/counselor/routing.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/routing.py:81>) — `classify_turn / counselor_web_search_enabled`; [intelligences/counselor/orchestrator.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/orchestrator.py:397>) — `node_run_conversation_agent / iter_reply_tokens`.

**Current behavior:** Only LIVE_RESEARCH regex matches receive the web tool. Explain-only prefixes override research detection.

**Why it exists:** Avoid extra tool calls on ordinary chat.

**Global/production risk:** Equivalent non-English questions cannot use live sources. 'Can you explain current fees...' is classified as personal advice before research matching.

**Decision:** Refactor.

**Permanent replacement:** Turn understanding supplies needs_external_evidence and rationale using original message plus history. Config, credentials, budgets and tool permissions stay deterministic.

**Migration risk:** Shadow evaluate recall and latency before replacing route authority; on model failure keep an explicit unknown state.

**Tests required:** Multilingual fee/deadline questions, indirect references, mixed fact plus question, provider unavailable.

**Evidence:** Local probe: French tuition question => PERSONAL_ADVICE.

### S06 — Medium: Contextual replies are discarded as trivial

**File/function:** [intelligences/counselor/routing.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/routing.py:68>) — `is_greeting / should_extract_facts / counseling_reply_max_tokens`.

**Current behavior:** 'yes', 'no', 'continue', 'help' and strings shorter than two characters skip extraction; greetings cap replies at 96 tokens.

**Why it exists:** Latency optimization.

**Global/production risk:** Answers to confirmation or planning questions carry meaning only in context; they are never sent to the extractor. Standalone model-routing salutations are narrower, so these decisions are inconsistent.

**Decision:** Refactor.

**Permanent replacement:** Use recent question/confirmation state plus semantic turn result; keep only genuinely context-independent transport fast paths.

**Migration risk:** Avoid interpreting bare yes as authorization for unrelated actions.

**Tests required:** Confirmation yes/no, one-character answers, corrections, new-session salutations, contextual continue.

**Evidence:** Local probe: should_extract_facts('no') is false.

### S07 — Medium: Stance and pressure are inferred from vocabulary and turn count

**File/function:** [intelligences/counselor/conversation_stance.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/conversation_stance.py:97>) — `compute_stance`; [intelligences/counselor/context.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/context.py:122>) — `_pressure_signal`.

**Current behavior:** English uncertainty/decision regexes control advisory stance; any 'my parents' or 'my friend' can imply pressure; ready/partial intelligence plus two prior assistant turns enables guide.

**Why it exists:** Avoid premature execution.

**Global/production risk:** Neutral family references become pressure; non-English uncertainty is missed; unrelated assistant turns stand in for goal understanding. Guidance is advisory, not an execution permission.

**Decision:** Refactor.

**Permanent replacement:** Turn understanding emits uncertainty, external-pressure evidence, goal commitment and unresolved motivations; counselor retains conversational judgment.

**Migration risk:** Do not turn model stance into execution authorization.

**Tests required:** Neutral versus coercive family statements, negation, multilingual uncertainty, goal switch after many turns.

**Evidence:** Static code trace

### S08 — Medium: Discovery scores encode a fixed value judgment

**File/function:** [intelligences/counselor/discovery.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/discovery.py:142>) — `score_field / score_depth_gap / select_discovery_candidates`; [domains/student/vault/catalog.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/vault/catalog.py:591>) — `VAULT_CATALOG`.

**Current behavior:** Fixed C/I/E weights, section keyword hits, per-field impacts and goal-type sections choose the top question; fields can be suppressed by substring matches in formatted facts.

**Why it exists:** Stable, testable prioritization.

**Global/production risk:** Question relevance depends on vocabulary and fixed assumptions rather than the present decision.

**Decision:** Refactor.

**Permanent replacement:** Enumerate available evidence and missing constraints deterministically; semantic next-intervention selection chooses answer, explore, clarify, research or ask with evidence and burden rationale.

**Migration risk:** Keep anti-repetition and privacy policies; evaluate intervention quality, not equality with old numeric scores.

**Tests required:** Decision pressure versus GPA; no useful question; high-burden sensitive fields; repeated unanswered question; global languages.

**Evidence:** Static code trace

### S09 — Medium: Question tracking assumes any question mark means the chosen gap was asked

**File/function:** [intelligences/counselor/orchestrator.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/orchestrator.py:605>) — `_record_discovery_question`; [intelligences/counselor/discovery.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/discovery.py:217>) — `select_discovery_candidates / score_depth_gap`.

**Current behavior:** A '?' records the nominated field even if a different question was asked. Depth candidates do not use the recently-asked penalty.

**Why it exists:** Cheap streaming-compatible tracking.

**Global/production risk:** Real questions are misattributed and depth gaps can repeat despite the nominal three-day suppression.

**Decision:** Refactor.

**Permanent replacement:** Record the actual selected intervention ID and emitted question metadata; apply repetition policy uniformly.

**Migration risk:** Backfill no fabricated question history; existing records are approximate.

**Tests required:** Question about motivation while GPA nominated; Arabic question mark; repeated depth question; no question.

**Evidence:** Static code trace

### S10 — High: Goal identity is inferred using weak anchors and token containment

**File/function:** [intelligences/goals/resolver.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/resolver.py:105>) — `_classify_goal_type / _extract_anchors_from_intent / _text_mentions_goal`; [domains/goals/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/goals/service.py:81>) — `_anchor_match_score / find_matching_goal`.

**Current behavior:** English fallback parses types/degrees; first mentioned country becomes target; weighted anchors use .6 threshold; resolver matches individual title tokens.

**Why it exists:** Deduplicate rephrases without an extra model call.

**Global/production risk:** Country plus degree alone can merge distinct programs; shared title tokens can reinforce unrelated pursuits; unknown languages lose anchors.

**Decision:** Refactor.

**Permanent replacement:** Semantic goal relation proposal references existing goal IDs: same, refine, switch, secondary, uncertain; domain validates ownership and lifecycle transitions.

**Migration risk:** Merge only reviewed duplicates; preserve history and active pointers. Keep GENERAL as extension escape hatch.

**Tests required:** Two programs same country/degree; renamed goal; several parallel aims; multilingual rephrase; explicit switch.

**Evidence:** Static code trace

### S11 — Medium: Closed education/test/intake vocabulary loses global detail

**File/function:** [domains/student/normalization/vocab.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/normalization/vocab.py:24>) — `EducationLevel / StandardizedTest / IntakeSeason`; [workflows/onboarding/catalog.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/workflows/onboarding/catalog.py:137>) — `DEGREE_FOR_LEVEL`; [workflows/onboarding/contracts.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/workflows/onboarding/contracts.py:155>) — `OnboardingSubmit / OnboardingTestScoreItem`; [intelligences/vault/normalize.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/vault/normalize.py:81>) — `_normalize_value`.

**Current behavior:** Forms use fixed levels and tests; 'other' exists but test item has only name enum and score; season-based intake does not represent all calendars. Qualification aliases collapse source wording.

**Why it exists:** Simple forms and validation.

**Global/production risk:** Unknown tests and qualifications cannot retain enough context; season labels lack academic-year/location context.

**Decision:** Refactor.

**Permanent replacement:** Keep stable UI categories as optional summaries; add original label, awarding body/framework, dates and extensible test identifier. Original representation survives normalization.

**Migration risk:** Add fields before changing enum clients; do not remove useful validation or require every record to map.

**Tests required:** Unknown test, vocational program, Southern Hemisphere intake, nonseasonal calendar, original labels.

**Evidence:** Static code trace

### S12 — High: Name matching and evidence length favor Latin scripts

**File/function:** [intelligences/documents/identity/names.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/identity/names.py:9>) — `fold_name / names_match`; [intelligences/documents/identity/matcher.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/identity/matcher.py:10>) — `match_student`; [intelligences/documents/evidence/grounding.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/evidence/grounding.py:19>) — `evidence_grounded / compact_span`; [intelligences/vault/ground.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/vault/ground.py:14>) — `evidence_in_source`.

**Current behavior:** Names are ASCII-folded; identical CJK names become empty tokens and ambiguous. Evidence shorter than 3/4 characters is rejected. Compact OCR matching removes non-Latin text; direct long Unicode substring matching does work.

**Why it exists:** Accent tolerance and anti-hallucination checks.

**Global/production risk:** Valid global names cannot confirm identity; short valid source spans fail. DOB equality can promote an ambiguous name to matched, so a date is acting as identity proof.

**Decision:** Refactor.

**Permanent replacement:** Unicode-preserving normalization and contextual evidence offsets; model/reference-assisted aliases must remain evidence-backed. Identity remains deterministic multi-signal validation with unknown fallback.

**Migration risk:** Do not replace conservative ambiguity with permissive fuzzy matching; no automatic merging on transliteration alone.

**Tests required:** Identical/different CJK and Arabic names; accents; short Chinese names; common DOB with different names.

**Evidence:** Local probe: identical 王伟 => ambiguous.

### S13 — High: Ambiguous dates are guessed and invalid ISO dates throw

**File/function:** [intelligences/documents/normalization/dates.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/normalization/dates.py:11>) — `parse_date`; [domains/student/typed_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/typed_apply.py:281>) — `_parse_date`.

**Current behavior:** 03/04/2005 is silently DMY; month/day swap occurs only when one exceeds 12. ISO date construction is outside ValueError handling. Partial YYYY-MM typed dates become the first day.

**Why it exists:** Convenient date normalization.

**Global/production risk:** Wrong DOB impacts identity; invalid ISO input can abort extraction; partial-date precision is invented.

**Decision:** Refactor.

**Permanent replacement:** Preserve raw date, precision and calendar/order context. Resolve ambiguity from explicit evidence or confirmation; validate malformed dates safely.

**Migration risk:** Historical guessed dates need provenance review, not bulk locale conversion.

**Tests required:** Ambiguous US/EU forms; ISO invalid day; leap years; partial dates; non-Gregorian original.

**Evidence:** Local probes: 03/04/2005 => 2005-04-03; 2025-02-30 raises ValueError.

### S14 — Medium: Document type and authority start with filename vocabulary

**File/function:** [intelligences/documents/classification/taxonomy.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/classification/taxonomy.py:41>) — `classify_from_name / _best_type`; [intelligences/documents/data/taxonomy.json](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/data/taxonomy.json>) — `filename_hints`; [intelligences/documents/data/policy.json](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/data/policy.json>) — `authority`; [intelligences/documents/classification/classifier.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/classification/classifier.py:6>) — `classify_document`.

**Current behavior:** Filename match overrides supplied hint and content; English hint vocabulary is reused for content; type determines authority policy.

**Why it exists:** Cheap classification and declarative policy.

**Global/production risk:** Unknown-language files fall through; a renamed file can acquire stronger type authority. Content and identity gates reduce risk but do not verify issuer authenticity.

**Decision:** Refactor classification; keep authority policies.

**Permanent replacement:** Semantic/multimodal document-type proposal with source evidence and uncertainty. Filename is a hint; deterministic authority requires supported type/provenance and cannot mean issuer verification.

**Migration risk:** Retain generated-document exclusion; migration must not broaden auto-accept.

**Tests required:** Renamed unrelated file; non-English transcript; mixed document; generated SOP; forged type label.

**Evidence:** Static code trace

### S15 — Medium: Memory ranking is tuned but not a universal semantic policy

**File/function:** [domains/memory/formation.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/memory/formation.py:90>) — `importance_of / rank_score`; [domains/memory/postgres_store.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/memory/postgres_store.py:214>) — `_rank_entries`.

**Current behavior:** Fixed importance/stability/recency weights blend with vector similarity and min-max rescaling. Comments cite local recall evaluation; weights are not wholly unexplained.

**Why it exists:** Stable bounded retrieval; existing evaluation-driven tuning.

**Global/production risk:** Fixed section priorities can hide relevant less-common facts; lexical fallback degrades languages without spaces.

**Decision:** Refactor gradually.

**Permanent replacement:** Retain tenant/status/evidence filtering, embeddings and bounded retrieval; evaluate optional learned reranking against multilingual relevance labels and latency budget.

**Migration risk:** Do not add another model call by default or remove a functioning evaluated baseline without evidence of improvement.

**Tests required:** Multilingual held-out relevance; singleton/equal similarity; stale/superseded facts; embedding timeout; cost/latency.

**Evidence:** Static code trace

### S16 — Medium: Goal assessment conflates sourced requirements with model judgment

**File/function:** [intelligences/goals/pipeline.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/pipeline.py:32>) — `_GOAL_TYPE_GUIDANCE / run_research_stage / run_assessment_stage`; [intelligences/research/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/research/service.py:20>) — `ResearchResult`.

**Current behavior:** Research is lists of strings plus a source list. LLM returns meets_requirements booleans and fit labels; individual requirements do not carry verified official-source linkage or grading context.

**Why it exists:** Early goal counseling before a full opportunity model.

**Global/production risk:** A source list is not proof for each eligibility statement; English admissions template centers GPA/IELTS/GRE.

**Decision:** Refactor incrementally.

**Permanent replacement:** Versioned requirement observations carry exact source span, retrieval time and verification state. Unsupported comparisons return unknown; separate eligibility constraints from semantic fit explanation.

**Migration risk:** No current numeric university match engine was found. Introduce only requirements needed by existing goal analysis, not an entire speculative marketplace.

**Tests required:** Search snippets disagree; unofficial result; stale deadline; unsupported equivalency; requirement absent from source.

**Evidence:** Static code trace

### Acceptable deterministic behavior to preserve

- **Kernel assertion and confidence policy** — `kernel/evidence/assertion.py:is_vault_eligible`, `kernel/policy/verifier.py:policy_decision`: reject nonstudent/nonasserted claims, require confirmation and apply confidence policy. Purpose: write safety. Keep the boundary, fix D01/D03 so all writers obey it. Thresholds are configurable policy candidates for calibration, not admission-fit intelligence. Migration risk: inadvertently relaxing gates. Tests: every source, assertion and sensitivity combination.
- **Authentication and ownership** — `interfaces/api/dependencies.py`, `platform/security/auth/jwt.py`, owned document/conversation/goal queries and signed-storage prefix check. Purpose: tenant isolation. Keep deterministic. No cross-user exploit was established here. Tests: foreign IDs, invalid issuer/audience/signature, expired credentials and deleted account.
- **Document provenance exclusions** — generated-source/SOP restrictions and explicit review are legitimate evidence policy. Keep, while distinguishing classified type from verified issuer. Tests: generated artifact never becomes self-authenticating evidence.
- **ISO codes and phone parsing** — `domains/student/normalization/geo.py:coerce_country` and `phone.py`: standards-backed parsing is useful. A PK example, the ZIP magic bytes PK, or mentioning Urdu in a global extraction prompt is not itself harmful hardcoding. Country mention → destination inference in boosters is harmful; keep those concerns separate. Tests: all supported ISO codes, ambiguous geographic names and international phone formats.
- **Queue state, retry arithmetic and locks** — retain durable queues, bounded attempts and SKIP LOCKED; correct their concurrency protocol under D05–D07. Do not replace scheduling guarantees with an LLM.
- **Database invariants and domain lifecycle enums** — ownership, foreign keys, accepted/pending/deleted states and version fields stay deterministic. Add missing atomicity/uniqueness; do not make states open-ended merely for “globality.”
- **Resource/token/time limits and proposed-only tasks** — keep deterministic budgets, source-size limits, no duplicate side effects and review before irreversible actions. Fix limit enforcement timing rather than remove limits.
- **Memory tenant/status filters, sensitive-field exclusion and unconfirmed-claim labels** — keep regardless of reranker. Ranking metadata is not a substitute for evidence; repeated observations must not bypass confirmation.

No manually invented GPA admission cutoff or numeric university-fit percentage engine was found. GPA <= 4 in onboarding/boosters is a data-model bug, not an eligibility score. The current LLM meets_requirements output still needs the stronger provenance boundary in S16.

## C. Other defects and production risks

These are additional findings, not reasons to remove deterministic safeguards.
### D01 — High: Pending candidates can mutate canonical truth

**File/function:** [domains/student/typed_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/typed_apply.py:301>) — `_apply_education_one / apply_typed_candidate`; [kernel/evidence/vault_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/kernel/evidence/vault_apply.py:31>) — `apply_vault_candidate / process_candidates`; [domains/student/person/profile_snapshot.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/person/profile_snapshot.py:84>) — `load_typed_profile_records`.

**Current behavior:** Pending education calls update the real row and merely return status='pending'. Sparse pending writes supersede the old active row before confirmation; other typed resources also lack a common proposal store.

**Why it exists:** Typed profile enrichment reuses write paths for pending and accepted facts.

**Global/production risk:** A safety gate changes the label but does not prevent mutation. Counselor/goal snapshots can see unconfirmed data or lose valid active truth.

**Decision:** Refactor first.

**Permanent replacement:** Persist pending observations separately. Only confirmed/accepted domain commands mutate canonical rows; apply and confirmation are atomic and version-checked.

**Migration risk:** Migrate existing pending data conservatively using history; some overwritten values cannot be reconstructed without evidence.

**Tests required:** Pending GPA must leave accepted GPA unchanged; pending sparse correction retains old active value; approve/reject idempotence; concurrent confirmation.

**Evidence:** Mocked persistence probe: pending GPA changes 3.0 to 1.7; pending city supersedes existing active row.

### D02 — High: Two goal writers and an unsafe legacy fallback

**File/function:** [domains/student/typed_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/typed_apply.py:590>) — `apply_typed_candidate / _upsert_career_goal`; [intelligences/counselor/orchestrator.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/orchestrator.py:560>) — `_capture_goal / finish_intelligence`; [domains/goals/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/goals/service.py:326>) — `upsert_goal_from_anchors`.

**Current behavior:** Semantic goal resolver writes first; career_interest typed application separately creates GENERAL goals. On any GoalService exception legacy fallback can rename the newest goal.

**Why it exists:** Compatibility with earlier single-goal storage.

**Global/production risk:** Duplicate goals, active-pointer changes and silent overwriting after partial failure. Title-only GENERAL anchors have no score-bearing identity fields.

**Decision:** Remove duplicate authority and fallback.

**Permanent replacement:** One GoalService command path from grounded goal observations; career interest is an observation, not an independent active-goal write. On failure rollback/defer/retry.

**Migration risk:** Preserve valid secondary goals; audit existing duplicates and title mutations before repair.

**Tests required:** Resolver plus career candidate in same turn; repeated title; failure after partial write; existing unrelated goal; DB error.

**Evidence:** Static code trace

### D03 — High: Extraction failure is acknowledged as completed work

**File/function:** [intelligences/counselor/orchestrator.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/orchestrator.py:466>) — `finish_intelligence / _capture_goal`; [intelligences/counselor/followup.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/followup.py:74>) — `run_intelligence_followup`; [interfaces/workers/intelligence.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/interfaces/workers/intelligence.py:21>) — `run_intelligence_worker_once`.

**Current behavior:** finish_intelligence catches extraction failures and returns task processing; followup marks run completed, worker marks job done.

**Why it exists:** Protect student reply from background failure.

**Global/production risk:** Timeout loses the learning event instead of retrying. Broad catches around DB mutations can also leave a failed transaction or partial staged changes.

**Decision:** Refactor.

**Permanent replacement:** Structured per-stage outcome; retryable failures propagate to worker after rollback. Chat remains independent, but background completion reflects actual stage success.

**Migration risk:** Replay by source-event ID so retries do not duplicate facts, goals, memory or tasks.

**Tests required:** Injected timeout/schema failure; DB flush failure; partial success; job retry reaches completion once.

**Evidence:** Local mocked TimeoutError returned from finish_intelligence without raising.

### D04 — High: Account deletion has a transaction conflict and incomplete cleanup

**File/function:** [interfaces/api/auth.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/interfaces/api/auth.py:335>) — `delete_account`; [domains/student/person/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/person/service.py:307>) — `get_person_by_auth / soft_delete_person_data`.

**Current behavior:** Lookup opens a session transaction; soft_delete_person_data then enters session.begin(). Cleanup exceptions are swallowed and identity deletion continues. Even successful cleanup omits conversations/messages, semantic memory, events, documents/storage, person jobs and tasks; Person is soft-deleted, so FK cascades do not purge them.

**Why it exists:** Best-effort application cleanup before auth deletion.

**Global/production risk:** API can report deletion success while retaining student content and queued processing. Workers load Person by ID without a deleted_at check.

**Decision:** Refactor first.

**Permanent replacement:** Durable deletion workflow: tombstone and block all reads/writes, cancel/fence jobs, purge all owned data/storage under explicit retention policy, revoke identity, retry failed steps and report incomplete until complete.

**Migration risk:** Do not run a blanket purge during audit. Include in-flight workers, object paths, backups/retention decisions and provider-failure recovery.

**Tests required:** Existing transaction; DB unavailable; provider fails; retry deletion; worker in flight; no retained content in active stores.

**Evidence:** Local AsyncSession probe reproduced InvalidRequestError with an existing transaction; cleanup scope verified statically.

### D05 — High: CV onboarding competes with the queued document worker

**File/function:** [workflows/onboarding/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/workflows/onboarding/service.py:132>) — `OnboardingService.ingest_cv`; [intelligences/documents/ingest.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/ingest.py:24>) — `create_document_upload`; [intelligences/documents/workers/analysis_worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/workers/analysis_worker.py:53>) — `claim_next_job / process_document_job`.

**Current behavior:** Upload commits a pending extraction job, then onboarding calls process_document_job directly without claiming or marking it processing.

**Why it exists:** Finish CV onboarding immediately.

**Global/production risk:** Normal document worker can claim the same committed pending job while onboarding runs; duplicate model calls, reconciliation and writes can follow.

**Decision:** Refactor.

**Permanent replacement:** Use the same atomic claim path for inline processing or make onboarding wait on a durable job with bounded response/polling.

**Migration risk:** Preserve user-visible onboarding state while moving extraction off request; do not double-schedule existing jobs.

**Tests required:** Barrier-controlled inline/worker race; upload retry; disconnect; unreadable CV; single analysis application.

**Evidence:** Static code trace

### D06 — High: Leases do not fence expired workers

**File/function:** [platform/jobs/lease.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/platform/jobs/lease.py:12>) — `reclaim_expired_leases`; [platform/jobs/queue.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/platform/jobs/queue.py:89>) — `claim_next_person_job / mark_job_done`; [intelligences/goals/worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/worker.py:46>) — `claim_next_goal_job`; [intelligences/documents/workers/analysis_worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/workers/analysis_worker.py:53>) — `claim_next_job`.

**Current behavior:** 600-second leases are reset to pending without heartbeat or attempt-generation fencing. Claim queries do not exclude attempts already over the retry maximum.

**Why it exists:** Recover abandoned jobs after restart.

**Global/production risk:** A slow still-running worker can overlap a reclaimed attempt and write stale results. Repeated crashes can exceed the intended attempt budget.

**Decision:** Refactor.

**Permanent replacement:** Lease owner/generation, heartbeat and fenced completion/write commands; exhausted leases become terminal; keep SKIP LOCKED and existing claim locks.

**Migration risk:** DDL and worker rollout must tolerate old workers; no claim rewrite without real PostgreSQL race tests.

**Tests required:** Long job > lease; expired worker completes after replacement; repeated crash; restart; max attempts.

**Evidence:** Static code trace

### D07 — High: Durable writes lack shared serialization and event idempotency

**File/function:** [kernel/evidence/vault_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/kernel/evidence/vault_apply.py:31>) — `apply_vault_candidate`; [domains/student/typed_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/typed_apply.py:112>) — `_find_education_match`; [domains/goals/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/goals/service.py:412>) — `enqueue_goal_intelligence_job / activate_goal`; [platform/jobs/queue.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/platform/jobs/queue.py:52>) — `enqueue_intelligence`; [domains/conversations/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/conversations/service.py:133>) — `begin_chat_turn / get_or_create_person_conversation`; [domains/student/person/models.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/person/models.py:188>) — `VaultValue`.

**Current behavior:** Chat jobs serialize per person; document jobs per document and goal jobs per goal use different locks. Vault active values lack a unique active-key constraint; goal enqueue is select-then-insert. Chat has no request idempotency key; followup writes commit separately from job completion.

**Why it exists:** Local transactions and per-queue locks appeared sufficient.

**Global/production risk:** Cross-queue/manual writes race; retries after committed effects duplicate evidence, goals or messages. First concurrent chats can create multiple active conversations.

**Decision:** Refactor.

**Permanent replacement:** Source-event operation keys and database uniqueness; one per-person/revision write protocol across all ingress; atomic outbox; optimistic version checks and short transactions.

**Migration risk:** Dedupe legacy rows before adding constraints; retain history and handle conflicts explicitly.

**Tests required:** Two documents plus chat same student; two enqueues; commit then crash; duplicate HTTP request; first-chat race; multiple processes.

**Evidence:** Static code trace

### D08 — High: Goal refresh can remain stale or publish a stale result as ready

**File/function:** [domains/goals/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/goals/service.py:42>) — `VAULT_FIELDS_THAT_AFFECT_GOALS / mark_intelligence_stale_for_vault_update`; [intelligences/goals/worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/worker.py:139>) — `process_goal_job / _save_intelligence`; [intelligences/counselor/context.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/context.py:160>) — `build_counselor_context`.

**Current behavior:** Refresh map omits education.gpa and education.program. A processing job suppresses another enqueue; worker saves its old snapshot with no revision check. Context uses intel.status even if goal.intelligence_status is stale.

**Why it exists:** Avoid unnecessary research and duplicate jobs.

**Global/production risk:** A GPA correction may never refresh assessment; a change during analysis can be overwritten by a ready result computed before the change.

**Decision:** Refactor.

**Permanent replacement:** Track input profile/goal/research revisions. Mark stale atomically, coalesce jobs by required revision and refuse stale publication. Derive dependencies from consumed fields rather than semantic type tables.

**Migration risk:** Keep old brief labelled stale until replaced; profile and research timestamps must be distinct.

**Tests required:** GPA/program update; change during processing; goal switch; multiple goals; stale intel row versus goal flag.

**Evidence:** Static code trace

### D09 — Medium: Pending memory can supersede accepted memory and retries increase certainty

**File/function:** [domains/memory/formation.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/memory/formation.py:79>) — `memory_key_for / merge_drafts / apply_draft / drafts_from_turn`.

**Current behavior:** Every vault-eligible item shares a field-level semantic key; changed pending content supersedes the accepted record. Multiple education/work items collapse by field. Same-content replays increase recurrence/confidence and candidate can become active at three repeats.

**Why it exists:** Compact versioned memory.

**Global/production risk:** Memory disagrees with accepted Vault or forgets distinct records; retry count resembles independent corroboration. claim: conflicts retain explicit unconfirmed formatting, which is a useful safeguard.

**Decision:** Refactor.

**Permanent replacement:** Key memory by entity and evidence identity; keep accepted versus pending lifecycles separate; only new independent evidence changes corroboration.

**Migration risk:** Retain previous versions and unconfirmed-claim wording; no automatic truth promotion from repetition.

**Tests required:** Pending versus active same field; two degrees; replay same message three times; truly independent corroboration.

**Evidence:** Static code trace

### D10 — High: Education record matching can overwrite a different qualification

**File/function:** [domains/student/typed_apply.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/student/typed_apply.py:112>) — `_find_education_match / _apply_education_fields / _apply_education_one`; [intelligences/documents/pipeline.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/pipeline.py:79>) — `_existing_belief`.

**Current behavior:** Institution-only match returns a single row before degree/year comparison; bare grade chooses most recently updated row. New row defaults to completed.

**Why it exists:** Avoid duplicate education records.

**Global/production risk:** Two degrees at one institution merge or trigger multiple-row error; unscoped grade updates wrong qualification; incomplete study can become completed.

**Decision:** Refactor.

**Permanent replacement:** Stable qualification IDs and entity-resolution proposals using institution, award, dates and source association; ambiguous grade attaches to an unresolved observation.

**Migration risk:** Split only with evidence; preserve original row IDs/history for consumers.

**Tests required:** Two degrees same institution; bare grade after correction to older degree; institution unknown; current/enrolled status.

**Evidence:** Static code trace

### D11 — Medium: Research and stage failures have incomplete provenance and status

**File/function:** [intelligences/goals/pipeline.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/pipeline.py:65>) — `_llm_json / run_full_pipeline / build_counselor_brief`; [intelligences/goals/worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/worker.py:139>) — `process_goal_job`; [intelligences/research/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/research/service.py:40>) — `research_query`.

**Current behavior:** Open JSON dictionaries are accepted; malformed gap/plan shapes fail later. Refresh branch marks ready even when assessment returns _error; gaps/plan failure returns empty lists. Research refresh reuses prior research without expiry. Blocking-gaps expression slices joined text to three characters.

**Why it exists:** Graceful partial output and reduced calls.

**Global/production risk:** Unavailable stages look like no gaps, stale sources appear fresh, and briefs lose blockers. Research options are copied into goal target_universities, mixing suggestions with intention.

**Decision:** Refactor.

**Permanent replacement:** Typed stage outcomes with error/degraded flags; per-claim provenance and expiry; validate list items and dependency indices; separate suggested opportunities from user targets; correct brief slicing.

**Migration risk:** Preserve known-good stage data but retain original timestamps and stale status.

**Tests required:** Malformed list item; assessment timeout on refresh; no-gap versus failed-gap; stale research; options not committed targets; three full blocker items.

**Evidence:** Static code trace

### D12 — Medium: Network calls and document parsing extend database transactions

**File/function:** [intelligences/goals/worker.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/goals/worker.py:139>) — `process_goal_job`; [intelligences/documents/pipeline.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/pipeline.py:113>) — `run_document_analysis`; [intelligences/documents/providers/native.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/providers/native.py:14>) — `NativeDocumentProvider.digitize`; [domains/documents/text.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/documents/text.py:29>) — `pdf_page_texts / _docx_text`; [interfaces/api/chat.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/interfaces/api/chat.py:115>) — `chat_stream`.

**Current behavior:** Goal/document sessions remain open from DB reads/writes across provider work. Native PDF/XML work runs synchronously inside async methods. SSE holds a session while model streams.

**Why it exists:** Straightforward orchestration.

**Global/production risk:** Long-lived connections/locks under load, blocked event loop when workers share API, and no overall wall-clock turn/job budget despite provider timeouts.

**Decision:** Refactor.

**Permanent replacement:** Load immutable snapshots, release transaction, run bounded external work, then version-check/apply in a short transaction. Isolate CPU parsing in bounded processes; add total deadlines/cancellation.

**Migration risk:** Snapshot approach requires D06–D08 revision protection; don't simply release locks and allow stale writes.

**Tests required:** Pool saturation; slow provider; large PDF; API colocated workers; drip-fed stream; cancellation.

**Evidence:** Static code trace

### D13 — High: Upload limits and storage lifecycle do not cover failure paths

**File/function:** [interfaces/api/documents.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/interfaces/api/documents.py:85>) — `upload_document`; [intelligences/documents/security/validation.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/security/validation.py:13>) — `sniff_mime / validate_upload_bytes`; [intelligences/documents/security/scanner.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/security/scanner.py:16>) — `scan_bytes`; [intelligences/documents/ingest.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/ingest.py:24>) — `create_document_upload`; [domains/documents/text.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/domains/documents/text.py:54>) — `_docx_text / pdf_page_texts`.

**Current behavior:** Entire upload is read before checking size; .txt suffix can bypass sniffing; DOCX XML expands before output truncation and native PDFs iterate all pages. Scanner default reports clean with no scan. Object upload precedes DB commit without compensation.

**Why it exists:** Simple supported-format ingestion with a future scan hook.

**Global/production risk:** Authenticated resource exhaustion and orphaned storage after commit failure; 'clean' implies a check that did not occur. Deployment request limits were not verified.

**Decision:** Refactor.

**Permanent replacement:** Bound streamed reads, compressed expansion/page/pixel limits and parser time; represent not_scanned honestly; quarantine as required by deployment policy; durable object cleanup/compensation and opaque storage names.

**Migration risk:** Enforce realistic limits with clear retry paths; scanner requirements are a product policy decision, not an excuse to disable ownership gates.

**Tests required:** Oversize/chunked request; zip expansion; many-page PDF; .txt binary; storage succeeds/DB fails; scan unavailable.

**Evidence:** Static code trace; no malicious files sent and no deployment exploit attempted.

### D14 — Medium: Cache lifetime and invalidation omit non-Vault state

**File/function:** [intelligences/counselor/context.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/context.py:138>) — `_profile_cache / build_counselor_context / invalidate_counselor_cache`.

**Current behavior:** Process-global cache has no size bound or TTL. Cached facts include goals but key only uses Vault version; invalidation is local to a process.

**Why it exists:** Avoid repeated profile reads.

**Global/production risk:** Memory grows with unique users; another worker/process can change goal or identity without invalidating cached facts.

**Decision:** Refactor.

**Permanent replacement:** Bound cache; key by relevant revisions or cache only Vault-derived data; invalidate deletion and all matching write events.

**Migration risk:** Do not remove caching without latency measurements.

**Tests required:** Two API workers; goal-only change; deletion; many students; local identity change.

**Evidence:** Static code trace

### D15 — Medium: Streaming and non-streaming have separate orchestration outcomes

**File/function:** [interfaces/api/chat.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/interfaces/api/chat.py:115>) — `chat_stream`; [intelligences/counselor/orchestrator.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/orchestrator.py:484>) — `iter_reply_tokens`; [intelligences/counselor/followup.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/counselor/followup.py:128>) — `handle_user_message`; [intelligences/planner/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/planner/service.py:8>) — `plan_next_actions`.

**Current behavior:** SSE manually builds state and accesses orchestrator internals; no terminal error/cancellation handler surrounds token generation and final persistence. It sets assistant_result=None and never fills task proposals; planner only forwards proposals.

**Why it exists:** Low-latency text streaming.

**Global/production risk:** Client sees partial tokens but no persisted assistant/job after disconnect; pending run can remain. Structured action/task behavior differs by entry path and is currently limited.

**Decision:** Refactor.

**Permanent replacement:** Shared turn coordinator with streamed events, durable turn ID and explicit failed/cancelled/completed state; separate typed intervention/action outputs from prose. Keep proposed tasks nonexecuting.

**Migration risk:** Define whether cancelled turns should still be learned; do not invent an external-action executor.

**Tests required:** Disconnect before/after token; provider fails midstream; persist failure; equivalent streaming/non-stream task request.

**Evidence:** Static code trace

### D16 — Medium: Verification resolution is mutable and can claim application without checking result

**File/function:** [intelligences/documents/verification/service.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/src/pai/intelligences/documents/verification/service.py:104>) — `resolve_case / close_open_cases_for_fields`.

**Current behavior:** resolve_case accepts already-resolved cases, applies fact, ignores returned apply outcome and labels it applied_user_confirmed; no closed-case idempotency/version check.

**Why it exists:** Simple student review endpoint.

**Global/production risk:** Retries or conflicting resolutions can reapply stale facts, and a rejected typed value can still be labelled applied.

**Decision:** Refactor.

**Permanent replacement:** Atomic versioned resolution command with ownership, open-state validation, idempotency and checked apply results.

**Migration risk:** Keep immutable decision history and allow explicit new review instead of overwriting old resolution.

**Tests required:** Double-click; conflicting accept/reject; missing fact; typed apply rejected; old document after new correction.

**Evidence:** Static code trace

### D17 — Medium: CI cannot enforce this architecture today

**File/function:** [../pyproject.toml](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/pyproject.toml>) — `pytest configuration`; [../tests/conftest.py](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/tests/conftest.py:303>) — `postgres_ready / _truncate_all`; [../README.md](<C:/Users/MSA/Desktop/PAI-main-b-end/PAI-main-b-end/README.md>) — `Validation`; `root .github directory (absent)` — `absent`.

**Current behavior:** No checked-in GitHub Actions workflow found. DB availability and migration failures both skip integration tests. Lockfile, packaging includes and substantial unit tests exist.

**Why it exists:** Local development tolerates missing PostgreSQL.

**Global/production risk:** A green unit run can hide broken migrations and write/concurrency paths. Live semantic model quality is largely outside mocked tests.

**Decision:** Refactor validation.

**Permanent replacement:** CI with isolated PostgreSQL+pgvector, mandatory migrations and race tests, locked install/build, lint baseline and opt-in semantic evaluations. Fail on CI DB/migration setup errors.

**Migration risk:** Never point truncating test fixtures at production; do not treat 47 skipped tests as passing integrations.

**Tests required:** Fresh migration; supported upgrade; wheel import/package data; concurrent workers; multilingual semantic evaluations.

**Evidence:** Static code trace

## D. Target global architecture

Keep the current top-level packages and evolve their contracts:

**Raw message/document/source → semantic observation → evidence validation → domain command → canonical record → versioned derived context.**

Turn understanding reads original language and relevant recent state. It emits intent, relevant goal IDs, uncertainty/commitment, need for external evidence and intervention proposal. It may reuse the existing model request or run a bounded fast model; first measure the tradeoff. It does not write facts or authorize actions.

A common observation envelope should include source event/version, verbatim evidence offsets, subject, assertion, temporal state, original value, optional normalized value, confidence and provenance. Subject/temporality/meaning are model proposals, not trusted just because JSON validates. Deterministic checks enforce ownership, references, approved write policy and consistency; ambiguity remains an observation until resolved.

Academic records need qualification identity, original name, status/period, optional level/field/framework/country mapping and mapping evidence. A grade includes native representation, scale/direction/context when known; equivalency is a separate sourced claim. An ordinary “unknown” is valid, not a validation failure to fix by guessing.

Vault owns current structured truth; Memory owns contextual observations with provenance; Goals own intended future outcomes and explicit parallel pursuits; Journey owns append-only change events; Documents own source evidence/version/review. No fallback crosses these ownership boundaries.

Opportunity requirements can initially be added to existing goal research: source span, official authority state, retrieval/expiry time, typed operator/value/unit/context, and unknown evaluation. A future opportunity model can add organization, program, cost, funding, location, deadlines and outcomes using the same sources. Do not implement a giant DSL, per-country modules, or matching platform before a real supported requirement needs it.

For ambiguous human choices, return semantic explanations with known facts and uncertainty. For eligibility, use only verified applicable constraints and a three-state result. A model's confidence is not admissions probability.

## E. Ordered refactor plan

**PR 0 — Reproducible baseline and constitution.** Preserve current latency work. Add agreed AGENTS.md, failing regressions for probes, and CI PostgreSQL isolation. Establish a clean baseline for the actual working tree before review.

**PR 1 — Truth gates.** Fix D01, D02, D03 together as small dependent changes: pending observation store, one goal writer, rollback/retry. Eliminate booster authority on negated/attributed/unclassified claims; retain observations when model unavailable. These are more urgent than optimizing discovery.

**PR 2 — Grade correctness.** Fix all S02/S03 paths, onboarding contract, native-grade comparisons and consumer displays. Add missing refresh dependencies immediately as containment. Perform a read-only evidence audit of old grades; separately review repair batches.

**PR 3 — Deletion and document processing ownership.** Fix D04 and D05 with durable deletion and a single claim protocol. These can be separate focused PRs; privacy correction should not wait for Turn Understanding.

**PR 4 — Retry/concurrency correctness.** D06–D08: source-event uniqueness, fenced leases, shared revisioned writes, required-profile revision on jobs, short transaction ownership and atomic outbox. Add constraints only after duplicate-data audit.

**PR 5 — Turn Understanding.** Add schemas/intelligence behind shadow mode, then replace routing/stance/pressure. Preserve original languages and explicit unknown fallback; reuse understanding across counselor and background extraction rather than multiply classifiers.

**PR 6 — Universal qualifications and document interpretation.** Add lossless fields and entity resolution, fix D10 and S11–S14. Versioned reference mappings; no country branches. Keep aliases as evidence-backed data, never as an exhaustive global ontology.

**PR 7 — Best next intervention.** Replace discovery weights and question-mark tracking after meaningful goal/qualification inputs exist. Retain policy filters and actual-question anti-repetition.

**PR 8 — Research requirements and derived intelligence.** S16/D11: typed stage outcomes, claim provenance, official-source verification state, freshness, stale-result rejection and separation of suggestions from goal intent. Fix three-character blocker truncation immediately if desired.

**PR 9 — Memory and operational reliability.** D09/D12–D17: entity/evidence-aware memory, evaluated optional reranker, bounded caches/parser work, storage compensation, SSE lifecycle consistency and idempotent review. Some high-impact upload limits should be pulled into PR 3 rather than wait.

Every PR needs a concrete diff, regression tests and measured impact. No automatic historical data rewrite. Rollback switches may disable a new semantic service but must never reactivate the unsafe writer or GPA guesser.

## F. Test plan

### Deterministic regression suite

- Test evidence and write policy as invariants across chat, manual onboarding, documents, student review and worker retries. A pending proposal never changes accepted truth.
- Grade input classes: unknown 8.2, explicit 8.2/10, 3.4/4, 4.5/5, 82/100, 2:1, German 1.7, letters, zero, malformed/nonfinite values. Preserve native representation; do not assert linear equivalency.
- Qualification identity: multiple awards at one school, same award at different schools, incomplete/planned records, unknown original names, unscoped grades.
- Semantic boundary fixtures include negation, attribution, uncertainty, corrections, hypothetical/future statements, short answers and conflicting source data. Mocked model output tests contract handling; they do not prove multilingual comprehension.
- Verify same source event twice produces no extra goal, memory recurrence, evidence, task or analysis effect.
- Confirmation/rejection must be owned, versioned and idempotent; failed application must not mark a case applied.

### Multilingual semantic evaluation

Use English, mixed Roman Urdu/English, Arabic, French, Chinese, Portuguese and Japanese, with typos and switches mid-sentence. Include education-system fixtures from Pakistan, India, UK, USA, Germany, France, Nigeria, UAE, China, Brazil and Japan. They are held-out input diversity, not branches in production code.

Score subject/assertion/temporal fidelity, scale abstention, routing recall, correct goal relation, useful intervention, original-value preservation and evidence citation. Pair equivalent paraphrases; include “I have a 2.1 degree,” “我的平均成绩是87分,” “Baccalauréat général,” and “A levels … Ausbildung.” Assert ambiguity when context is insufficient; never require a guessed country/scale to pass.

Set acceptance targets against a labelled baseline and inspect errors by language/system; do not invent “permanent” thresholds without evaluation. Keep model/prompt/reference version and latency/cost in evaluation output.

### PostgreSQL concurrency and crash suite

Use barriers and separate real connections/processes. Test same student across chat/document/manual writes; same goal enqueue; inline CV versus worker claim; expiry and late completion; crash after domain commit before acknowledgement; correction during goal analysis; deletion during extraction; repeated external request after timeout. Assert canonical row counts, required revisions, lease ownership, active pointers, remaining jobs and evidence IDs.

Run migrations on empty DB and representative historical duplicates; constraints must reject invalid active-state combinations. Fail CI if database or migration setup is unavailable.

### Provider and operational failures

Inject model timeout/refusal/truncation/schema drift, missing credentials, search failure, stale source, embedding failure, storage success/DB failure, parser resource exhaustion, SSE disconnect and partial-stream failure. Verify bounded time/resources, retryable stage status and preserved accepted truth.

Load-test 50-turn histories, multiple active goals, many students/cache eviction, worker backlog and shared API/worker deployment. Record p50/p95 reply and first-token latency, total model calls, extraction backlog age, stale-result drops, retries/dead letters and DB pool wait. Existing token/network caps and timing spans are a baseline, not proof of end-to-end latency bounds.

### Security and privacy validation

Test cross-person IDs on every read/write/review endpoint, signed-object ownership, deleted-user rejection and purge completion. Exercise prompt injection in uploaded text/search snippets as untrusted data, with no ability to bypass evidence/write gates. Inspect raw provider-error previews and persisted evidence for sensitive content; field-level Vault encryption does not imply every document, log or memory is encrypted/redacted. No concrete cross-tenant exploit was proven in this pass.

## G. File-by-file implementation plan

Paths below are relative to src/pai unless explicitly marked new/root. No changes in this section have been made.

- **kernel/contracts/schemas.py** — add shared observation/source identity, typed grade and explicit proposal state; keep compatibility adapters versioned.
- **kernel/evidence/candidate_eval.py, vault_apply.py; kernel/policy/verifier.py** — one eligibility/policy path, pending isolation, atomic confirmation and checked application results; remove unchecked “already reconciled” authority from unvalidated call paths.
- **domains/student/typed_apply.py** — accepted-only typed commands, qualification resolution, no completed default, no legacy goal writer, preserve scale/history.
- **domains/student/person/models.py; new Alembic migration** — proposal/evidence links, qualification/grade metadata, write revisions and appropriate unique constraints after data audit.
- **domains/student/vault/service.py, completion.py, catalog.py** — consistent manual/model write protocol; derived completeness from actual record coverage; optional original labels and no semantic admission rankings.
- **intelligences/vault/boosters.py, sources/chat.py, sources/document.py, merge.py, normalize.py, ground.py, formation.py** — remove bypassing semantic guesses; preserve attribution/assertion/temporality, source IDs and Unicode evidence. Merge only after canonical key normalization and preserve conflicting alternatives.
- **intelligences/documents/normalization/gpa.py; extraction/schemas/transcript.py; reconciliation/comparators.py; pipeline.py** — native grade contract everywhere, no scale assumptions/equivalence and record-specific existing beliefs.
- **workflows/onboarding/contracts.py, catalog.py, service.py** — grade scale/context input and original test/qualification labels; single document claim path; review-required extraction stays distinguishable from completion.
- **domains/goals/service.py, models.py; intelligences/goals/resolver.py, worker.py, pipeline.py, integrated.py** — single domain writer, goal-relation command, pending job uniqueness, revision/freshness checks, typed stage outcomes, no research-options overwrite of intent.
- **platform/jobs/models.py, queue.py, lease.py; all three workers** — event idempotency, heartbeat/fencing, bounded attempts, cross-domain write serialization and honest completion.
- **interfaces/api/auth.py; domains/student/person/service.py; new workflows/account_deletion/service.py** — transaction ownership, durable deletion/outbox, all owned stores and object cleanup; workers honor tombstones.
- **new intelligences/understanding/schemas.py and turn.py** — multilingual structured turn interpretation using existing gateway; bounded timeout, versioned output, original evidence and explicit unknown.
- **intelligences/counselor/routing.py, conversation_stance.py, context.py, profile_depth.py, discovery.py, orchestrator.py** — consume understanding once; replace pressure/ladder/weight authority; actual intervention tracking; revisioned bounded cache and neutral failure fallback.
- **intelligences/counselor/followup.py, graph.py, counselor_graph.py; interfaces/api/chat.py** — unify SSE/non-SSE state transitions and error/cancellation handling; separate reply and derived-work outcomes.
- **intelligences/documents/identity/names.py, matcher.py; evidence/grounding.py; normalization/dates.py** — Unicode-preserving comparisons and contextual identity evidence; unknown ambiguous date/order; no invented date precision.
- **intelligences/documents/classification/taxonomy.py, classifier.py, data/taxonomy.json, data/policy.json** — semantic type proposals plus deterministic source-authority policy; labels alone do not verify documents.
- **interfaces/api/documents.py, onboarding.py; intelligences/documents/ingest.py, security/validation.py, security/scanner.py; domains/documents/text.py** — stream/expansion/page limits, honest scan status, bounded parser workers and compensated storage lifecycle.
- **intelligences/documents/verification/service.py; domains/documents/service.py** — versioned review, idempotent resolution, validate applied outcome and refresh derived state after confirmed changes.
- **domains/memory/formation.py, postgres_store.py, service.py; platform/llm/embeddings.py** — entity/evidence keys, verified versus pending lifecycle, replay safety and injected provider boundary; evaluate reranking.
- **intelligences/research/service.py; goals pipeline; capabilities/search/service.py** — per-claim provenance, official-source verification state, expiry and bounded external operations.
- **intelligences/planner/service.py; domains/actions/service.py, models.py** — typed task identity and source-event dedupe; keep proposed-only status and explicit execution boundary.
- **app.py, config.py, interfaces/workers/__main__.py** — readiness covers DB/schema and observable worker health/backlog; total budgets and deployment parser isolation.
- **root AGENTS.md (proposed), .github/workflows/ci.yml (new), tests/conftest.py, affected tests and evaluation fixtures** — persistent rules, disposable DB CI, migration failures fail CI, generic invariant tests, multilingual quality evaluation.
- **README.md, DEVELOPER.md, docs/latency-openai.md** — keep current provider/deployment docs consistent and document actual failure/unknown behavior. pyproject.toml/uv.lock already exist; verify locked build before claiming packaging is broken.

## Proposed engineering constitution for PR 0

PAI is global; Pakistan is the initial validation market. Preserve the current top-level package architecture unless an actual defect requires change.

Models interpret original-language meaning and propose evidence-backed observations, goal relations and interventions. Deterministic code validates ownership, references, schema, arithmetic, consent, policy, lifecycle, idempotency and irreversible actions. A model result is not a write authorization.

Preserve original qualifications, grades, dates and source text. Unknown scale, level, date order, equivalency and requirement remain unknown. Country facts and education-framework mappings belong to versioned reference/source data. Do not move vocabulary heuristics to config and call them semantic intelligence.

Vault owns accepted current truth, Memory contextual observations, Goals future intention, Journey history and Documents evidence. Pending data cannot mutate accepted truth. All domain changes carry source identity and revision. Failure must rollback/defer/retry, never activate a legacy semantic writer.

Before changing behavior: trace callers and persistence, add meaningful regressions, make the smallest coherent change, run checks and state evidence limits. Require real DB concurrency tests for write/queue changes and labelled multilingual evaluation for model behavior. Rollback flags must preserve these guarantees.

## Reproduced probes

1. Short chat “I never lived in Berlin”: no model call; city Berlin reaches candidate outcome accept.
2. “My sister GPA is 3.4”: no model call; bachelor GPA 3.4/4 reaches candidate outcome accept.
3. “CGPA 3.4/5”: no model call; explicit /5 is replaced by /4.
4. “No scholarship please”: scholarship_interest true is proposed pending, despite negation.
5. parse_gpa("CGPA 8.2"): value 8.2, scale 4.0.
6. values_equivalent GPA(80/100, 3.2/4): true without grading-system equivalency evidence.
7. names_match("王伟", "王伟"): ambiguous.
8. parse_date("03/04/2005"): 2005-04-03; invalid ISO 2025-02-30 throws ValueError.
9. classify_rung("Postgraduate Diploma"): master.
10. French tuition question: PERSONAL_ADVICE; should_extract_facts("no"): false.
11. soft_delete_person_data with active AsyncSession transaction: InvalidRequestError, “A transaction is already begun on this Session.”
12. finish_intelligence with mocked extraction TimeoutError: returns normally.
13. _apply_education_one with mocked persistence and pending GPA: actual ORM object changes 3.0 → 1.7, result pending.
14. apply_vault_candidate with mocked persistence and pending city: old active row becomes superseded.

These probes validate the local functions, not live provider understanding or committed PostgreSQL outcomes. The next implementation phase should turn them into retained regression tests and add end-to-end database cases.

## Audit exit

The requested audit artifacts are complete. The implementation is intentionally unstarted. Start with PR 0/PR 1 and the deletion repair; do not start with a broad rewrite or a new matching engine.
