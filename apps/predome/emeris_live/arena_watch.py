"""Current formal cohort selection for intake; no schema creation or network."""
import re
from .arena_store import instant

INACTIVE={'withdrawn_before_execution','withdrawn','superseded'}


def current_targets(snapshot):
    cohorts=[c for c in snapshot['cohorts'] if
        c.get('protocol',{}).get('season_kind')=='registered_12_project_season'
        and c.get('execution_status') not in INACTIVE]
    if not cohorts:return []
    current=max(cohorts,key=lambda c:instant(c['registered_at']))['id']
    targets=[t for t in snapshot['targets'] if t.get('cohort_id')==current
        and t.get('execution_status') not in INACTIVE and t.get('status') not in INACTIVE]
    if any(not re.fullmatch(r'NCT[0-9]{8}',str(t.get('nct_id',''))) for t in targets):
        raise ValueError('Current Arena watch contains an invalid registry identity')
    return sorted(targets,key=lambda t:t['id'])


def current_trial_ids(store):
    from .arena_public import read_frozen
    return list(dict.fromkeys(t['nct_id'] for t in current_targets(read_frozen(store))))
