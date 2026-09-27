"""Offline, prospective-record admission and scoring. Does not generate forecasts."""
from datetime import datetime
from typing import Literal
import math
from pydantic import Field, model_validator, AwareDatetime
from .models import Contract

class TrialForecast(Contract):
    trial_id: str = Field(pattern=r'^NCT[0-9]{8}$')
    asset_group: str = Field(min_length=2)
    phase: Literal[2,3]
    endpoint_definition: str = Field(min_length=10)
    task: Literal['primary_endpoint'] = 'primary_endpoint'
    cutoff: AwareDatetime
    recorded_at: AwareDatetime
    source_latest_public_at: AwareDatetime
    training_latest_label_at: AwareDatetime
    model_version: str = Field(min_length=3)
    source_manifest_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    probability: float = Field(ge=0,le=1,allow_inf_nan=False)
    baseline_probability: float = Field(gt=0,lt=1,allow_inf_nan=False)
    commercial_rights: Literal['verified','unknown','noncommercial']
    outcome: Literal['success','failure','pending','mixed','administrative_stop']
    outcome_public_at: AwareDatetime | None = None
    outcome_source: str | None = None
    outcome_adjudicator: str | None = None
    synthetic: bool = False
    @model_validator(mode='after')
    def chronological(self):
        if self.source_latest_public_at>self.cutoff:raise ValueError('Post-cutoff source leakage')
        if self.training_latest_label_at>=self.cutoff:raise ValueError('Training label leakage')
        if self.recorded_at>self.cutoff:raise ValueError('Not a prospectively recorded forecast; use a separately audited historical replay')
        if self.outcome in ['success','failure']:
            if not self.outcome_public_at or self.outcome_public_at<=self.cutoff:raise ValueError('Outcome known at prediction time or undated')
            if not self.outcome_source or not self.outcome_adjudicator:raise ValueError('Outcome requires source and adjudication')
        return self

def score_forecasts(rows,training_asset_groups=()):
    records=[TrialForecast.model_validate(r) for r in rows]
    ids=[r.trial_id for r in records]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate trial; select one frozen prediction horizon')
    if any(r.asset_group in training_asset_groups for r in records):raise ValueError('Asset overlaps training and test')
    if any(r.commercial_rights!='verified' for r in records):raise ValueError('Commercial-use rights not verified')
    if len({r.synthetic for r in records})>1:raise ValueError('Cannot mix synthetic and real evidence')
    phases={}
    for phase in [2,3]:
        eligible=[r for r in records if r.phase==phase and r.outcome in ['success','failure']]
        result={'n':len(eligible),'excluded_unresolved':sum(r.phase==phase and r.outcome not in ['success','failure'] for r in records),'status':'insufficient_evidence','brier':None,'baseline_brier':None,'log_loss':None}
        if eligible:
            y=[int(r.outcome=='success') for r in eligible]
            result.update(brier=sum((r.probability-v)**2 for r,v in zip(eligible,y))/len(y),baseline_brier=sum((r.baseline_probability-v)**2 for r,v in zip(eligible,y))/len(y),log_loss=-sum(v*math.log(max(1e-15,r.probability))+(1-v)*math.log(max(1e-15,1-r.probability)) for r,v in zip(eligible,y))/len(y),status='descriptive_only')
        phases[str(phase)]=result
    return {'phases':phases,'synthetic':bool(records) and records[0].synthetic,'clinical_predictive_validity_established':False,'investment_edge_established':False,'trade_authorized':False,'note':'Scoring is not independent timestamp authentication, calibration validation, or a return backtest.'}
