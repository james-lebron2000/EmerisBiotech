"""Source-backed result intervals; never fabricate an exact first disclosure day."""
from datetime import date,timedelta

SPLITS={'train':('2021-01-01','2023-01-01'),'calibration':('2023-01-01','2024-01-01'),'test':('2024-01-01','2026-01-01')}

def result_interval(row):
    exact=row.get('decisive_primary_at');interval=row.get('decisive_primary_interval')
    if exact:
        d=date.fromisoformat(exact)
        if interval:raise ValueError('Use exact date or interval, never conflicting representations')
        return d.isoformat(),(d+timedelta(days=1)).isoformat()
    if not interval:return None
    lo=date.fromisoformat(interval['lower']).isoformat();hi=date.fromisoformat(interval['upper_exclusive']).isoformat()
    if lo>=hi or interval.get('precision') not in ['day','month','year','documented_range']:raise ValueError('Invalid source date interval')
    return lo,hi

def time_split(row):
    value=result_interval(row)
    if not value:return 'excluded'
    lo,hi=value
    matches=[name for name,(a,b) in SPLITS.items() if a<=lo and hi<=b]
    return matches[0] if len(matches)==1 else 'excluded'
