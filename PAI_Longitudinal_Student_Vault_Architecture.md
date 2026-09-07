# PAI Student Vault — Longitudinal Student World Model

## 1. Purpose

PAI's Vault should evolve from a collection of structured profile fields into a **canonical, structured, longitudinal Student World Model**.

The Vault should represent what is actually known about a student across time:

- identity
- demographics
- education history
- work history
- projects
- skills
- certifications
- tests
- achievements
- languages
- research/publications
- extracurricular activities
- volunteering
- location
- preferences
- constraints
- finance
- mobility
- accessibility

The Vault should not become a giant dump of everything PAI knows.

The permanent architectural separation should remain:

```text
VAULT
= canonical structured student facts and profile history

MEMORY
= narrative/contextual understanding

GOALS
= what the student wants

JOURNEY
= important changes/events across the person's relationship with PAI

DOCUMENTS
= evidence/artifacts

ASSESSMENTS
= what PAI currently concludes about the student

PLANS
= actions toward a goal

PROJECTIONS
= what might happen next

OPPORTUNITY MATCHES
= explainable comparisons between the student and opportunities
```

These systems may reference one another, but they should not be collapsed into one store.

---

# 2. Core Principle

The Vault should answer:

> **What do we know about this student, where does each fact belong, when was it true, what evidence supports it, how confident are we, and what is still missing or contradictory?**

Some sections are naturally **current-state fields**.

Examples:

- current city
- preferred language
- communication style
- current relocation willingness

Other sections are naturally **timelines**.

Examples:

- education
- work experience
- projects
- tests
- certifications
- skills development
- location history
- applications

These should not behave like unordered flat fields.

---

# 3. Current Backend Vault Structure

The current backend already contains a useful foundation.

## 3.1 Person / Authentication

```text
Person
├── auth_provider
├── external_auth_id
├── email
├── email_verified
├── full_name
├── preferred_name
├── phone
├── account_status
├── onboarding_completed_at
├── onboarding_path
└── version
```

## 3.2 Current Vault Catalog

```text
STUDENT
│
├── Auth
│   ├── user_id
│   ├── email
│   ├── email_verified
│   └── account_status
│
├── Identity
│   ├── full_name
│   ├── preferred_name
│   ├── phone
│   ├── current_status
│   └── national_id
│
├── Demographics
│   ├── date_of_birth
│   ├── gender
│   └── nationality
│
├── Location
│   ├── current_country
│   └── current_city
│
├── Social
│   └── linkedin_url
│
├── Preferences
│   ├── preferred_language
│   ├── communication_style
│   └── learning_style
│
├── Education
│   ├── highest_level
│   ├── stream
│   ├── marks
│   ├── additional_maths
│   ├── records
│   ├── program
│   └── gpa
│
├── Career
│   ├── work_history
│   ├── projects
│   ├── skills
│   └── certifications
│
├── Application
│   ├── goals
│   ├── career_interest
│   ├── study_country
│   ├── target_universities
│   ├── admission_cycle
│   └── test_scores
│
├── Mobility
│   ├── passport_number
│   ├── relocation_willingness
│   └── preferred_regions
│
├── Finance
│   ├── funding_status
│   └── scholarship_interest
│
├── Lifestyle
│   └── work_life_balance
│
├── Accessibility
│   └── accommodation_needs
│
└── Family
    └── sponsor_contact
```

## 3.3 Current Typed Entities

The backend already stores several repeatable entities separately instead of putting everything into flat JSON.

### Education

```text
Education
├── institution
├── qualification_data
├── degree
├── major
├── start_date
├── end_date
├── graduation_year
├── gpa
├── gpa_scale
├── percentage
└── status
```

### Work Experience

```text
WorkExperience
├── organization
├── title
├── employment_type
├── start_date
├── end_date
├── is_current
└── description
```

### Project

```text
Project
├── name
├── description
├── role
├── start_date
├── end_date
└── url
```

### Skill

```text
Skill
├── name
├── proficiency
└── years_experience
```

