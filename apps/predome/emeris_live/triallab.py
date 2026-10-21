"""Unvalidated PH/log-rank design workbench; this is not a clinical predictor.

Inputs are explicit user assumptions, optionally updated by user-supplied matched
historical log-HR estimates. Source/review declarations are NOT independently
verified here. No candidate evidence is automatically imported. The only success
event is a favorable rejection for ONE survival endpoint at ONE final analysis.
For theta=log(HR), I=D*q*(1-q): Z|theta ~ N(theta*sqrt(I), 1).
P(favorable rejection|theta)=Phi(-z_(1-alpha/2)-theta*sqrt(I)).
Normal convolution integrates this exactly against the effect distribution;
there is no LLM, random sampling, patient simulation, or fitted clinical model.
"""

from datetime import date, datetime, timezone
import hashlib
import json
import math
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


VERSION = "triallab-0.1.0"
NORMAL = NormalDist()
Z95 = NORMAL.inv_cdf(0.975)
Endpoint = Literal["PFS", "OS", "DFS", "EFS"]


class HistoricalEffect(BaseModel):
    """One independent, user-reviewed historical study's log-HR and SE.

SE is on the log scale, not the HR scale. Review/matching flags are declarations;
the backend cannot establish matching, independence or source authenticity.
"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    trial_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    endpoint: Endpoint
    log_hr: float = Field(ge=-3, le=3)
    log_hr_se: float = Field(ge=0.001, le=2)
    source_url: HttpUrl
    source_note: str = Field(min_length=10, max_length=1200)
    published_date: date
    matching_rationale: str = Field(min_length=20, max_length=1200)
    reviewed: Literal[True]


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["scenario", "evidence_conditioned"] = "scenario"
    endpoint: Endpoint = "PFS"
    analysis: Literal["single_final_ph_logrank"] = "single_final_ph_logrank"
    prior_log_hr_mean: float = Field(default=math.log(0.75), ge=-3, le=3)
    prior_log_hr_sd: float = Field(default=0.15, ge=0, le=1.5)
    event_count: int = Field(default=400, ge=10, le=100000, strict=True)
    allocation: float = Field(default=0.5, ge=0.05, le=0.95)
    alpha: float = Field(default=0.05, ge=0.0001, le=0.2)
    clinical_hr_threshold: float = Field(default=0.8, ge=0.05, le=1)
    # These are assumptions, NOT heterogeneity estimates learned from the data.
    between_study_sd: float = Field(default=0.15, ge=0, le=1.5)
    transport_sd: float = Field(default=0, ge=0, le=1.5)
    historical_effects: list[HistoricalEffect] = Field(default_factory=list, max_length=50)
    candidate_id: str | None = Field(default=None, min_length=1, max_length=100,
                                     pattern=r"^[A-Za-z0-9_.:-]+$")
    target_trial_id: str | None = Field(default=None, min_length=1, max_length=80,
                                        pattern=r"^[A-Za-z0-9_.:-]+$")
    cutoff_date: date | None = None
    seed: int | None = Field(default=None, ge=0, le=1000000, strict=True)

    @model_validator(mode="after")
    def evidence_gate(self):
        if self.mode == "scenario":
            if self.historical_effects:
                raise ValueError("Historical evidence requires evidence_conditioned mode.")
            if self.candidate_id or self.target_trial_id:
                raise ValueError("Scenario assumptions cannot be attached to a named clinical candidate.")
            return self
        if not self.historical_effects:
            raise ValueError("Insufficient evidence: no historical effects; no candidate probability produced.")
        if not self.target_trial_id or not self.cutoff_date:
            raise ValueError("Evidence-conditioned analysis requires target_trial_id and cutoff_date.")
        if self.cutoff_date > datetime.now(timezone.utc).date():
            raise ValueError("Evidence cutoff cannot be a future date (UTC); use scenario mode for hypothetical inputs.")
        if self.prior_log_hr_sd == 0:
            raise ValueError("Evidence-conditioned analysis requires a non-degenerate prior SD.")
        ids = [e.trial_id.casefold() for e in self.historical_effects]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate historical trials are not independent evidence.")
        for evidence in self.historical_effects:
            if evidence.trial_id.casefold() == self.target_trial_id.casefold():
                raise ValueError("Target trial outcomes cannot be used as historical evidence.")
            if evidence.published_date > self.cutoff_date:
                raise ValueError("Evidence published after cutoff is not eligible.")
            if evidence.endpoint != self.endpoint:
                raise ValueError("Endpoint mismatch: different survival endpoints cannot be pooled.")
        return self


def _cdf(z: float) -> float:
    # erfc preserves small lower-tail probabilities better than 1+erf.
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _below(threshold: float, mean: float, sd: float) -> float:
    if sd == 0:
        return float(mean < threshold)
    return _cdf((threshold - mean) / sd)


def favorable_power(log_hr: float, event_count: int, allocation: float, alpha: float) -> float:
    """Approximate probability of a *favorable* two-sided rejection, not either tail."""
    information = event_count * allocation * (1 - allocation)
    return _cdf(-NORMAL.inv_cdf(1 - alpha / 2) - log_hr * math.sqrt(information))


def _effect(req: TrialRequest) -> tuple[float, float, dict]:
    mean = req.prior_log_hr_mean
    variance = req.prior_log_hr_sd ** 2
    if req.mode == "scenario":
        return mean, math.sqrt(variance + req.transport_sd ** 2), {
            "method": "user_assumed_normal_log_hr",
            "historical_study_count": 0,
            "between_study_sd_used": False,
            "transport_sd": req.transport_sd,
        }
    prior_precision = 1 / variance
    precision = prior_precision
    numerator = mean * precision
    for item in req.historical_effects:
        weight = 1 / (item.log_hr_se ** 2 + req.between_study_sd ** 2)
        numerator += item.log_hr * weight
        precision += weight
    posterior_mean = numerator / precision
    posterior_variance = 1 / precision
    # Predict a NEW study effect: retain assumed between-study variation as
    # well as common-mean uncertainty and additional target transport error.
    target_variance = posterior_variance + req.between_study_sd ** 2 + req.transport_sd ** 2
    return posterior_mean, math.sqrt(target_variance), {
        "method": "normal_normal_common_mean_with_fixed_assumed_heterogeneity",
        "historical_study_count": len(req.historical_effects),
        "common_mean_posterior_log_hr": posterior_mean,
        "common_mean_posterior_sd": math.sqrt(posterior_variance),
        "prior_precision_fraction": prior_precision / precision,
        "between_study_sd_used": True,
        "assumed_between_study_sd": req.between_study_sd,
        "transport_sd": req.transport_sd,
        "heterogeneity_fitted": False,
        "matching_and_review": "user_declared_not_independently_verified",
    }


def execute(req: TrialRequest) -> dict:
    """Return reproducible, explicitly unvalidated design calculations.

