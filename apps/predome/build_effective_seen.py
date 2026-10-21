"""Bind immutable exposure inventory to reviewed identity-only alias components."""
from continuous_audit import ROOT,DATA,digest
from continuous_dataset import core
from predome_pilot import write_new
import json

def build():
    old=DATA/'seen_inventory.json';identity=DATA/'derived_seen_component_manifest.json'
    source=json.loads(old.read_text());d=json.loads(identity.read_text())
    review_path=DATA/'reviews/seen_component_independent_review_v1.json';review=json.loads(review_path.read_text())
    if review['passed']!=review['total'] or review['passed']<1 or review['reviewed_manifest']['sha256']!=digest(identity):
        raise ValueError('Independent seen role review missing or stale')
    if d['alias_collisions']:raise ValueError('Seen alias collisions')
    aliases={}
    for comp in d['components']:
        canon=core(comp['component_id'])
        for alias in comp['normalized_aliases']+[comp['component_id']]:
            alias=core(alias)
            if alias in aliases and aliases[alias]!=canon:raise ValueError('Component collision')
            aliases[alias]=canon
    out=source|{'asset_components':sorted({core(c['component_id']) for c in d['components']}),
                'asset_alias_to_core':aliases,'unresolved_seen_ncts':d['unresolved_seen_ncts'],
                'identity_manifest':{'path':str(identity.relative_to(ROOT)),'sha256':digest(identity)},
                'original_inventory':{'path':str(old.relative_to(ROOT)),'sha256':digest(old)},
                'independent_role_review':{'path':str(review_path.relative_to(ROOT)),'sha256':digest(review_path)},
                'current_identity_only_no_historical_predictors':True,
                'scope':'Exact seen NCTs quarantined even when roles unknown; reviewed experimental components, not shared SOC, propagate exposure.'}
    if set(r['nct_id'] for r in d['records'])!=set(source['nct_ids']):raise ValueError('Incomplete seen NCT identity coverage')
    write_new(DATA/'effective_seen_v1.json',out)
    print(json.dumps({'ncts':len(source['nct_ids']),'cores':len(out['asset_components']),'aliases':len(aliases)}))

if __name__=='__main__':build()