### Certification

```text
Certification
├── name
├── issuer
├── issue_date
├── expiry_date
└── credential_url
```

### Goals

Goals are already correctly separated from the Vault itself.

```text
Goal
├── goal_type
├── title
├── description
├── lifecycle_status
├── intelligence_status
├── target_date
├── priority
├── degree_level
├── program
├── target_country
├── intake_year
├── intake_term
├── budget_range
├── target_company
├── role
├── anchors
├── confidence
└── source_conversation_id
```

## 3.4 Current Vault Integrity Layer

The current backend already has important supporting structures.

```text
PersonVault
├── catalog_version
├── applicable_scopes
├── critical_completion
├── important_completion
├── enrichment_completion
├── overall_completion
│
├── VaultValue[]
│   ├── field_key
│   ├── value
│   ├── encrypted_value
│   ├── status
│   ├── verification_level
│   ├── confidence
│   ├── valid_from
│   ├── valid_until
│   ├── supersedes_id
│   └── version
│
├── VaultEvidence[]
│   ├── source_type
│   ├── source_reference
│   ├── evidence_text
│   └── confidence
│
├── VaultHistory[]
│
└── PersonConsent[]
```

This gives the current Vault:

- versioning
- confidence
- verification
- provenance
- history
- sensitive-field consent
- encrypted values
- temporal validity

That foundation should be preserved.

---

# 4. Main Current Limitation

The backend is **not simply flat**, but the typed entities are still relatively shallow.

For example:

```text
Education
├── degree
├── institution
├── GPA
└── dates
```

is useful, but for a true Student World Model we eventually want:

```text
Education
├── identity of qualification
├── timeline position
├── academic performance
├── subjects/courses
├── evidence
├── achievements
├── related projects
├── verification
├── confidence
└── timeline relationships
```

The same issue applies to WorkExperience, Skill, Project, Certification, and Tests.

The evolution should therefore be:

> **Not "add hundreds of flat Vault fields", but enrich selected domains into proper longitudinal entities.**

---

# 5. Timeline-Aware Vault Domains

The following domains should be timeline-aware.

```text
Education
Work Experience
Projects
Tests
Certifications
Applications
Location History
Skills Development
Possibly Research / Publications
```

Not every domain needs a timeline.

Mostly current-state domains can remain simple:

```text
Identity
Nationality
Preferred language
Communication style
Current relocation willingness
Accessibility needs
Current finance constraints
```

---

# 6. Education Timeline — Core Example

Education should be treated as an ordered academic history.

```text
Education Timeline

2018–2020
└── Matric

2020–2022
└── FSc Pre-Engineering

2022–2026
└── BS Computer Science

2027+
└── NOT current truth
    possible goal/projection only
```

The key rule is:

> **Every new academic observation should be resolved to the correct Education entity before being persisted.**

Example:

Student says:

> "I got 82% in FSc."

PAI should not write:

```text
education.marks = 82%
```

without context.

It should perform:

```text
Observation
    ↓
Academic entity resolution
    ↓
Which existing education record does this describe?
    ↓
FSc
    ↓
Attach 82% to FSc
```

If the student later uploads an FSc transcript, the document evidence should attach to the same FSc entity.

---

# 7. Normalized Education Levels

PAI needs an internal normalized understanding of education levels.

For example, Pakistan:

```text
School / Secondary
    ↓
Matric / O-Level
    ↓
FSc / FA / ICS / A-Level / equivalent
    ↓
Bachelor
    ↓
Master
    ↓
PhD
```

But PAI must **not hard-code only Pakistan**.

Each Education entity should preserve the actual qualification while optionally mapping it to a canonical level.

Example:

```text
original_name = "FSc Pre-Engineering"
country = "Pakistan"
framework = "HSSC"
canonical_level = "higher_secondary"
field = "Pre-Engineering"
```

Another country may have a completely different qualification name but still map to a comparable canonical stage.

The current `qualification_data` concept is a strong foundation for this.

