# Onboarding fields

`POST /api/v1/onboarding` accepts one JSON body for the **form path**. Onboarding is a **starting seed**, not the full Person Vault. Chat and documents fill in the rest.

The **CV path** is `POST /api/v1/onboarding/cv` only. A successful extract marks onboarding complete. Do not send this form after a CV upload.

Use ids from `GET /api/v1/onboarding` → `data.enums` **before** submit. Do not send display labels like `"Bachelor's"` or `"University admission"`. `POST /onboarding` and `POST /onboarding/cv` return compact status (`onboardingCompleted`, `nextPath`, `identity`) — not the form catalog.

Countries may be sent as ISO alpha-2 (`PK`), alpha-3 (`DEU`), or English name (`Pakistan`). PAI stores alpha-2.

Phones are stored as E.164. Send `+923001234567`, or a national number plus `currentCountry` (`03001234567` + `PK`).

---

## `otherLevelLabel`

Show this field **only when `educationLevel` is `other`**.

The closed list is `high_school` | `diploma` | `bachelor` | `master` | `phd` | `other`. If none of those fit (A-Levels, IB, CA, vocational cert), the user picks `other` and types the real name here. That text becomes the stored degree label.

| `educationLevel` | `otherLevelLabel` |
|---|---|
| `bachelor` | omit / `null` |
| `other` | `"A-Levels"` or `"Chartered Accountant"` |

```json
{
  "educationLevel": "other",
  "otherLevelLabel": "A-Levels",
  "institution": "Beaconhouse"
}
```

---

## Required

| Field | What it is | Allowed values / format | Example |
|---|---|---|---|
| `path` | How they onboarded. Optional; defaults to the chosen path, else `manual`. | `manual` \| `cv` | `"manual"` |
| `phone` | Mobile number | E.164, or national + `currentCountry` | `"+923001234567"` |
| `dateOfBirth` | Date of birth | ISO date, age 13–100 | `"2004-03-12"` |
| `nationality` | Citizenship | ISO country | `"PK"` |
| `currentCountry` | Where they live now | ISO country | `"PK"` |
| `currentCity` | City they live in | Free text, 2–128 chars | `"Lahore"` |
| `currentStatus` | Life stage | `student` \| `graduate` \| `professional` \| `job_seeker` \| `other` | `"student"` |
| `educationLevel` | Highest level so far | `high_school` \| `diploma` \| `bachelor` \| `master` \| `phd` \| `other` | `"bachelor"` |
| `gender` | Gender | `male` \| `female` \| `non_binary` \| `prefer_not_to_say` \| `other` | `"male"` |
| `primaryGoal` | Why they are using PAI | `exploring` \| `placement` \| `admission` \| `professional` \| `journey_tracker` | `"admission"` |

`primaryGoal` is a **category**, not a sentence. Put `"MS Computer Science in Germany"` in `goalDetail`.

---

## Conditional (send if known)

Shown as extra inputs, not blockers. Needed for a useful education row when the student has one.

| Field | What it is | Example |
|---|---|---|
| `institution` | School / college / university name | `"Bahria University"` |
| `degree` | Program short name (free text) | `"BSCS"` |
| `major` | Field of study enum | `"computer_science"` |
| `otherLevelLabel` | Custom level name when `educationLevel` is `other` | `"A-Levels"` |

`major` ids: `computer_science` | `software_engineering` | `data_science` | `artificial_intelligence` | `engineering` | `business` | `medicine` | `law` | `arts_humanities` | `social_sciences` | `natural_sciences` | `other`

---

## Optional

| Field | What it is | Allowed values / format | Example |
|---|---|---|---|
| `goalDetail` | Free-text note under `primaryGoal` | ≤ 256 chars | `"MS Computer Science in Germany"` |
| `linkedinUrl` | LinkedIn profile | `linkedin.com` URL | `"https://www.linkedin.com/in/ali"` |
| `gpa` | GPA on a 4.0 scale | 0–4 | `3.4` |
| `graduationYear` | Year they finished (or will) | 1950–2100 | `2026` |
| `skills` | Skill list | see below | `[{"name":"Python","proficiency":"intermediate"}]` |
| `workExperience` | Jobs / internships | see below | |
| `targetCountries` | Destinations they are considering | ISO list | `["DE","NL"]` |
| `studyCountry` | Primary study destination | ISO alpha-2 | `"DE"` |
| `intake` | Intake season | `fall` \| `spring` \| `summer` \| `winter` \| `rolling` | `"fall"` |
| `intakeYear` | Intake year | 2020–2100 | `2027` |
| `budget` | Funding band | `limited` \| `moderate` \| `comfortable` \| `fully_funded` | `"limited"` |
| `scholarships` | Interested in scholarships | boolean | `true` |
| `testScores` | Standardized tests | see below | `[{"name":"ielts","score":"7.5"}]` |

### `skills[]`

| Field | Required | Values | Example |
|---|---|---|---|
| `name` | yes | free text | `"Python"` |
| `proficiency` | no | `beginner` \| `intermediate` \| `advanced` \| `expert` | `"intermediate"` |

Bare strings are also accepted: `"skills": ["Python", "SQL"]`.

### `workExperience[]`

| Field | Required | Values | Example |
|---|---|---|---|
| `organization` | yes | free text | `"Systems Ltd"` |
| `title` | yes | free text | `"Software intern"` |
| `employmentType` | no | `internship` \| `part_time` \| `full_time` \| `contract` \| `freelance` \| `other` | `"internship"` |
| `isCurrent` | no | boolean, default `false` | `true` |
| `description` | no | free text | `"Built internal tools."` |

### `testScores[]`

| Field | Required | Values | Example |
|---|---|---|---|
| `name` | yes | `ielts` \| `toefl` \| `pte` \| `duolingo` \| `gre` \| `gmat` \| `sat` \| `act` \| `net` \| `ecat` \| `mdcat` \| `other` | `"ielts"` |
| `score` | yes | free text | `"7.5"` |

---

## Minimal payload (unlocks chat)

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
  "primaryGoal": "admission"
}
```

## Typical student payload

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
  "graduationYear": 2026,
  "primaryGoal": "admission",
  "goalDetail": "MS Computer Science in Germany",
  "studyCountry": "DE",
  "targetCountries": ["DE", "NL"],
  "intake": "fall",
  "intakeYear": 2027,
  "budget": "limited",
  "scholarships": true,
  "linkedinUrl": "https://www.linkedin.com/in/ali",
  "skills": [
    { "name": "Python", "proficiency": "intermediate" }
  ],
  "testScores": [
    { "name": "ielts", "score": "7.5" }
  ]
}
```

## Frontend hints

1. Enum dropdowns: bind to `GET /onboarding` `enums.<field>` (`id` + `label`).
2. Country dropdowns: `enums.countries` (ISO `id`, English `label`) for nationality, current country, study country, and target countries.
3. Show `otherLevelLabel` only if `educationLevel === "other"`.
4. Show `goalDetail` as an optional text box under `primaryGoal`.
5. `institution`, `degree`, and `major` are not required to complete onboarding.