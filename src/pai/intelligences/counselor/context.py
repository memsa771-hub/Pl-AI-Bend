from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.domains.conversations.models import Conversation, Message
from pai.domains.documents.models import Document, VerificationCase
from pai.domains.student.person.models import Person, VaultValue
from pai.domains.student.person.profile_snapshot import load_typed_profile_records
from pai.domains.student.vault.service import VaultService


class CounselorContext(BaseModel):
    """Compact counselor prompt + stay payload. Not the full Person dump."""

    person_id: str
    identity: dict[str, Any] = Field(default_factory=dict)
    goal: str | None = None
    education: str | None = None
    location: str | None = None
    budget: str | None = None
    tests: list[str] = Field(default_factory=list)
    universities: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    missing_critical_fields: list[str] = Field(default_factory=list)
    missing_important_fields: list[str] = Field(default_factory=list)
    enrichment_opportunities: list[str] = Field(default_factory=list)
    top_discovery_candidate: str | None = None
    discovery_reason: str | None = None
    recent_messages: list[dict[str, str]] = Field(default_factory=list)
    relevant_memory: list[str] = Field(default_factory=list)
    active_tasks: list[dict[str, str]] = Field(default_factory=list)
    critical_verifications: list[dict[str, Any]] = Field(default_factory=list)
    active_goal_id: str | None = None
    active_goal_brief: str | None = None
    active_goal_status: str | None = None  # ready | partial | pending | failed
    pending_confirmations: list[str] = Field(default_factory=list)
    career_interest: str | None = None
    decision_signal: str | None = None
    # Advisory per-turn posture (explore/understand/guide). Background guidance
    # for tone and pacing — never a command. See conversation_stance.py.
    conversation_focus: str | None = None

    def profile_block(self) -> str:
        lines = []
        if self.conversation_focus:
            lines.append(f"counselor_focus: {self.conversation_focus}")
        if self.critical_verifications:
            lines.append("CRITICAL VERIFICATION:")
            for row in self.critical_verifications[:4]:
                field = str(row.get("fieldKey") or "fact")
                lines.append(
                    f"{field} disputed. Current: {row.get('existingValue')}. "
                    f"Document: {row.get('incomingValue')}. "
                    + (
                        "Do not make GPA-sensitive recommendations until resolved. "
                        if field.startswith("education.gpa")
                        else "Do not treat this as settled truth until resolved. "
                    )
                    + "Ask the student to resolve this."
                )
        if self.active_goal_brief:
            lines.append(
                f"[ACTIVE GOAL INTELLIGENCE — status:{self.active_goal_status or 'unknown'}]"
            )
            for brief_line in self.active_goal_brief.splitlines():
                stripped = brief_line.strip()
                if stripped:
                    lines.append(f"  {stripped}")
        elif self.goal:
            lines.append(f"goal: {self.goal}")
        if self.decision_signal:
            lines.append(f"decision_signal: {self.decision_signal}")
        if self.career_interest:
            lines.append(f"career_interest: {self.career_interest}")
        if self.education:
            lines.append(f"education: {self.education}")
        if self.location:
            lines.append(f"location: {self.location}")
        if self.budget:
            lines.append(f"budget: {self.budget}")
        if self.tests:
            lines.append("tests: " + "; ".join(self.tests[:6]))
        if self.universities:
            lines.append("universities: " + "; ".join(self.universities[:6]))
        if self.relevant_memory:
            lines.append("memory: " + " | ".join(self.relevant_memory))
        if self.top_discovery_candidate:
            lines.append(f"gaps: {self.discovery_reason or self.top_discovery_candidate}")
        elif self.missing_critical_fields:
            lines.append("gaps: " + ", ".join(self.missing_critical_fields[:4]))
        if self.pending_confirmations:
            lines.append(
                "pending confirmation (ask the student to confirm at most one): "
                + "; ".join(self.pending_confirmations[:3])
            )
        return "\n".join(lines) if lines else "(no stored profile yet)"