---

# 8. Proposed Rich Education Entity

```text
EducationRecord
│
├── Identity
│   ├── institution
│   ├── qualification
│   ├── original_qualification_name
│   ├── country
│   ├── framework
│   ├── framework_level
│   ├── canonical_level
│   ├── field / major / stream
│   └── status
│
├── Timeline
│   ├── start_date
│   ├── end_date
│   ├── graduation_year
│   ├── sequence_order
│   └── current / completed / interrupted
│
├── Academic Performance
│   ├── GPA
│   ├── GPA scale
│   ├── percentage
│   ├── marks_obtained
│   ├── marks_total
│   ├── rank
│   └── distinction
│
├── Subjects / Courses[]
│   ├── name
│   ├── grade
│   ├── credits
│   ├── result
│   └── evidence
│
├── Academic Achievements[]
│
├── Related Projects[]
│
├── Evidence[]
│   ├── transcript
│   ├── degree certificate
│   ├── marks sheet
│   └── institution document
│
├── Provenance
│   ├── source
│   ├── confidence
│   ├── verification_level
│   └── timestamps
│
└── Relationships
    ├── previous_education_id
    └── next_education_id
```

`previous_education_id` / `next_education_id` may be derived rather than explicitly stored if ordering can be reliably calculated.

---

# 9. Timeline Placement

Whenever new information is collected:

```text
Chat / Document / Onboarding / User Edit
                  ↓
              Observation
                  ↓
            Entity Resolver
                  ↓
       Resolve existing or create new
                  ↓
           Timeline Placement
                  ↓
            Timeline Validation
```

The placement engine should consider:

- qualification type
- canonical level
- institution
- dates
- graduation year
- field/major
- known neighboring records
- evidence
- country/framework
- student wording
- existing timeline

---

# 10. Education Entity Resolution

Example existing Vault:

```text
Education #1
Matric
2018–2020

Education #2
FSc Pre-Engineering
2020–2022

Education #3
BSCS
2022–2026
```

New message:

> "My FSc percentage was 82%."

Resolver should strongly match Education #2.

New message:

> "I studied at Punjab College."

If only FSc institution is missing and surrounding context makes it clear, the observation may resolve to Education #2.

If multiple records could match, PAI should **not guess**.

Create an ambiguity:

```text
TimelineIssue
type = ambiguous_entity
```

and clarify when useful.

---

# 11. Timeline Validation

After every relevant insert/update, the domain timeline should be validated.

Possible checks:

```text
missing_stage
missing_start_date
missing_end_date
date_gap
date_overlap
qualification_order_problem
duplicate_entity
ambiguous_entity
contradicting_date
contradicting_grade
contradicting_institution
impossible_transition
unverified_mapping
```

Important rule:

> **A detected issue is not automatically an error.**

Examples:

- gaps may be legitimate
- overlapping work experiences are normal
- overlapping education can be normal
- a student can skip a traditional educational stage
- qualifications differ by country

The validator should detect suspicious patterns, not impose a rigid worldview.

---

# 12. Missing Education Stage Example

Known:

```text
Matric
2018–2020

BSCS
2022–2026
```

Potential issue:

```text
TimelineIssue
domain = education
issue_type = missing_stage
between = Matric → Bachelor
period = 2020–2022
```

PAI should not silently invent FSc.

At a relevant conversational moment, it can ask:

> "I have your Matric and bachelor's details, but I'm missing what you studied between them. Did you do FSc, A-Levels, ICS, or something else?"

Answer:

> "FSc Pre-Engineering."

Then:

```text
Matric
    ↓
FSc Pre-Engineering
    ↓
BSCS
```

Issue becomes resolved.

---

# 13. Contradiction Example

Existing:

```text
FSc
end_year = 2022
```

New statement:

> "I completed FSc in 2023."

Do not silently overwrite.

Create:

```text
TimelineIssue
domain = education
issue_type = contradicting_date

existing = 2022
new_claim = 2023
```

