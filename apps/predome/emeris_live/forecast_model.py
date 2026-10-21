"""Frozen PACIFIC-9 research forecasts and endpoint-specific adjudication.

This model is not clinically calibrated. It predicts two distinct observable
targets per comparison: first official primary ITT/BICR PFS HR < 1, and official
prespecified primary PFS statistical superiority. The latter uses a deliberately
approximate single-final-analysis log-rank proxy, not the complete trial SAP.

The recipe was selected before calculating v1 probabilities. Actual evidence and
parameters are supplied as frozen dictionaries; no sources are fetched, no files
are modified and no timestamp is fabricated. Publication, freshness checks and
append-only registry controls belong to the calling layer. Adjudication flags are
reviewer declarations: this code cannot establish whether a source is authentic
or really the first eligible result.
"""

from datetime import date, datetime, timezone
import hashlib
import json
import math
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


VERSION = "pacific9-research-forecast-v1"
NORMAL = NormalDist()
Z95 = NORMAL.inv_cdf(.975)
# Critical parameters are fixed for this version; changing them requires a new
# version, avoiding silent retuning under the same model identity.
FIXED = {
    "model_version": VERSION,
    "candidate_id": "pacific-9", "trial_id": "NCT05221840",
    "prior_log_hr_mean": 0.0, "prior_log_hr_sd": .35,
    "transport_log_hr_sd": .2,
    "assumed_pairwise_events": [200, 400, 600],
    "event_scenario_weights": [1/3, 1/3, 1/3],
    "pairwise_allocation": .5,
    "statistical_success_approximation_two_sided_alpha": .05,
    "alpha_sensitivity": [.025, .05],
    "transport_sd_sensitivity": [.1, .2, .3],
    "classification_probability_threshold": .5,
    "source_study_id": "NCT03822351", "source_id": "coast_final_2025",
}


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _cdf(value: float) -> float:
    return .5 * math.erfc(-value / math.sqrt(2))


def _finite_positive(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)


def _mixture_quantile(probability: float, mean: float, sds: list[float], weights: list[float]) -> float:
    """Quantile of a location-shared normal mixture, not a moment-matched normal."""
    low, high = mean - 12 * max(sds), mean + 12 * max(sds)
    for _ in range(100):
        middle = (low + high) / 2
        cumulative = sum(w * _cdf((middle - mean) / sd) for w, sd in zip(weights, sds))
        if cumulative < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _frechet(probabilities: list[float]) -> list[float]:
    return [max(probabilities), min(1.0, sum(probabilities))]


def _posterior(hr: float, ci: list[float], spec: dict) -> tuple[float, float, float]:
    log_hr = math.log(hr)
    se = (math.log(ci[1]) - math.log(ci[0])) / (2 * Z95)
    precision = 1 / spec["prior_log_hr_sd"] ** 2 + 1 / se ** 2
    variance = 1 / precision
    mean = variance * (spec["prior_log_hr_mean"] / spec["prior_log_hr_sd"] ** 2 + log_hr / se ** 2)
    return mean, variance, se


def _readout(mean: float, posterior_variance: float, transport_sd: float, events: int, allocation: float, alpha: float) -> dict:
    sampling_variance = 1 / (events * allocation * (1 - allocation))
    readout_sd = math.sqrt(posterior_variance + transport_sd ** 2 + sampling_variance)
    critical_estimate = -NORMAL.inv_cdf(1 - alpha/2) * math.sqrt(sampling_variance)
    return {
        "direction_probability": _cdf(-mean / readout_sd),
        "statistical_success_probability": _cdf((critical_estimate - mean) / readout_sd),
        "future_log_hr_sd": readout_sd,
    }