_PRESSURE = re.compile(
    r"\b("
    r"peer pressure|social pressure|"
    r"everyone (?:says|is|wants|does|doing)|"
    r"people (?:say|think|want)|"
    r"they want me|"
    r"someone else(?:'s)?|"
    r"my (?:friend|friends|classmates?|cousin|cousins|parents?|family|dad|father|mom|mother|uncle|aunt)|"
    r"parents? (?:want|said)"
    r")\b",
    re.I,
)


def _pressure_signal(*, recent: list[dict], facts: list[str], memory: list[str]) -> str | None:
    blob = " ".join(
        [
            *(str(m.get("content") or "") for m in recent if m.get("role") == "user"),
            *facts,
            *memory,
        ]
    )
    if not blob.strip() or not _PRESSURE.search(blob):
        return None
    return (
        "Stated goal may reflect peer pressure rather than personal fit. "
        "Advise on genuine fit and constraints; do not only chase the named program."
    )


_profile_cache: dict[str, tuple[int, dict[str, Any]]] = {}


def _fact_after(facts: list[str], *prefixes: str) -> str | None:
    for item in facts:
        key = item.split(":", 1)[0].strip().casefold()
        if any(key.startswith(prefix) for prefix in prefixes):
            return item.split(":", 1)[-1].strip() or None
    return None


def _facts_all(facts: list[str], *prefixes: str) -> list[str]:
    out: list[str] = []
    for item in facts:
        key = item.split(":", 1)[0].strip().casefold()
        if any(key.startswith(prefix) for prefix in prefixes):
            value = item.split(":", 1)[-1].strip()
            if value:
                out.append(value)
    return out