If no stronger evidence automatically resolves it, the counselor may ask:

> "I previously had your FSc completion as 2022, but you just mentioned 2023. Which year is correct?"

If a verified transcript says 2022 and the student casually says 2023, the evidence policy may retain 2022 and keep the new statement as an unconfirmed conflicting claim.

---

# 14. TimelineIssue — Proposed Concept

PAI should have a reusable issue model.

```text
TimelineIssue
├── id
├── person_id
├── domain
│   ├── education
│   ├── work
│   ├── tests
│   ├── projects
│   └── ...
├── issue_type
│   ├── missing_stage
│   ├── missing_date
│   ├── overlap
│   ├── contradiction
│   ├── ambiguous_entity
│   ├── duplicate_entity
│   └── impossible_or_unusual_transition
├── related_entity_ids[]
├── detected_from
├── severity
├── confidence
├── clarification_needed
├── status
│   ├── open
│   ├── resolved
│   ├── ignored
│   └── accepted_as_valid
├── resolution
└── timestamps
```

This concept can later be generalized beyond timelines into broader **ProfileIssue** if needed.

---

# 15. Important Behavioral Rule: Detect Everything, Ask Selectively

This is critical.

PAI may know:

```text
7 missing profile facts
3 timeline gaps
2 conflicting claims
1 unverified document fact
```

That does **not** mean the counselor should ask ten questions.

Instead:

```text
Detected Issues
      ↓
Discovery / Relevance Ranking
      ↓
Highest-value issue for current conversation
      ↓
Counselor may ask ONE natural question
```

The counselor should consider:

- current user goal
- current conversation topic
- whether the missing fact blocks a recommendation
- decision impact
- whether PAI has already asked recently
- whether the user is in an emotional/counseling moment
- whether the user simply wants a direct answer
- severity of contradiction
- evidence availability

This prevents the Vault from turning the counselor into an onboarding questionnaire.

---

# 16. Example of Relevance-Based Clarification

PAI knows:

```text
Missing:
- Matric school name
- Bachelor's CGPA
- FSc completion date
- Father's sponsor phone
```

Student asks:

> "Can I apply for an MS in AI in Germany?"

Likely priority:

```text
Bachelor's CGPA
    >
FSc completion date
    >
Matric school name
```

PAI should not ask for sponsor phone unless it becomes relevant to finance/visa planning.

---

# 17. Work Experience Timeline

Work should also be timeline-aware.

```text
Work Timeline

2023
└── Software Intern
    └── Company A

2024
└── Freelance Web Developer

2025–Present
└── Backend Engineer
    └── Company B
```

Overlaps are often valid.

A richer WorkExperience should eventually contain:

```text
WorkExperience
├── organization
├── title
├── employment_type
├── start_date
├── end_date
├── is_current
├── description
├── responsibilities[]
├── achievements[]
├── skills_used[]
├── technologies[]
├── location
├── evidence[]
├── verification
└── provenance
```

---

# 18. Projects Timeline

Projects should become richer entities rather than names/descriptions only.

```text
Project
├── name
├── role
├── description
├── start_date
├── end_date
├── status
├── technologies[]
├── skills_used[]
├── outcomes[]
├── collaborators[]
├── repository_url
├── live_url
├── related_work_experience_id
├── related_education_id
├── evidence[]
└── provenance
```

This becomes important for skill evidence and opportunity matching.

---

# 19. Skills Should Be Evidence-Backed

Current:

```text
Python
├── proficiency
└── years_experience
```

Target:

```text
Skill
├── name
├── proficiency
├── years_experience
├── first_observed
├── last_observed
├── status
│   ├── active
│   ├── stale
│   └── historical
├── evidence[]
│   ├── Project A
│   ├── Internship B
│   ├── Certification C
│   └── Document D
├── confidence
└── provenance
```

This makes matching explainable.

Instead of:

> "Student has Python."

PAI can say:

> "Python is supported by two projects and one internship, and was last observed in 2026."

---