def build_forecast(catalog: dict, spec: dict) -> dict:
    """Build one PACIFIC-9 payload; missing/mismatched evidence raises ValueError.

Other catalog candidates are explicitly listed as abstentions. This function
does not confer prospective-registration status; its caller must refresh sources,
freeze inputs and create the actual dated registry entry before target outcomes.
"""
    for key, expected in FIXED.items():
        if spec.get(key) != expected:
            raise ValueError(f"Frozen v1 parameter mismatch: {key}; create a new version rather than overwrite v1")
    comparisons_spec = spec.get("comparisons", [])
    if [x.get("arm_id") for x in comparisons_spec] != ["oleclumab", "monalizumab"]:
        raise ValueError("Frozen candidate comparisons do not match PACIFIC-9 v1")
    if spec.get("adjudication", {}).get("endpoint") != "BICR_PFS" or spec["adjudication"].get("population") != "ITT":
        raise ValueError("Frozen endpoint/population mismatch")
    candidates = [c for c in catalog.get("candidates", []) if c.get("id") == spec["candidate_id"]]
    if len(candidates) != 1:
        raise ValueError("A unique PACIFIC-9 candidate is required")
    candidate = candidates[0]
    if candidate.get("nct_id") != spec["trial_id"]:
        raise ValueError("Target trial identity mismatch")
    status = candidate.get("status_as_of", {})
    if status.get("code") != "pending_no_public_result_found" or status.get("registry_has_results") is not False or status.get("announcement_date"):
        raise ValueError("Target outcome is not confirmed pending in the input catalog; do not issue a new prospective forecast")
    cutoff = date.fromisoformat(catalog["as_of"])
    if cutoff > datetime.now(timezone.utc).date():
        raise ValueError("Catalog evidence cutoff cannot be in the future (UTC)")
    sources = [s for s in catalog.get("sources", []) if s.get("id") == spec["source_id"]]
    if len(sources) != 1 or not sources[0].get("url"):
        raise ValueError("Matched historical source and URL are required")
    if date.fromisoformat(sources[0]["published_at"]) > cutoff:
        raise ValueError("Historical source postdates evidence cutoff")

    arms = []
    for arm_spec in comparisons_spec:
        matches = [e for e in candidate.get("prior_evidence", []) if e.get("arm") == arm_spec["historical_arm"]]
        if len(matches) != 1:
            raise ValueError("Exactly one final COAST effect is required per comparison")
        evidence = matches[0]
        if evidence.get("nct_id") != spec["source_study_id"] or evidence.get("source_id") != spec["source_id"] or evidence.get("measure") != "HR":
            raise ValueError("Historical study, source or effect measure mismatch")
        if evidence.get("endpoint") != "PFS by investigator, secondary endpoint":
            raise ValueError("COAST historical endpoint changed; re-review before forecasting")
        if date.fromisoformat(evidence["published_at"]) > cutoff:
            raise ValueError("Historical estimate postdates evidence cutoff")
        hr = _finite_positive(evidence.get("estimate"), "HR")
        ci = evidence.get("ci95")
        if not isinstance(ci, list) or len(ci) != 2:
            raise ValueError("A two-sided 95% confidence interval is required")
        ci = [_finite_positive(x, "HR confidence bound") for x in ci]
        if not .01 <= ci[0] < hr < ci[1] <= 100:
            raise ValueError("Historical HR must lie inside a valid, bounded 95% interval")
        mean, variance, se = _posterior(hr, ci, spec)
        event_values = spec["assumed_pairwise_events"]
        weights = spec["event_scenario_weights"]
        central = [_readout(mean, variance, spec["transport_log_hr_sd"], d, spec["pairwise_allocation"],
                            spec["statistical_success_approximation_two_sided_alpha"]) for d in event_values]
        direction = sum(w * r["direction_probability"] for w, r in zip(weights, central))
        statistical = sum(w * r["statistical_success_probability"] for w, r in zip(weights, central))
        target_sd = math.sqrt(variance + spec["transport_log_hr_sd"] ** 2)
        sds = [r["future_log_hr_sd"] for r in central]
        sensitivity = []
        for transport in spec["transport_sd_sensitivity"]:
            for events in event_values:
                for alpha in spec["alpha_sensitivity"]:
                    values = _readout(mean, variance, transport, events, spec["pairwise_allocation"], alpha)
                    sensitivity.append({"transport_sd": transport, "pairwise_events": events,
                                        "two_sided_alpha": alpha, **values})
        arms.append({
            "arm_id": arm_spec["arm_id"], "arm_label": arm_spec["arm_label"],
            "direction_probability": direction,
            "statistical_success_probability": statistical,
            "direction_call": "HR_below_1" if direction >= spec["classification_probability_threshold"] else "HR_not_below_1",
            "statistical_success_call": "meets_primary_PFS_criterion" if statistical >= spec["classification_probability_threshold"] else "does_not_meet_primary_PFS_criterion",
            "hr_estimate_interval_95": [math.exp(_mixture_quantile(p, mean, sds, weights)) for p in (.025, .975)],
            "hr_estimate_interval_kind": "future_estimate_prediction_interval_under_fixed_design_mixture",
            "latent_effect": {"log_hr_mean": mean, "log_hr_sd": target_sd,
                              "hr_median": math.exp(mean),
                              "hr_interval_95": [math.exp(mean-Z95*target_sd), math.exp(mean+Z95*target_sd)],
                              "probability_true_hr_below_1": _cdf(-mean/target_sd),
                              "directly_adjudicable": False},
            "historical_update": {"observed_log_hr": math.log(hr), "approximate_log_hr_se": se,
                                  "posterior_log_hr_mean": mean, "posterior_log_hr_variance": variance,
                                  "prior_log_hr_sd": spec["prior_log_hr_sd"],
                                  "transport_log_hr_sd": spec["transport_log_hr_sd"]},
            "historical_evidence": dict(evidence),
            "assumed_design_mixture": [{"pairwise_events": d, "weight": w, **r} for d, w, r in zip(event_values, weights, central)],
            "sensitivity": sensitivity,
            "adjudication_status": "pending",
        })
    result = {
        "candidate_id": spec["candidate_id"], "trial_id": spec["trial_id"], "trial_name": candidate["trial_name"],
        "model_version": VERSION, "status": "research_forecast_ready_for_freezing",
        "clinical_prediction_validated": False, "clinical_use": False,
        "probability_calibration": "not_established", "prospective_registration_proven": False,
        "evidence_cutoff_date": cutoff.isoformat(), "comparisons": arms,
        "targets": dict(spec["adjudication"]),
        "classification_probability_threshold": spec["classification_probability_threshold"],
        "trial_level": {
            "direction_probability_bounds": _frechet([a["direction_probability"] for a in arms]),
            "statistical_success_probability_bounds": _frechet([a["statistical_success_probability"] for a in arms]),
            "event": "at_least_one_comparison_meets_the_respective_target",
            "method": "Frechet_bounds_without_assuming_independence",
            "direction_probability": None, "statistical_success_probability": None,
            "joint_dependence_fitted": False,
        },
        "assumptions": list(spec["scope_and_assumptions"]),
        "limitations": [
            "HR方向与官方统计学优势是两个不同预测；方向命中不代表试验成功。",
            "统计优势预测以单次PH/log-rank检验近似实际预设规则；未知完整SAP、事件数和跨比较门控可能导致系统误差。",
            "先验和II→III期迁移SD为预先固定研究假设，未经历史外部队列校准；不能把模型概率当作已验证频率。",
            "公开COAST的CI经过四舍五入，反推SE为近似；研究者评估的次要PFS向BICR主要PFS的迁移未验证。",
            "两比较共享对照；各自概率不证明独立，整项至少一成功只报告概率上下界。",
            "没有收到匹配的官方结果时保持pending；不把失访、无公告、未披露HR或只报另一比较当成失败。",
            "来源真实性、首次分析、信息截止与发布前是否已泄漏结果需要独立人工核查。",
        ],
        "source_ids": list(candidate.get("source_ids", [])),
        "spec_sha256": canonical_sha256(spec),
        "catalog_sha256": canonical_sha256(catalog),
        "abstentions": [{"candidate_id": c["id"], "probability": None,
                         "reason": "No frozen endpoint-matched model in pacific9-research-forecast-v1"}
                        for c in catalog.get("candidates", []) if c.get("id") != spec["candidate_id"]],
        "calculation": {"method": "conjugate_normal_update_and_exact_normal_design_mixture",
                        "patient_simulation": False, "random_sampling": False,
                        "model_performance_estimated": False},
    }
    result["payload_sha256"] = canonical_sha256(result)
    return result