async def build_counselor_context(
    session: AsyncSession,
    person: Person,
    *,
    conversation_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    semantic_memory: str = "",
    message: str = "",
) -> CounselorContext:
    """Vault + goals + last messages. No documents, cross-thread, or task fan-out."""
    settings = settings or get_settings()
    if person.vault is None:
        await session.refresh(person, attribute_names=["vault"])
    version = int(getattr(person.vault, "version", 0) or 0) if person.vault else 0
    cached = _profile_cache.get(str(person.id))
    identity = {
        "email": person.email,
        "fullName": person.full_name,
        "preferredName": person.preferred_name,
    }
    if cached and cached[0] == version:
        facts = list(cached[1].get("facts") or [])
        missing = list(cached[1].get("missing") or [])
        raw_missing_critical = list(cached[1].get("raw_missing_critical") or [])
        raw_missing_important = list(cached[1].get("raw_missing_important") or [])
        raw_missing_enrichment = list(cached[1].get("raw_missing_enrichment") or [])
        depth_gaps = list(cached[1].get("depth_gaps") or [])
    else:
        typed_records = await load_typed_profile_records(session, person.id)
        vault_svc = VaultService(settings)
        unified = await vault_svc.get_unified_vault(
            session,
            person,
            include_sensitive=False,
            typed_records=typed_records,
        )
        completion = unified.get("completion") or {}
        sparse = unified.get("sparseFields") or {}
        from pai.domains.journey.service import goal_fact_lines

        facts = await goal_fact_lines(session, person.id)
        facts.extend(build_known_facts(identity=identity, sparse=sparse, typed=typed_records))
        facts = _dedupe_goal_lines(facts)
        raw_missing_critical = list(completion.get("missingCriticalFields") or [])
        raw_missing_important = list(completion.get("missingImportantFields") or [])
        raw_missing_enrichment = list(completion.get("missingEnrichmentFields") or [])
        missing = _advice_gaps(raw_missing_critical)
        from pai.intelligences.counselor.profile_depth import compute_depth_gaps

        _highest = sparse.get("education.highest_level")
        depth_gaps = compute_depth_gaps(
            highest_level=_sparse_value(_highest) if _highest is not None else None,
            educations=typed_records.get("educations") or [],
            work_experiences=typed_records.get("workExperiences") or [],
        )
        _profile_cache[str(person.id)] = (
            version,
            {
                "facts": facts,
                "missing": missing,
                "raw_missing_critical": raw_missing_critical,
                "raw_missing_important": raw_missing_important,
                "raw_missing_enrichment": raw_missing_enrichment,
                "depth_gaps": depth_gaps,
            },
        )
    recent: list[dict[str, str]] = []
    if conversation_id:
        result = await session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.person_id == person.id,
            )
            .order_by(Message.created_at.desc())
            .limit(settings.chat_recent_message_limit)
        )
        rows = list(reversed(result.scalars().all()))
        recent = [{"role": m.role, "content": m.content} for m in rows]
    memory_lines = [
        line.strip(" -")
        for line in (semantic_memory or "").splitlines()
        if line.strip() and not line.strip().startswith("Relevant context")
    ]
    from pai.intelligences.documents.verification.service import list_open_cases, public_case

    cases = await list_open_cases(session, person.id)
    verifications = [public_case(row) for row in cases[:8]]
    known = list(facts[:16])
    for row in reversed(verifications):
        known.insert(
            0,
            f"DISPUTED {row.get('fieldKey')}: current={row.get('existingValue')} document={row.get('incomingValue')}",
        )
    active_goal_id: str | None = None
    active_goal_type: str | None = None
    active_goal_brief: str | None = None
    active_goal_status: str | None = None
    recently_asked_field_key: str | None = None
    recently_asked_at: datetime | None = None
    pending_confirmations: list[str] = []
    if person.vault is not None:
        try:
            pending_rows = await session.execute(
                select(VaultValue.field_key, VaultValue.value).where(
                    VaultValue.vault_id == person.vault.id,
                    VaultValue.status == "pending_confirmation",
                )
            )
            for field_key, value in pending_rows.all():
                preview = value
                if isinstance(preview, (dict, list)):
                    preview = str(preview)[:80]
                elif preview is not None:
                    preview = str(preview)[:80]
                else:
                    preview = "(value pending)"
                pending_confirmations.append(f"{field_key}={preview}")
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).exception(
                "Failed to load pending confirmations (non-fatal)"
            )
    if conversation_id is not None:
        try:
            from pai.domains.goals.service import get_goal_by_id, get_goal_intelligence

            conv = await session.get(Conversation, conversation_id)
            if conv is not None:
                recently_asked_field_key = conv.last_discovery_field_key
                recently_asked_at = conv.last_discovery_asked_at
                if conv.active_goal_id is not None:
                    active_goal = await get_goal_by_id(
                        session, conv.active_goal_id, person.id
                    )
                    if active_goal is not None:
                        active_goal_id = str(active_goal.id)
                        active_goal_type = active_goal.goal_type
                        intel = await get_goal_intelligence(session, active_goal.id)
                        if intel is not None and intel.counselor_brief:
                            active_goal_brief = intel.counselor_brief
                            active_goal_status = intel.status
                        else:
                            active_goal_status = active_goal.intelligence_status or "pending"
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).exception(
                "Failed to load active goal brief (non-fatal)"
            )
    from pai.intelligences.counselor.discovery import explain, select_discovery_candidates

    discovery = select_discovery_candidates(
        missing_critical=raw_missing_critical,
        missing_important=raw_missing_important,
        missing_enrichment=raw_missing_enrichment,
        depth_gaps=depth_gaps,
        message=message,
        goal_type=active_goal_type,
        known_facts=known,
        recently_asked_field_key=recently_asked_field_key,
        recently_asked_at=recently_asked_at,
    )
    top_discovery_candidate = discovery.top.field_key if discovery.top else None
    discovery_reason = explain(discovery.top) if discovery.top else None
    decision_signal = _pressure_signal(recent=recent, facts=known, memory=memory_lines)

    from pai.intelligences.counselor.conversation_stance import compute_stance
    from pai.intelligences.counselor.routing import classify_turn, is_greeting

    prior_assistant_turns = sum(1 for m in recent if m.get("role") == "assistant")
    stance = compute_stance(
        message=message,
        turn_kind=classify_turn(message),
        is_greeting=is_greeting(message),
        has_active_goal=active_goal_id is not None,
        active_goal_status=active_goal_status,
        decision_signal=bool(decision_signal),
        prior_assistant_turns=prior_assistant_turns,
    )
    return CounselorContext(
        person_id=str(person.id),
        identity=identity,
        goal=_fact_after(facts, "current goal"),
        education=_fact_after(facts, "education"),
        location=_fact_after(facts, "current city", "current country", "location"),
        budget=_fact_after(facts, "budget", "funding"),
        tests=_facts_all(facts, "test"),
        universities=_facts_all(facts, "target universit"),
        known_facts=known[:16],
        missing_critical_fields=missing,
        missing_important_fields=discovery.missing_important,
        enrichment_opportunities=discovery.enrichment_opportunities,
        top_discovery_candidate=top_discovery_candidate,
        discovery_reason=discovery_reason,
        recent_messages=recent,
        # Recall already returns at most SEMANTIC_MEMORY_MAX_RESULTS; a second
        # hardcoded slice here silently discarded half of what was configured.
        relevant_memory=memory_lines,
        critical_verifications=verifications,
        active_goal_id=active_goal_id,
        active_goal_brief=active_goal_brief,
        active_goal_status=active_goal_status,
        pending_confirmations=pending_confirmations[:5],
        career_interest=_fact_after(facts, "career interest"),
        decision_signal=decision_signal,
        conversation_focus=stance.focus,
    )