# 20. Tests Must Become Proper Entities

Current test scores are too flat for long-term matching.

Target:

```text
Test
├── type
│   ├── IELTS
│   ├── TOEFL
│   ├── GRE
│   ├── SAT
│   └── ...
├── attempt_number
├── test_date
├── overall_score
├── sections
│   ├── listening
│   ├── reading
│   ├── writing
│   └── speaking
├── validity_until
├── status
│   ├── valid
│   └── expired
├── evidence[]
└── verification
```

Example:

```text
IELTS
├── Attempt 1 — 2024 — 6.5 — expired
└── Attempt 2 — 2026 — 7.5 — current
```

Do not overwrite previous attempts.

---

# 21. Certifications Timeline

```text
Certification
├── name
├── issuer
├── issue_date
├── expiry_date
├── credential_id
├── credential_url
├── status
│   ├── active
│   └── expired
├── related_skills[]
├── evidence[]
└── verification
```

---

# 22. Additional Typed Domains Worth Adding

Not all must be added immediately, but these are valuable for future matching.

## Languages

```text
Language
├── name
├── speaking_level
├── reading_level
├── writing_level
├── listening_level
├── certification
├── evidence
└── confidence
```

## Achievements / Awards

```text
Achievement
├── title
├── issuer
├── date
├── category
├── description
├── level
│   ├── institution
│   ├── regional
│   ├── national
│   └── international
└── evidence
```

## Volunteering

```text
VolunteerExperience
├── organization
├── role
├── start_date
├── end_date
├── responsibilities
├── achievements
├── skills
└── evidence
```

## Extracurricular Activities

```text
Activity
├── type
├── organization
├── role
├── dates
├── achievements
└── evidence
```

## Research

```text
ResearchExperience
├── title
├── institution
├── supervisor
├── topic
├── methods
├── dates
├── output
├── related_skills
└── evidence
```

## Publications

```text
Publication
├── title
├── authors
├── venue
├── publication_date
├── DOI / URL
├── status
└── evidence
```

## Competitions

```text
Competition
├── name
├── level
├── date
├── result
├── team
├── skills
└── evidence
```

---

# 23. Evidence Must Become Entity-Level

This is one of the most important architectural improvements.

Today VaultValue has strong support for:

- source
- evidence
- confidence
- verification
- history

Typed entities should receive equivalent evidence support.

Target:

```text
Education
└── Evidence[]
    ├── transcript
    ├── certificate
    └── student statement

Skill
└── Evidence[]
    ├── project
    ├── internship
    ├── certification
    └── portfolio

WorkExperience
└── Evidence[]
    ├── CV
    ├── offer letter
    ├── experience letter
    └── student statement
```

This can be implemented through a generic evidence relation instead of duplicating evidence columns everywhere.

Example concept:

```text
EntityEvidence
├── entity_type
├── entity_id
├── source_type
├── source_reference
├── evidence_text
├── confidence
├── verification_level
└── timestamp
```

---

# 24. Entity-Level History

Typed entities should also gain stronger history semantics.

Example:

```text
Education #FSC
percentage:
    82% self-reported
        ↓
    82.2% transcript-verified
```

The old information should remain historically traceable.

Do not simply erase the previous claim.

Useful concepts:

```text
EntityChange
├── entity_type
├── entity_id
├── attribute
├── old_value
├── new_value
├── source
├── reason
└── timestamp
```

The current VaultHistory mechanism may be extended instead of creating an entirely separate history system.

---

# 25. Privacy / Visibility

Each field/entity or relationship should eventually support sharing rules.

Example classes:

```text
private
counselor_only
student_visible
shareable
explicit_consent_required
```

Examples:

```text
Python skill
→ potentially shareable

BSCS degree
→ potentially shareable

Work experience
→ potentially shareable

Family pressure
→ private / memory, not matching profile

Emotional struggles
→ private / memory

Counseling conversation
→ private
```

PAI must never expose private counseling context to opportunity providers merely because it knows it.

