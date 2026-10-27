"""Read-only cohort summary for the script-free workbench."""
from html import escape
from .cohort_lab import cohort_result


def render(app, query):
    if set(query) - {'cutoff', 'day', 'population'}:
        raise ValueError('Unexpected cohort parameter')
    cutoff = int(query.get('cutoff', '7'))
    day = int(query.get('day', '7'))
    population = query.get('population', 'paired')
    if population not in ('paired', 'available') or not 0 <= day <= cutoff + 3:
        raise ValueError('Unsupported cohort view')
    result = cohort_result(cutoff)
    body = '<h1>Virtual Trial Cohort</h1><p>24 synthetic patients · Two fixed molecule exposures · No clinical trial outcomes</p>'
    body += '<p>The same patients are evaluated under each molecule when paired assays exist. Not randomized arms or clinical efficacy estimates.</p>'
    body += '<form method="get"><label>Data cutoff<select name="cutoff">'+''.join(f'<option value="{c}"'+(' selected' if c==cutoff else '')+f'>Day {c}</option>' for c in (3,5,7))+'</select></label>'
    body += '<label>Assay day<input type="number" name="day" min="0" max="10" value="'+str(day)+'"></label><label>Summary population<select name="population">'+''.join('<option value="'+p+'"'+(' selected' if p==population else '')+'>'+label+'</option>' for p,label in [('paired','Complete pairs'),('available','Available cases')])+'</select></label><button>Inspect cohort</button></form>'
    body += f'<p>{len(result["paired_patient_ids"])} complete pairs / 24 patients · Day {day} · '+('Extrapolated, unvalidated' if day>cutoff else 'Measured or interpolated synthetic assay')+'</p>'
    body += '<table><tr><th>Molecule</th><th>Patients</th><th>Median viability</th><th>Patient IQR</th></tr>'
    for molecule, info in result['molecules'].items():
        s=result['summaries'][population][molecule][day]
        body += f'<tr><td>{escape(info["label"])}</td><td>{s["n"]}</td><td>{s["median"]:.1f}%</td><td>{s["q25"]:.1f}–{s["q75"]:.1f}%</td></tr>'
    body += '</table><p>Patient percentiles, not confidence intervals. Available-case groups can have different patients.</p><h2>All patients at the same assay time</h2><table><tr><th>Patient</th><th>Fluorouracil</th><th>Oxaliplatin</th><th>Paired summary</th></tr>'
    for row in result['patients']:
        patient_id=row['patient']['patient_id']
        values=[f'{r["trajectory"][day]["viability_percent"]:.1f}%' if r['status']=='estimable' else 'Not estimable' for r in row['responses'].values()]
        body += '<tr><td>'+patient_id+'</td>'+''.join('<td>'+v+'</td>' for v in values)+'<td>'+('Included' if patient_id in result['paired_patient_ids'] else 'Excluded: missing pair')+'</td></tr>'
    return body+'</table><p>Clinical response, PFS, OS and toxicity: unavailable.</p><p><a href="/molecule-lab">Single-patient lab</a></p>'