class AdjudicationObservation(BaseModel):
    """Reviewer-verified result metadata; missing outcome fields remain pending."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    trial_id: str = Field(min_length=1, max_length=80)
    arm_id: str = Field(min_length=1, max_length=80)
    endpoint: str = Field(min_length=1, max_length=100)
    population: str = Field(min_length=1, max_length=100)
    analysis: str = Field(min_length=1, max_length=150)
    is_first_eligible_official_analysis: bool
    independently_reviewed: bool
    source_authority: Literal["sponsor", "trial_registry", "peer_reviewed_primary_result", "unverified"]
    source_url: HttpUrl
    source_locator: str = Field(min_length=5, max_length=2000)
    published_at_utc: datetime
    observed_hr: float | None = Field(default=None, ge=.001, le=1000)
    hr_source_quote: str | None = Field(default=None, max_length=2000)
    statistical_result: Literal["met_prespecified_primary_criterion", "did_not_meet_prespecified_primary_criterion", "not_reported"] = "not_reported"
    statistical_source_quote: str | None = Field(default=None, max_length=2000)

    @field_validator("published_at_utc")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Publication time requires a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def evidence_for_labels(self):
        if self.observed_hr is not None and not (self.hr_source_quote or "").strip():
            raise ValueError("An observed HR needs its source quotation or exact value locator")
        if self.statistical_result != "not_reported" and not (self.statistical_source_quote or "").strip():
            raise ValueError("Official statistical status needs its exact supporting statement")
        return self


def binary_score(probability: float, outcome: bool) -> dict:
    if isinstance(probability, bool) or not isinstance(outcome, bool) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Scoring requires a known boolean outcome and a probability in [0,1]")
    likelihood = probability if outcome else 1-probability
    return {"brier": (probability - int(outcome)) ** 2,
            "log_loss": -math.log(likelihood) if likelihood else None,
            "log_loss_infinite": likelihood == 0,
            "correct_at_0_5": (probability >= .5) == outcome}


def adjudicate(forecast: dict, observation: AdjudicationObservation | dict, *, frozen_at_utc: str | datetime) -> dict:
    """Score distinct targets only after matching a reviewer-verified official result.