No results timestamp is generated. Hash covers complete inputs, code version,
assumptions and calculated outputs. Hash is not a trusted prospective timestamp.
An optional seed is recorded but unused because integration is analytic.
"""
    mean, sd, fit = _effect(req)
    information = req.event_count * req.allocation * (1 - req.allocation)
    sampling_se = 1 / math.sqrt(information)
    critical_z = NORMAL.inv_cdf(1 - req.alpha / 2)
    readout_sd = math.sqrt(sd ** 2 + sampling_se ** 2)
    assurance = _cdf((-critical_z * sampling_se - mean) / readout_sd)
    curve = []
    for offset in (-2, -1, 0, 1, 2):
        theta = mean + offset * sd
        curve.append({"log_hr_sd_offset": offset, "hr": math.exp(theta),
                      "conditional_power": favorable_power(theta, req.event_count, req.allocation, req.alpha)})

    result = {
        "version": VERSION,
        "mode": req.mode,
        "evidence_kind": "user_assumptions" if req.mode == "scenario" else "user_supplied_historical_aggregate_effects",
        "status": "scenario_only" if req.mode == "scenario" else "unvalidated_evidence_conditioned_calculation",
        "clinical_use": False,
        "clinical_prediction_validated": False,
        "candidate_forecast": None,
        "prospective_prediction_registered": False,
        "inputs": req.model_dump(mode="json"),
        "summary": {
            "conditional_power_at_median": favorable_power(mean, req.event_count, req.allocation, req.alpha),
            "assurance": assurance,
            "probability_hr_below_1": _below(0, mean, sd),
            "probability_hr_below_clinical_threshold": _below(math.log(req.clinical_hr_threshold), mean, sd),
            "clinical_hr_threshold": req.clinical_hr_threshold,
            "assurance_label": "指定假设下单一生存终点在最终分析中向有利方向拒绝原假设的概率",
        },
        "effect_distribution": {
            "label": "假定真实效应分布" if req.mode == "scenario" else "给定证据和可迁移性假设的目标研究真实效应分布",
            "log_hr_mean": mean,
            "log_hr_sd": sd,
            "hr_median": math.exp(mean),
            "hr_interval_95": [math.exp(mean - Z95 * sd), math.exp(mean + Z95 * sd)],
            "interval_kind": "assumed_effect_interval" if req.mode == "scenario" else "model_conditional_target_effect_interval",
        },
        "future_readout_distribution": {
            "label": "包含研究抽样误差的未来 log-HR 估计分布",
            "log_hr_mean": mean,
            "log_hr_sd": readout_sd,
            "hr_interval_95": [math.exp(mean - Z95 * readout_sd), math.exp(mean + Z95 * readout_sd)],
            "interval_kind": "model_conditional_future_estimate_prediction_interval",
        },
        "fit": fit,
        "design": {"information": information, "approximate_log_hr_se": sampling_se,
                   "two_sided_alpha": req.alpha, "favorable_critical_z": -critical_z,
                   "success_rule": "estimated_log_hr / SE < -Phi_inverse(1-alpha/2)",
                   "harmful_direction_rejection_counts_as_success": False},
        "sensitivity": curve,
        "calculation": {"integration": "exact_gaussian_convolution", "monte_carlo_error": 0,
                        "seed_used": False, "clinical_performance_tested": False},
        "assumptions": [
            "单一时间到事件终点、两组随机比较、比例风险、最终一次分析的正态/log-rank 近似。",
            "HR 小于 1 表示事件风险降低；事件必须是不利结局，不能用于好事件发生率。",
            "event_count 是两组最终可分析事件总数，不是入组患者人数；allocation 是治疗组分配比例。",
            "固定事件信息 D*q*(1-q)；实际风险集分配、分层、协变量、删失与依从性差异可能改变信息量。",
            "alpha 为单个终点最终检验的双侧水平；未实施多重终点、期中花费、非劣效或联合成功规则。",
            "未来研究读数区间含抽样误差；真实效应分布区间不含该误差，两者不能混用。",
            "情景输入和异质性/迁移误差均是用户假设；历史证据的来源、匹配及独立性需另外复核。",
        ],
        "limitations": [
            "未验证的研究与设计工具；不预测具体候选药是否获批，不输出临床或投资决策建议。",
            "不支持二分类、连续终点、非比例风险、非劣效、单臂试验或多个主要终点的整体成功概率。",
            "没有依据单细胞、类器官、基因组或虚拟患者生成临床效应；默认 HR/SD 为演示假设。",
            "历史数据更新假定终点和比较足够匹配；不能用目标试验结果回填后声称事前预测。",
            "证据截止日不得晚于当前 UTC 日期；来源时间仅做字段检查，不能排除遗漏资料、同资产相关性或人为日期错误。",
            "解析积分消除了 Monte Carlo 误差，不消除近似误差、机制失配或证据偏倚。",
            "结果哈希证明内容一致性，不证明预测事前存在；无第三方时间戳或独立临床性能验证。",
        ],
    }
    if req.event_count < 100:
        result["limitations"].append("事件数较少，正态近似可能不可靠，应使用更完整的设计模拟复核。")
    result["result_sha256"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    return result
