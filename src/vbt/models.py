from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

class DatasetVersion(Contract):
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.:+-]+$")
    source_version: str | None = None
    source_url: str = Field(min_length=1)
    local_path: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    download_status: Literal["complete"]
    format: Literal["geo_matrix", "geo_platform", "csv", "tsv", "parquet", "json"]
    evidence_kind: Literal["public_observational", "synthetic", "experimental"] = "public_observational"
    required_columns: list[str] = Field(default_factory=list)
    license: str = "unverified"

class Cohort(Contract):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    matrix_key: str
    sample_key: str | None = None
    probe_key: str | None = None
    endpoint_definition: str = ""
    endpoint_source: str = ""
    expression_scale: str = ""
    platform_id: str = ""
    mapping_reviewed_by: str = ""
    mapping_reviewed_at: str = ""

class ResearchPlan(Contract):
    question: str = Field(min_length=5)
    workflow: Literal["geo_uc", "inventory", "custom"] = "geo_uc"
    evidence_kind: Literal["public_observational", "synthetic", "experimental"] = "public_observational"
    genes: list[str] = Field(default_factory=lambda: ["OSMR", "OSM"])
    cohorts: list[Cohort] = Field(default_factory=list)
    dataset_keys: list[str] = Field(default_factory=list)
    seed: int = 42
    bootstrap_replicates: int = Field(default=10000, ge=10000, le=100000)
    timeout_seconds: int = Field(default=180, ge=1, le=3600)
    memory_gb: int = Field(default=8, ge=1, le=64)
    expected_direction: Literal["higher_in_nonresponders", "lower_in_nonresponders", "unspecified"] = "unspecified"
    multiplicity_family: Literal["all_planned_genes_by_cohorts"] = "all_planned_genes_by_cohorts"
    @model_validator(mode="after")
    def unique_items(self):
        if not self.genes or len(self.genes) != len(set(self.genes)):
            raise ValueError("Genes must be nonempty and unique")
        if len({c.name for c in self.cohorts}) != len(self.cohorts):
            raise ValueError("Cohort names must be unique")
        if self.workflow == "geo_uc" and not self.cohorts:
            raise ValueError("GEO analysis requires cohorts")
        return self
    def keys(self):
        return sorted(set(self.dataset_keys + [k for c in self.cohorts for k in [c.matrix_key,c.sample_key,c.probe_key] if k]))

class EvidenceClaim(Contract):
    claim_id: str
    text: str
    direction: Literal["support", "oppose", "mixed", "unknown"]
    artifact: str
    row_id: str
    limitations: list[str] = Field(default_factory=list)

class DecisionRecord(Contract):
    decision: Literal["pass", "revise", "insufficient_evidence"]
    reviewer: str
    reviewer_type: Literal["automated", "human"]
    reason: str
    human_signature: str | None = None

class AnalysisRun(Contract):
    run_id: str
    execution: Literal["pending", "running", "succeeded", "blocked", "failed", "interrupted"]
    record_integrity: Literal["pending", "complete", "incomplete"]
    scientific_review: Literal["pending", "pass", "revise", "insufficient_evidence"]
    clinical_release: Literal[False] = False

class TargetIndicationHypothesis(Contract):
    target: str
    intervention_direction: Literal["agonism", "antagonism", "depletion", "replacement", "unknown"]
    modality: str
    first_indication: str
    expansion_indications: list[str] = Field(default_factory=list)
    supporting_claims: list[str] = Field(default_factory=list)
    opposing_claims: list[str] = Field(default_factory=list)
    human_owner: str | None = None