---

# 26. Vault Intelligence Should Evolve

Current conceptual responsibility:

> "Extract fields from the message."

Target responsibility:

> **"Understand what we learned about this student, resolve it to the correct entity, reconcile it with existing truth, place it correctly in time, detect gaps/conflicts, and produce structured candidates for safe persistence."**

Possible conceptual name:

```text
Student Understanding Intelligence
```

or retain the Vault Intelligence name but expand its responsibility.

---

# 27. Observation Layer

Before writing to the Vault, information should become structured observations.

Example:

Student:

> "I did FSc Pre-Engineering from Punjab College in 2022 and got 82%."

Observation:

```text
StudentObservation
├── domain = education
├── entity_hint = FSc Pre-Engineering
├── attributes
│   ├── institution = Punjab College
│   ├── qualification = FSc Pre-Engineering
│   ├── graduation_year = 2022
│   └── percentage = 82
├── source = chat
├── assertion_status = explicit
├── evidence_span = exact student wording
├── confidence
└── timestamp
```

Then:

```text
Observation
    ↓
Entity resolution
    ↓
Evidence / policy gates
    ↓
Timeline placement
    ↓
Validation
    ↓
Persist / Pending / Conflict
```

---

# 28. Multi-Source Learning

Vault Intelligence should learn from:

```text
Chat
Documents
Onboarding
User edits
Application data
Future integrations
Outcome data
Professional profile integrations
```

All sources should contribute observations, but source authority may differ.

Example:

```text
Student chat says GPA = 3.4
Transcript says GPA = 3.47
```

The transcript may become the verified canonical value while the chat statement remains historical evidence.

---

# 29. Do Not Duplicate Entities

Example:

Chat:

```text
FSc Pre-Engineering
Punjab College
2022
82%
```

Later transcript:

```text
Punjab College
FSc Pre-Engineering
2022
82.2%
```

Wrong result:

```text
Education #1 = FSc from chat
Education #2 = FSc from transcript
```

Correct result:

```text
Education #1 = FSc
├── institution = Punjab College
├── graduation_year = 2022
├── percentage = 82.2
├── evidence
│   ├── student chat: 82
│   └── transcript: 82.2
└── verification = document_verified
```

Entity resolution and reconciliation are therefore essential.

---

# 30. Clarification Should Be a Product of Understanding

PAI should not ask questions because a fixed form field is empty.

Instead:

```text
Student Model
    ↓
Known facts
Unknown facts
Timeline gaps
Contradictions
Ambiguities
Goal-relevant missing facts
    ↓
Discovery ranking
    ↓
Natural counselor clarification
```

This makes conversation feel intelligent instead of mechanistic.

---

# 31. Clarification Priority

A suggested ranking model can consider:

```text
priority =
    goal relevance
  + decision impact
  + contradiction severity
  + timeline importance
  + evidence uncertainty
  + current conversation relevance
  - recently asked penalty
  - interruption cost
```

The current backend already has a discovery-ranking philosophy that can be extended with timeline issues.

---

# 32. Counselor Rules

The counselor should follow these rules:

1. Do not ask about every missing fact.
2. Ask at most one high-value clarification in a turn unless absolutely necessary.
3. Prefer facts that materially affect the user's current decision.
4. Do not interrupt emotional/counseling moments with profile collection.
5. Do not re-ask known information.
6. Do not assume missing timeline stages.
7. Do not silently overwrite contradictory facts.
8. Use document evidence when stronger than casual statements.
9. Ask naturally, not like a database form.
10. Let users continue even when they decline to clarify a non-blocking issue.

---

# 33. Example End-to-End Education Flow

Student says:

> "I'm doing BSCS."

PAI:

```text
Observation
domain = education
qualification = BSCS
status = current
```

Resolver:

```text
No matching bachelor record
→ Create Bachelor education entity
```

Timeline:

```text
Bachelor — current
```

Validator notices:

```text
No earlier education known
```

This alone may not require immediate questioning.

Later student asks:

> "Am I eligible for MS AI in Germany?"

Relevant education details are now important.

Discovery sees:

```text
Bachelor known
CGPA missing
graduation date missing
prior education incomplete
```

Highest decision impact may be CGPA.

Counselor asks:

> "What is your current CGPA?"

Later student says:

> "I did FSc Pre-Engineering before this."

Resolver creates:

```text
FSc
    ↓
Bachelor
```

Later:

> "My FSc was 82%."

Resolver attaches 82% to FSc.

Later transcript is uploaded with 82.2%.

Document Intelligence reconciles and verifies the FSc entity.

This is the behavior PAI should achieve.

---

# 34. Other Timeline Examples

## Tests

```text
IELTS Attempt 1
2024
6.5
expired

IELTS Attempt 2
2026
7.5
valid
```

## Work

```text
Internship
2023

Freelance
2024

Backend Engineer
2025–Present
```

## Certifications

```text
AWS Cloud Practitioner
2024–2027
active
```

## Skills

```text
Python
first_observed = 2023
used_in_project = 2024
used_in_internship = 2025
last_observed = 2026
status = active
```

---

# 35. Current State vs Future State

## Current

```text
Vault Catalog
+
VaultValue
+
Typed Education
+
Typed WorkExperience
+
Typed Project
+
Typed Skill
+
Typed Certification
+
Goal Domain
+
Document Evidence Pipeline
+
Memory
+
Journey
```

## Target

```text
Longitudinal Student World Model
│
├── Current-state Vault fields
│
├── Rich timeline-aware entities
│   ├── Education
│   ├── Work
│   ├── Projects
│   ├── Skills
│   ├── Tests
│   ├── Certifications
│   └── other useful domains
│
├── Entity relationships
├── Entity-level evidence
├── Entity-level verification
├── Entity history
├── Timeline validation
├── Gap / contradiction detection
├── Clarification ranking
└── Explainable opportunity matching
```

---

# 36. Recommended Target Vault Tree

```text
STUDENT WORLD MODEL
│
├── Identity
│   ├── Basic Identity
│   ├── Contact
│   └── Verified Identity Evidence
│
├── Demographics
│
├── Location
│   ├── Current Location
│   └── Location History[]
│
├── Education[]
│   ├── Qualification Identity
│   ├── Timeline
│   ├── Academic Performance
│   ├── Subjects / Courses[]
│   ├── Achievements[]
│   ├── Related Projects[]
│   └── Evidence[]
│
├── Work Experience[]
│   ├── Timeline
│   ├── Responsibilities[]
│   ├── Achievements[]
│   ├── Skills[]
│   ├── Technologies[]
│   └── Evidence[]
│
├── Projects[]
│   ├── Timeline
│   ├── Technologies[]
│   ├── Skills[]
│   ├── Outcomes[]
│   └── Evidence[]
│
├── Skills[]
│   ├── Proficiency
│   ├── Usage History
│   ├── Related Experience[]
│   ├── Related Projects[]
│   ├── Related Certifications[]
│   └── Evidence[]
│
├── Tests[]
│   ├── Attempts[]
│   ├── Validity
│   └── Evidence[]
│
├── Certifications[]
│
├── Languages[]
│
├── Achievements / Awards[]
│
├── Research[]
│
├── Publications[]
│
├── Activities[]
│
├── Volunteering[]
│
├── Competitions[]
│
├── Preferences
│
├── Constraints
│
├── Finance
│
├── Mobility
│
├── Accessibility
│
├── Privacy / Consent
│
├── Provenance
│
├── Evidence
│
└── Timeline / Profile Issues[]
```

Outside but connected:

```text
Goals
Journey
Memory
Documents
Assessments
Plans
Projections
Opportunity Matches
```

---

# 37. Opportunity Matching Benefit

Once this model exists, PAI can compare opportunities against real evidence.

Example opportunity:

```text
MS Artificial Intelligence
Requirements:
- Bachelor's in CS or related field
- CGPA >= 3.0
- Python
- Machine Learning background
- IELTS 6.5
```

