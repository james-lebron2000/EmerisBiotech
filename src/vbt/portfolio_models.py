"""Strict contracts for research dossiers. These are not drug approval objects."""
from typing import Literal
from datetime import date
from pydantic import Field, HttpUrl, model_validator, ConfigDict
from .models import Contract as BaseContract

class Contract(BaseContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

ID = r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'

class SourceEvidence(Contract):
    evidence_id: str = Field(pattern=ID)
    family: str = Field(min_length=1)
    title: str = Field(min_length=3)
    url: HttpUrl
    locator: str = Field(min_length=3)
    summary: str = Field(min_length=5)
    kind: Literal['market','observational','mechanistic','clinical','experimental']
    checked_on: date
    limitations: list[str] = Field(min_length=1)
    reviewed_by: str | None = None
    review_note: str | None = None
    @model_validator(mode='after')
    def review_pair(self):
        if bool(self.reviewed_by) != bool(self.review_note):
            raise ValueError('Evidence reviewer and review note must be supplied together')
        return self

class EvidenceLink(Contract):
    evidence_id: str = Field(pattern=ID)
    stance: Literal['support','oppose','context']
    interpretation: str = Field(min_length=5)
    contradiction_response: str | None = None
    response_reviewer: str | None = None

class ExperimentDraft(Contract):
    question: str = Field(min_length=5)
    system: str = Field(min_length=3)
    biological_unit: str = Field(min_length=2)
    comparison: str = Field(min_length=5)
    primary_endpoint: str = Field(min_length=3)
    alternatives_discriminated: list[str] = Field(min_length=2)
    controls: list[str] = Field(min_length=2)
    qc_and_stop: list[str] = Field(min_length=2)
    sample_size: int | None = Field(default=None, ge=2)
    sample_size_basis: str | None = None
    analysis_plan: str | None = None
    randomization_blinding: str | None = None
    meaningful_effect: str | None = None
    protocol_ref: str | None = None
    owner: str | None = None
    material_access_ref: str | None = None
    data_rights_ref: str | None = None
    budget_amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    budget_authorization_ref: str | None = None

class Hypothesis(Contract):
    hypothesis_id: str = Field(pattern=ID)
    title: str = Field(min_length=5)
    target: str = Field(min_length=2)
    direction: Literal['inhibit','activate','deplete','replace','unknown']
    modality: str = Field(min_length=3)
    indication: str = Field(min_length=2)
    population: str = Field(min_length=5)
    question: str = Field(min_length=5)
    differentiation_to_test: str = Field(min_length=5)
    alternatives: list[str] = Field(min_length=2)
    competitor_asset_ids: list[str] = Field(min_length=1)
    links: list[EvidenceLink] = Field(min_length=1)
    counterevidence_search: str | None = None
    counterevidence_reviewed_by: str | None = None
    experiment: ExperimentDraft
    human_owner: str | None = None
    @model_validator(mode='after')
    def unique_refs(self):
        for refs in [self.competitor_asset_ids,[x.evidence_id for x in self.links]]:
            if len(refs)!=len(set(refs)): raise ValueError('Duplicate hypothesis reference')
        return self

class PortfolioPlan(Contract):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=5)
    evidence_kind: Literal['public_research','synthetic'] = 'public_research'
    as_of: date
    sources: list[SourceEvidence] = Field(min_length=1)
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=20)
    clinical_release: Literal[False] = False
    @model_validator(mode='after')
    def references(self):
        for seq,key in [(self.sources,'evidence_id'),(self.hypotheses,'hypothesis_id')]:
            ids=[getattr(x,key) for x in seq]
            if len(ids)!=len(set(ids)):raise ValueError('Duplicate object ID')
        sources={s.evidence_id:s for s in self.sources}
        for source in self.sources:
            if source.checked_on>self.as_of:raise ValueError('Evidence checked after portfolio as_of')
        for h in self.hypotheses:
            for link in h.links:
                if link.evidence_id not in sources:raise ValueError('Unknown evidence reference: '+link.evidence_id)
            # A single trial reported several ways cannot contradict itself silently.
            by_family={}
            for link in h.links:
                fam=sources[link.evidence_id].family
                by_family.setdefault(fam,set()).add(link.stance)
            if any({'support','oppose'}<=v for v in by_family.values()):
                raise ValueError('Same evidence family has opposing stances; separate the claim or mark context')
        return self

class PortfolioReview(Contract):
    hypothesis_id: str = Field(pattern=ID)
    action: Literal['continue_research','request_revision','stop','authorize_experiment']
    reviewer: str = Field(min_length=2)
    reason: str = Field(min_length=10)
    signature: str = Field(min_length=2)
