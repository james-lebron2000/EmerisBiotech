"""Script-free workbench entry for the patient-molecule research branch."""
from html import escape
from .molecule_lab import FIXTURE_VALUES, MOLECULES, simulate_molecule


def render(app, query):
    if set(query) - {'patient', 'molecule', 'cutoff'}:
        raise ValueError('Unexpected molecule scenario parameter')
    patient = query.get('patient', 'P001')
    molecule = query.get('molecule', 'fluorouracil')
    result = simulate_molecule(patient, molecule, int(query.get('cutoff', '7')))
    options = lambda values, current: ''.join(f'<option value="{escape(k)}"'+(' selected' if k == current else '')+f'>{escape(v)}</option>' for k, v in values.items())
    body = '<h1>Patient × Molecule Lab</h1><p>Synthetic paired assays · English research branch</p>'
    body += '<p>Patient-derived assay response. No manual resistance setting. Clinical outcomes remain unavailable.</p>'
    body += '<form method="get"><label>Patient<select name="patient">'+options({k:k for k in FIXTURE_VALUES},patient)+'</select></label><label>Molecule<select name="molecule">'+options({k:v['label'] for k,v in MOLECULES.items()},molecule)+'</select></label><label>Data cutoff<select name="cutoff">'+options({str(k):f'Day {k}' for k in (3,5,7)},str(result['cutoff_day']))+'</select></label><button>Simulate paired assay</button></form>'
    response = result['response']
    body += '<h2>Resistance status</h2><p>Unresolved: viable-cell counts do not identify resistant cells.</p>'
    if response['status'] == 'not_estimable':
        body += '<h2>Not estimable</h2><p>'+escape(response['reason'])+'</p>'
    else:
        body += f'<h2>Day {result["cutoff_day"]} residual viability: {response["terminal_viability_percent"]:.1f}%</h2>'
        body += '<p>Relative to matched vehicle and baseline. Technical replicates only.</p><table><tr><th>Assay day</th><th>Viability (%)</th><th>Evidence</th></tr>'
        body += ''.join(f'<tr><td>{r["day"]}</td><td>{r["viability_percent"]:.1f}</td><td>{r["kind"]}</td></tr>' for r in response['trajectory'])+'</table>'
    body += '<h2>Clinical response / PFS / OS / toxicity</h2><p>Unavailable: no validated clinical translation model.</p><h2>Input fingerprint</h2><code>'+result['input_sha256']+'</code>'
    body += '<p><a href="/patient-lab">Original scenario archive</a></p>'
    return body
