"""Evidence-backed qualification metadata, without inferred equivalencies."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Qualification(BaseModel):
    model_config = ConfigDict(extra="allow")
    original_name: str | None = None
    country: str | None = None
    framework: str | None = None
    framework_level: str | None = None
    canonical_level: str | None = None
    field: str | None = None
    mapping_source: str | None = None
    mapping_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_mapping_evidence(self):
        if not (self.mapping_source or "").strip():
            self.canonical_level = None
            self.mapping_confidence = None
        return self

def qualification_metadata(value: dict | None, *, degree=None, field=None) -> dict:
    raw = dict(value or {})
    raw.setdefault("original_name", degree if isinstance(degree, str) else None)
    raw.setdefault("field", field if isinstance(field, str) else None)
    return Qualification.model_validate(raw).model_dump()