def invalidate_counselor_cache(person_id: uuid.UUID | str) -> None:
    _profile_cache.pop(str(person_id), None)


class PersonContextPack(BaseModel):
    person_id: str
    identity: dict[str, Any] = Field(default_factory=dict)
    applicable_vault_fields: dict[str, Any] = Field(default_factory=dict)
    typed_profile_summary: dict[str, Any] = Field(default_factory=dict)
    vault_completion: dict[str, Any] = Field(default_factory=dict)
    missing_critical_fields: list[str] = Field(default_factory=list)
    # Explicit list the counselor must not re-ask
    known_facts: list[str] = Field(default_factory=list)
    recent_messages: list[dict[str, str]] = Field(default_factory=list)
    # Recent turns from OTHER threads (when clients fragment conversationIds)
    cross_thread_recent: list[dict[str, str]] = Field(default_factory=list)
    conversation_topic: str | None = None
    pending_conflicts: list[str] = Field(default_factory=list)
    relevant_documents: list[dict[str, str]] = Field(default_factory=list)
    active_tasks: list[dict[str, str]] = Field(default_factory=list)
    proposed_tasks: list[dict[str, str]] = Field(default_factory=list)
    applied_vault_changes_turn: list[dict[str, Any]] = Field(default_factory=list)


StudentContextPack = PersonContextPack


_ADVICE_GAPS = frozenset(
    {
        "location.current_city",
        "location.current_country",
        "education.highest_level",
        "education.records",
        "application.study_country",
        "application.goals",
        "identity.current_status",
    }
)


def _advice_gaps(missing: list[str]) -> list[str]:
    return [key for key in missing if key in _ADVICE_GAPS]


from pai.domains.actions.service import list_tasks_for_person


def _sparse_value(entry: Any) -> Any:
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