Student model:

```text
Education
└── BSCS
    └── CGPA 3.42

Skills
├── Python
│   ├── Project A evidence
│   └── Internship evidence
└── Machine Learning
    └── Final-year project evidence

Tests
└── IELTS 7.0
```

PAI can explain:

> The student meets the academic and language requirements. Python is supported by both project and internship evidence. Machine-learning experience is supported by the final-year project.

Instead of generating:

> "92% match."

Matching should remain explainable.

---

# 38. Recommended Architecture Flow

```text
                SOURCE
   Chat / Docs / Onboarding / User Edit
                    ↓
             Vault Intelligence
                    ↓
             Observation Layer
                    ↓
             Entity Resolution
                    ↓
          Evidence / Policy Gates
                    ↓
              Reconciliation
                    ↓
             Timeline Placement
                    ↓
            Timeline Validation
                    ↓
        ┌───────────┴───────────┐
        ↓                       ↓
      Valid                  Issue
        ↓                       ↓
     Persist          Gap / Conflict / Ambiguity
                                ↓
                       Discovery Ranking
                                ↓
                          Counselor Context
                                ↓
                     Ask only when relevant
                                ↓
                           Resolution
```

---

# 39. What Not To Do

Do not:

- convert everything into flat Vault keys
- store future goals as current facts
- silently assume missing educational stages
- overwrite conflicting claims automatically
- create duplicate education/work entities from different sources
- ask the student every time a field is missing
- treat AI inference as verified truth
- expose private counseling information to opportunity matching
- mix Memory, Goals, Journey and Projections directly into canonical Vault truth
- hard-code one country's education system as universal

---

# 40. What To Reuse From Current Backend

The existing architecture already gives strong foundations.

Reuse:

- Person
- PersonVault
- VaultValue
- VaultHistory
- VaultEvidence
- PersonConsent
- Education
- WorkExperience
- Project
- Skill
- Certification
- Goal domain
- document intelligence
- candidate evaluation / evidence gates
- typed candidate application
- qualification metadata
- discovery ranking
- semantic memory
- Journey
- goal intelligence

The change should be evolutionary, not a rewrite.

---

# 41. Suggested Implementation Phases

## Phase 1 — Education Timeline

Start with education because it provides the clearest value.

Implement:

- normalized education levels
- richer education entity
- entity resolver
- timeline ordering
- timeline issue detection
- missing-stage detection
- contradiction detection
- discovery integration
- natural counselor clarification

## Phase 2 — Entity Evidence

Generalize evidence support to typed entities.

Implement:

- entity evidence relation
- entity verification
- entity provenance
- attribute-level change history where useful

## Phase 3 — Tests

Move test scores from flat arrays into repeatable TestAttempt entities.

## Phase 4 — Skills Relationships

Connect skills to:

- projects
- work experience
- certifications
- courses
- documents

## Phase 5 — Work and Projects Timeline

Add richer history, responsibilities, achievements and evidence.

## Phase 6 — Additional Student Domains

Add where product needs justify them:

- languages
- achievements
- volunteering
- extracurriculars
- research
- publications
- competitions

## Phase 7 — Opportunity Matching

Build matching against the structured student model with evidence-backed explanations.

---

# 42. Final Design Rule

PAI's Vault should not merely answer:

> "What fields do we have?"

It should answer:

> **"What is the student's structured life, education and career history; how does every known fact fit into it; what evidence supports it; what is missing; what conflicts; and what matters for the student's next decision?"**

The desired behavior is:

```text
Chat teaches PAI
Documents verify PAI
New evidence updates the right entity
Timeline logic puts facts in the right historical position
Validation detects gaps and contradictions
Discovery decides what is worth asking
Counselor asks naturally
Vault becomes more complete
Matching becomes more accurate
Outcomes generate new evidence
Student model keeps evolving
```

That is the intended **PAI Longitudinal Student World Model**.