The caller must guarantee this is the immutable published forecast and persist
the returned adjudication as a new record, never overwrite the forecast. This
function verifies the payload content hash, but hashes do not prove timestamps.
"""
    obs = observation if isinstance(observation, AdjudicationObservation) else AdjudicationObservation(**observation)
    frozen = datetime.fromisoformat(frozen_at_utc.replace("Z", "+00:00")) if isinstance(frozen_at_utc, str) else frozen_at_utc
    if frozen.tzinfo is None:
        raise ValueError("Frozen time requires a timezone")
    if frozen.astimezone(timezone.utc).date() < date.fromisoformat(forecast["evidence_cutoff_date"]):
        raise ValueError("Forecast freeze cannot predate its evidence cutoff")
    payload = {k: v for k, v in forecast.items() if k != "payload_sha256"}
    if canonical_sha256(payload) != forecast.get("payload_sha256"):
        raise ValueError("Forecast content hash mismatch")
    target = forecast["targets"]
    reasons = []
    if obs.trial_id != forecast["trial_id"]:
        reasons.append("trial_mismatch")
    matches = [c for c in forecast["comparisons"] if c["arm_id"] == obs.arm_id]
    if len(matches) != 1:
        reasons.append("comparison_mismatch")
    if obs.endpoint != target["endpoint"] or obs.population != target["population"] or obs.analysis != target["analysis"]:
        reasons.append("endpoint_population_or_analysis_mismatch")
    if not obs.is_first_eligible_official_analysis or not obs.independently_reviewed or obs.source_authority == "unverified":
        reasons.append("official_first_result_not_verified")
    if obs.published_at_utc <= frozen:
        reasons.append("outcome_not_published_after_forecast_freeze")
    if obs.published_at_utc > datetime.now(timezone.utc):
        reasons.append("future_result_publication")
    if obs.observed_hr is not None and obs.observed_hr > 1 and obs.statistical_result == "met_prespecified_primary_criterion":
        reasons.append("conflicting_hr_and_favorable_statistical_statement")
    result = {"forecast_payload_sha256": forecast["payload_sha256"], "trial_id": forecast["trial_id"],
              "arm_id": obs.arm_id, "observation": obs.model_dump(mode="json"),
              "status": "ineligible" if reasons else "pending", "reasons": reasons,
              "direction": {"outcome": None, "score": None},
              "statistical_success": {"outcome": None, "score": None},
              "trial_ids_for_dedup": [], "comparison_count": 0, "target_count_scored": 0,
              "aggregation_rule": "Deduplicate trial_ids_for_dedup across arms; target and comparison counts are not independent trials.",
              "rounding_boundary": obs.observed_hr == 1,
              "clinical_validation_claim": False}
    if not reasons:
        arm = matches[0]
        if obs.observed_hr is not None:
            outcome = obs.observed_hr < 1
            result["direction"] = {"outcome": outcome, "score": binary_score(arm["direction_probability"], outcome)}
        if obs.statistical_result != "not_reported":
            outcome = obs.statistical_result == "met_prespecified_primary_criterion"
            result["statistical_success"] = {"outcome": outcome, "score": binary_score(arm["statistical_success_probability"], outcome)}
        count = sum(result[k]["outcome"] is not None for k in ("direction", "statistical_success"))
        result["target_count_scored"] = count
        result["comparison_count"] = int(count > 0)
        # IDs must be unioned across arm adjudications, never sum per-arm trials.
        result["trial_ids_for_dedup"] = [forecast["trial_id"]] if count else []
        result["status"] = "adjudicated" if count == 2 else "partially_adjudicated" if count else "pending"
    result["adjudication_sha256"] = canonical_sha256(result)
    return result