def _dedupe_goal_lines(lines: list[str]) -> list[str]:
    """Drop a "Career/study goal: X" line whose title is already covered by a
    Current/Previous/Secondary goal line (same underlying pursuit, different
    rendering path) so the same goal is never listed twice in known facts."""

    def _key(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    covered: set[str] = set()
    for line in lines:
        for prefix in ("Current goal", "Previous goal", "Secondary/exploring goal"):
            if line.startswith(prefix):
                _, _, rest = line.partition(": ")
                title = rest.split(" — ")[0].strip()
                if title:
                    covered.add(_key(title))
                break
    out: list[str] = []
    for line in lines:
        if line.startswith("Career/study goal: "):
            title = line[len("Career/study goal: ") :].strip()
            if _key(title) in covered:
                continue
        out.append(line)
    return out


def build_known_facts(
    *,
    identity: dict[str, Any],
    sparse: dict[str, Any],
    typed: dict[str, Any],
) -> list[str]:
    """Human-readable facts already known — counselor must not re-ask these."""
    facts: list[str] = []
    name = identity.get("preferredName") or identity.get("fullName")
    if name:
        facts.append(f"Student name: {name}")

    for edu in typed.get("educations") or []:
        parts = [
            p
            for p in (
                edu.get("degree"),
                edu.get("major"),
                edu.get("institution"),
            )
            if p
        ]
        detail = " / ".join(str(p) for p in parts) if parts else "education record"
        if edu.get("gpa") is not None:
            scale = edu.get("gpaScale") or 4.0
            detail += f", GPA/CGPA {edu['gpa']}/{scale}"
        if edu.get("percentage") is not None:
            detail += f", {edu['percentage']}%"
        facts.append(f"Education: {detail}")

    seen_goal_titles: set[str] = set()
    for goal in typed.get("goals") or []:
        title = goal.get("title")
        if not title or goal.get("status") == "archived":
            continue
        key = re.sub(r"\s+", " ", str(title)).strip().casefold()
        if not key or key in seen_goal_titles:
            continue
        seen_goal_titles.add(key)
        facts.append(f"Career/study goal: {title}")

    for skill in (typed.get("skills") or [])[:12]:
        if skill.get("name"):
            facts.append(f"Skill: {skill['name']}")

    for key, label in (
        ("application.study_country", "Target study country/countries"),
        ("application.career_interest", "Career interest"),
        ("application.target_universities", "Target universities"),
        ("application.admission_cycle", "Admission cycle"),
        ("application.test_scores", "Test scores"),
        ("mobility.preferred_regions", "Preferred regions"),
        ("education.stream", "Education stream"),
        ("education.marks", "Marks"),
        ("education.additional_maths", "Additional Maths"),
        ("location.current_city", "Current city"),
        ("location.current_country", "Current country"),
        ("demographics.gender", "Gender"),
        ("demographics.nationality", "Nationality"),
        ("demographics.date_of_birth", "Date of birth"),
        ("identity.current_status", "Current status"),
        ("social.linkedin_url", "LinkedIn"),
        ("education.highest_level", "Highest education level"),
        ("preferences.preferred_language", "Preferred language"),
        ("preferences.learning_style", "Learning style"),
        ("preferences.communication_style", "Communication style"),
        ("finance.funding_status", "Funding / budget status"),
        ("finance.scholarship_interest", "Scholarship interest"),
        ("mobility.relocation_willingness", "Relocation willingness"),
    ):
        if key in sparse:
            raw = _sparse_value(sparse[key])
            if raw in (None, "", "***"):
                continue
            facts.append(f"{label}: {raw}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in facts:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


async def _load_cross_thread_messages(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    exclude_conversation_id: uuid.UUID | None,
    limit: int = 12,
) -> list[dict[str, str]]:
    """Last N messages across all active threads (helps when clients split chats)."""
    q = (
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.person_id == person_id,
            Conversation.person_id == person_id,
            Conversation.status == "active",
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    if exclude_conversation_id is not None:
        q = q.where(Message.conversation_id != exclude_conversation_id)
    result = await session.execute(q)
    rows = list(reversed(result.scalars().all()))
    return [{"role": m.role, "content": m.content} for m in rows]


async def build_person_context_pack(
    session: AsyncSession,
    person: Person,
    *,
    conversation_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> PersonContextPack:
    settings = settings or get_settings()
    if person.vault is None:
        await session.refresh(person, attribute_names=["vault"])
    vault_svc = VaultService(settings)
    typed_records = await load_typed_profile_records(session, person.id)
    unified = await vault_svc.get_unified_vault(
        session,
        person,
        include_sensitive=False,
        typed_records=typed_records,
    )
    completion = unified.get("completion") or {}
    sparse = unified.get("sparseFields") or {}
    limit = settings.chat_recent_message_limit
    recent: list[dict[str, str]] = []
    topic: str | None = None
    if conversation_id:
        conv = await session.get(Conversation, conversation_id)
        if conv is None or conv.person_id != person.id:
            conv = None
            topic = None
            recent = []
        else:
            topic = conv.topic
            result = await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.person_id == person.id,
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))
            recent = [{"role": m.role, "content": m.content} for m in rows]
    cross = await _load_cross_thread_messages(
        session,
        person.id,
        exclude_conversation_id=conversation_id,
        limit=min(12, limit),
    )
    docs = await session.execute(
        select(Document)
        .where(
            Document.person_id == person.id,
            Document.deleted_at.is_(None),
        )
        .order_by(Document.created_at.desc())
        .limit(5)
    )
    doc_summaries = [
        {
            "id": str(d.id),
            "filename": d.original_filename,
            "type": d.document_type or "other",
            "source": d.source_type,
            "attention": d.verification_status,
        }
        for d in docs.scalars()
    ]
    pending_conflicts: list[str] = []
    if person.vault is not None:
        pending_rows = await session.execute(
            select(VaultValue.field_key).where(
                VaultValue.vault_id == person.vault.id,
                VaultValue.status == "pending_confirmation",
            )
        )
        pending_conflicts = list(pending_rows.scalars().all())
    tasks = await list_tasks_for_person(session, person.id)
    active_tasks = [
        {"id": str(t.id), "title": t.title, "status": t.status} for t in tasks if t.status != "proposed"
    ]
    proposed_tasks = [
        {"id": str(t.id), "title": t.title, "status": t.status} for t in tasks if t.status == "proposed"
    ]
    identity = {
        "email": person.email,
        "fullName": person.full_name,
        "preferredName": person.preferred_name,
    }
    from pai.domains.journey.service import goal_fact_lines

    known = await goal_fact_lines(session, person.id)
    known.extend(build_known_facts(identity=identity, sparse=sparse, typed=typed_records))
    known = _dedupe_goal_lines(known)
    open_cases = await session.execute(
        select(VerificationCase.field_key).where(
            VerificationCase.person_id == person.id,
            VerificationCase.status.in_(("open", "presented")),
        )
    )
    disputed = list(open_cases.scalars().all())
    pending_conflicts = list(dict.fromkeys([*pending_conflicts, *disputed]))
    for key in disputed:
        known.append(f"DISPUTED {key}: do not treat as settled truth")
    return PersonContextPack(
        person_id=str(person.id),
        identity=identity,
        applicable_vault_fields=sparse,
        typed_profile_summary=typed_records,
        vault_completion=completion,
        missing_critical_fields=_advice_gaps(completion.get("missingCriticalFields") or []),
        known_facts=known,
        recent_messages=recent,
        cross_thread_recent=cross,
        conversation_topic=topic,
        pending_conflicts=pending_conflicts,
        relevant_documents=doc_summaries,
        active_tasks=active_tasks,
        proposed_tasks=proposed_tasks,
    )


async def build_student_context_pack(
    session: AsyncSession,
    person: Person,
    *,
    conversation_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    applied_vault_changes_turn: list[dict[str, Any]] | None = None,
) -> StudentContextPack:
    pack = await build_person_context_pack(
        session, person, conversation_id=conversation_id, settings=settings
    )
    if applied_vault_changes_turn:
        pack.applied_vault_changes_turn = applied_vault_changes_turn
    return pack


def context_pack_to_json(pack: BaseModel | PersonContextPack | StudentContextPack) -> str:
    return json.dumps(pack.model_dump(), default=str)


def _pack_get(pack: Any, key: str, default: Any = None) -> Any:
    if pack is None:
        return default
    if isinstance(pack, dict):
        return pack.get(key, default)
    return getattr(pack, key, default)


_MISSING_QUESTION = {
    "location.current_city": "Which city are you in now?",
    "location.current_country": "Which country do you live in now?",
    "education.highest_level": "What is your current education level?",
    "application.study_country": "Which country do you want to study in?",
    "identity.current_status": "Are you a student, graduate, or working professional?",
}


def one_gap_question(missing: list[str]) -> str | None:
    for key in missing:
        if key in _MISSING_QUESTION:
            return _MISSING_QUESTION[key]
    return None


def build_chat_starters(pack: Any) -> list[dict[str, str]]:
    """Tap-to-send prompts so the student has a next chat, not a blank box."""
    facts = [str(item) for item in (_pack_get(pack, "known_facts", []) or [])]
    dest = None
    for fact in facts:
        if fact.lower().startswith("target study country"):
            dest = fact.split(":", 1)[-1].strip() or None
            break
    starters: list[dict[str, str]] = []
    if dest:
        starters.append(
            {
                "label": f"Tests for {dest}",
                "message": f"What language or admissions test score should I target for {dest}?",
            }
        )
        starters.append(
            {
                "label": "Universities that fit me",
                "message": f"Which universities in {dest} fit my education, GPA, and budget?",
            }
        )
    else:
        starters.append(
            {
                "label": "Where should I study?",
                "message": "Based on my profile, which countries and programs should I consider?",
            }
        )
    starters.append(
        {
            "label": "Plan this week",
            "message": "What should I do this week to move my goal forward?",
        }
    )
    typed = _pack_get(pack, "typed_profile_summary", {}) or {}
    if typed.get("skills"):
        starters.append(
            {
                "label": "Jobs and internships",
                "message": "What internships or jobs fit my skills right now?",
            }
        )
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in starters:
        if item["message"] in seen:
            continue
        seen.add(item["message"])
        out.append(item)
        if len(out) == 3:
            break
    return out


def chat_stay_payload(
    pack: Any,
    *,
    next_question: str | None = None,
    suggested_next_step: str | None = None,
) -> dict[str, Any]:
    facts = [str(item) for item in (_pack_get(pack, "known_facts", []) or [])][:16]
    missing = [str(item) for item in (_pack_get(pack, "missing_critical_fields", []) or [])][:8]
    tasks = list(_pack_get(pack, "active_tasks", []) or [])[:5]
    question = next_question or one_gap_question(missing)
    return {
        "knownFacts": facts,
        "missingCriticalFields": missing,
        "nextQuestion": question,
        "suggestedNextStep": suggested_next_step,
        "starters": build_chat_starters(pack),
        "activeTasks": tasks,
    }


def compose_opening(pack: Any) -> str:
    """Vault-grounded first message. Unique facts, goal/education first."""
    identity = _pack_get(pack, "identity", {}) or {}
    name = identity.get("preferredName") or identity.get("fullName")
    facts = [str(item) for item in (_pack_get(pack, "known_facts", []) or [])]
    profile = _opening_facts(facts)
    greeting = f"Hi {name} — I'm PAI." if name else "Hi — I'm PAI, your counselor."
    if not profile:
        return (
            f"{greeting} Tell me what you're working toward and I'll start from there. "
            "You don't need to repeat anything once it's in your profile."
        )
    bullets = "\n".join(f"• {item}" for item in profile)
    return (
        f"{greeting} Good to have you here. So you never have to repeat yourself, "
        f"here's what I already know:\n{bullets}\n\n"
        "What's on your mind right now — something you're trying to figure out, or a "
        "direction you're weighing? We'll start wherever you are."
    )


def _opening_facts(facts: list[str]) -> list[str]:
    """Dedupe by label and by value; rank by known-fact kind, not a country list."""
    rank = {
        "current goal": 0,
        "education": 1,
        "test scores": 2,
        "target study country/countries": 3,
        "target universities": 4,
        "admission cycle": 5,
    }
    by_key: dict[str, str] = {}
    seen_value: set[str] = set()
    for item in facts:
        key, _, rest = item.partition(":")
        label = key.strip().casefold()
        if label == "student name":
            continue
        value = rest.strip().casefold()
        if label in by_key:
            continue
        if value and value in seen_value:
            continue
        by_key[label] = item
        if value:
            seen_value.add(value)
    ordered = sorted(
        by_key.items(),
        key=lambda kv: (rank.get(kv[0].split("(")[0].strip(), 80), kv[0]),
    )
    return [item for _label, item in ordered][:8]
