"""Script-free patient showcase using the workbench shell and domain results."""
import html
import math
from urllib.parse import urlencode
from .patient_lab import PLANS, snapshot

COLORS = {'a': '#16858a', 'b': '#8961b0', 'ab': '#bd7728'}
DASH = {'a': '', 'b': '7 4', 'ab': '2 4'}


def esc(value):
    return html.escape(str(value), quote=True)


STYLE = '''<style>
.patientlab{--pl-a:#16858a;--pl-b:#8961b0;--pl-ab:#bd7728}.patientlab .pl-head{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:start}.patientlab .pl-kicker{font-size:11px;letter-spacing:1.8px;color:var(--muted);margin:0 0 6px}.patientlab h1{margin:0 0 9px}.patientlab .pl-sub{color:var(--muted);margin:0 0 15px}.patientlab .pl-pill{display:inline-block;background:var(--teal-light);color:#12685d;border-radius:6px;padding:6px 10px;font-size:12px}.patientlab .pl-banner{background:#fff3da;padding:12px 16px;border-radius:8px;line-height:1.8;margin:16px 0;color:#5c4820}.patientlab .pl-flow{display:flex;flex-wrap:wrap;gap:9px 20px;font-size:12px;color:var(--muted);margin:18px 0}.patientlab .pl-flow b{font-weight:600;color:#166b66}.patientlab .pl-layout{display:grid;grid-template-columns:255px minmax(0,1fr);gap:20px;align-items:start}.patientlab .pl-profile,.patientlab .pl-stage{background:white;border:1px solid var(--line);border-radius:10px;padding:18px;min-width:0}.patientlab h2{font-size:16px;margin:0 0 12px}.patientlab h3{font-size:13px;margin:15px 0 8px}.patientlab .pl-id{font-size:23px;letter-spacing:.5px;font-weight:600}.patientlab .pl-muted{font-size:12px;color:var(--muted);line-height:1.8}.patientlab .pl-fields{margin:8px 0 0}.patientlab .pl-fields div{padding:9px 0;border-bottom:1px solid var(--line)}.patientlab .pl-fields dt{font-size:11px;color:var(--muted)}.patientlab .pl-fields dd{margin:2px 0 0;font-size:12px;overflow-wrap:anywhere}.patientlab details{margin-top:12px}.patientlab summary{cursor:pointer;font-size:12px;color:#166b66}.patientlab .pl-form{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:16px 0}.patientlab label{font-size:12px;display:block}.patientlab select,.patientlab input[type=range]{max-width:100%}.patientlab select{font-size:13px;min-height:36px;margin-top:5px}.patientlab button,.patientlab .button{font-size:12px;min-height:36px}.patientlab .pl-plans{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0 0 14px}.patientlab .pl-plan{padding:12px;border:1px solid var(--line);border-radius:8px;background:#fff;text-decoration:none;color:var(--ink);min-width:0}.patientlab .pl-plan[aria-current=true]{border:2px solid #166b66;padding:11px;background:#f4faf9}.patientlab .pl-plan small{display:block;color:var(--muted);font-size:11px;margin-top:5px}.patientlab .pl-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}.patientlab .pl-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:15px}.patientlab .pl-tabs a{font-size:12px;padding:7px 10px;text-decoration:none;color:var(--muted);border-radius:6px}.patientlab .pl-tabs a[aria-current=page]{background:var(--teal-light);color:#166b66}.patientlab .pl-chart{display:block;width:100%;height:auto;max-height:310px}.patientlab .pl-chart text{font:13px sans-serif;fill:#5e747e}.patientlab .pl-chart .pl-grid{stroke:#dce5e9;stroke-width:1}.patientlab .pl-metrics{display:grid;grid-template-columns:1fr 1fr;gap:15px;border-top:1px solid var(--line);padding-top:13px}.patientlab .pl-value{font-size:27px;font-weight:600;display:block;margin:6px 0}.patientlab .pl-cellbar{height:10px;display:flex;border-radius:4px;overflow:hidden;margin:12px 0 8px}.patientlab .pl-row{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.patientlab .pl-row small{font-size:11px;color:var(--muted)}.patientlab .pl-run{padding:13px 0;border-top:1px solid var(--line);font-size:12px}.patientlab code{overflow-wrap:anywhere;word-break:break-all}.patientlab .pl-history{display:flex;gap:8px;flex-wrap:wrap}.patientlab .pl-history a{font-size:12px;padding:6px 9px;border:1px solid var(--line);border-radius:5px}.patientlab .pl-table{width:100%;border-collapse:collapse;font-size:12px}.patientlab .pl-table th,.patientlab .pl-table td{padding:11px 7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere;max-width:none}.patientlab .pl-footer{margin-top:15px;font-size:11px;color:var(--muted);line-height:1.8}.patientlab .pl-scroll{overflow-x:auto}.patientlab .pl-compare{display:grid;grid-template-columns:1fr 1fr;gap:16px}.patientlab .pl-evidence{font-size:12px;line-height:1.9}.patientlab .pl-evidence dd{margin:3px 0 12px;color:var(--muted)}
.patientlab .pl-chart-small{display:none}@media(max-width:1100px){.patientlab .pl-chart-wide{display:none}.patientlab .pl-chart-small{display:block}.patientlab .pl-layout{grid-template-columns:220px minmax(0,1fr)}}
@media(max-width:800px){.patientlab .pl-layout{grid-template-columns:1fr}.patientlab .pl-profile .pl-fields{display:grid;grid-template-columns:1fr 1fr;gap:0 15px}.patientlab .pl-compare{grid-template-columns:1fr}}
@media(max-width:440px){.patientlab .pl-profile .pl-fields,.patientlab .pl-metrics{grid-template-columns:1fr}.patientlab .pl-profile,.patientlab .pl-stage{padding:13px}.patientlab .pl-plan{padding:10px 7px;font-size:12px}.patientlab .pl-plan[aria-current=true]{padding:9px 6px}.patientlab .pl-tabs{gap:2px}.patientlab .pl-tabs a{padding:8px 6px}}
</style>'''


def chart(curves, selected, week=12, survival=False):
    return _chart(curves, selected, week, survival, 680) + _chart(curves, selected, week, survival, 280)


def _chart(curves, selected, week, survival, width):
    height = 225 if width == 280 else 265
    left, right, top, bottom = 48, 14, 30, 40
    metric = 'illustrative_percent' if survival else 'burden_index'
    xs = 'month' if survival else 'week_after_cutoff'
    ymax = 100 if survival else math.ceil(max(row['scenario_high'] for rows in curves.values() for row in rows) / 20) * 20
    x = lambda t: left + t / 24 * (width - left - right)
    y = lambda v: height - bottom - v / ymax * (height - top - bottom)
    size_class = 'pl-chart-small' if width == 280 else 'pl-chart-wide'
    parts = [f'<svg class="pl-chart {size_class}" viewBox="0 0 {width} {height}" role="img" aria-label="'+('Mathematical survival illustration, not patient prognosis' if survival else 'Burden scenarios for three fictional treatments, not clinical predictions')+'"><title>'+('Independent mathematical illustration' if survival else 'Synthetic burden trajectories')+'</title>']
    parts.append(f'<text x="{left}" y="17">'+('Illustrative survival (%)' if survival else 'Burden index (baseline=100)')+'</text>')
    for v in (0, ymax / 2, ymax):
        parts.append(f'<line class="pl-grid" x1="{left}" x2="{width-right}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text x="{left-7}" y="{y(v)+4:.1f}" text-anchor="end">{v:g}</text>')
    for t in (0, 8, 16, 24):
        parts.append(f'<text x="{x(t):.1f}" y="{height-20}" text-anchor="'+('start' if t == 0 else 'end' if t == 24 else 'middle')+f'">{t}</text>')
    if not survival:
        rows = curves[selected]
        upper = [(x(r[xs]), y(r['scenario_high'])) for r in rows]
        lower = [(x(r[xs]), y(r['scenario_low'])) for r in reversed(rows)]
        points = ' '.join(f'{a:.1f},{b:.1f}' for a, b in upper + lower)
        parts.append(f'<polygon points="{points}" fill="{COLORS[selected]}" opacity=".12"/>')
    for key in PLANS:
        rows = curves[key]
        path = ' '.join(('M' if i == 0 else 'L') + f'{x(r[xs]):.1f},{y(r[metric]):.1f}' for i, r in enumerate(rows))
        parts.append(f'<path d="{path}" fill="none" stroke="{COLORS[key]}" stroke-width="{3 if key == selected else 1.8}" stroke-dasharray="{DASH[key]}"/>')
    if not survival:
        v = curves[selected][week][metric]
        parts.append(f'<line x1="{x(week):.1f}" x2="{x(week):.1f}" y1="{top}" y2="{height-bottom}" stroke="#7c909a" stroke-dasharray="3 4"/><circle cx="{x(week):.1f}" cy="{y(v):.1f}" r="4" fill="{COLORS[selected]}"/>')
    parts.append(f'<text x="{width-right}" y="{height-2}" text-anchor="end">'+('Scenario time (months)' if survival else 'Weeks after current cutoff')+'</text></svg>')
    return ''.join(parts)


def render(app, query):
    if set(query) - {'id', 'plan', 'week', 'view'}:
        raise ValueError('Unknown patient scenario query')
    selected, view = query.get('plan', 'a'), query.get('view', 'response')
    try:
        week = int(query.get('week', '12'))
    except (ValueError, TypeError):
        raise ValueError('Invalid scenario week') from None
    if selected not in PLANS or view not in ('response', 'prognosis', 'evidence') or not 0 <= week <= 24:
        raise ValueError('Invalid patient scenario view')
    stored = app.patient_lab.get(query['id']) if query.get('id') else None
    data = stored['result'] if stored else None
    patient = data['patient'] if data else snapshot()
    def url(**changes):
        params = {'plan': selected, 'week': week, 'view': view}
        if stored:
            params['id'] = stored['run_id']
        params.update(changes)
        return '/patient-lab?' + esc(urlencode(params))
    def fields(values):
        return '<dl class="pl-fields">' + ''.join('<div><dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd></div>' for k, v in values.items()) + '</dl>'
    token = '<input type="hidden" name="token" value="'+esc(app.token)+'">'
    body = STYLE + '<section class="patientlab"><div class="pl-head"><div><p class="pl-kicker">PATIENT SCENARIO LAB</p><h1>One patient. Multiple futures.</h1><p class="pl-sub">Clinical history × biological state → treatment scenarios → follow-up updates</p></div><span class="pl-pill">Synthetic patient · Research showcase</span></div>'
    body += '<div class="pl-banner"><strong>Synthetic patient and fictional treatments.</strong>Values follow explicit illustrative assumptions, not predictions of real treatment benefit, toxicity or lifespan.</div><div class="pl-flow"><span>1. Review patient data</span><span>2. Set assumptions</span><b>3. Run and save</b><span>4. Add follow-up versions</span></div>'
    body += '<div class="pl-layout"><aside class="pl-profile"><span class="pl-id">'+esc(patient['patient_id'])+'</span><p class="pl-muted">Data cutoff: week '+str(patient['cutoff_week'])+'<br>Anonymous identifier · Synthetic data</p><h2>Clinical context</h2>'+fields(patient['clinical'])+'<details><summary>Biology and missing information</summary>'+fields(patient['molecular'])+'</details><h3>Available synthetic observations</h3>'
    for obs in patient['observations']:
        body += '<p class="pl-muted">Week '+str(obs['week'])+' week · index '+str(obs['burden_index'])+'<br>'+esc(obs['source_id'])+'</p>'
    body += '<p class="pl-muted">Anatomy and omics are context only, not model inputs. Missing does not mean negative.</p></aside><div>'
    body += '<form class="pl-form" method="post" action="/patient-lab/run">'+token+'<label for="resistance">Resistance assumption for a new baseline run<select id="resistance" name="resistance"><option value="low"'+(' selected' if not data or data['resistance_assumption'] == 'low' else '')+'>Low resistance: 20%</option><option value="high"'+(' selected' if data and data['resistance_assumption'] == 'high' else '')+'>High resistance: 45%</option></select></label><button type="submit">Run and save a new scenario</button></form>'
    if not data:
        body += '<div class="pl-stage"><h2>Explore alternative futures for P001</h2><p class="pl-muted">Run a scenario to calculate 24-week trajectories for fictional A, B and A+B and save a record you can reopen. Explore prognosis illustrations, export JSON, then load the predefined week-6 and week-12 synthetic follow-ups.</p><p class="pl-muted">Viewing this page does not create records or execute the model. No scenario has been run yet.</p></div>'
    else:
        body += '<div class="pl-plans">'
        for key, plan in PLANS.items():
            body += '<a class="pl-plan" aria-current="'+str(key == selected).lower()+'" href="'+url(plan=key)+'"><span class="pl-dot" style="background:'+COLORS[key]+'"></span>'+esc(plan['label'])+'<small>'+esc(plan['description'])+'</small></a>'
        body += '</div><form method="get" action="/patient-lab" class="pl-form"><input type="hidden" name="id" value="'+stored['run_id']+'"><input type="hidden" name="plan" value="'+selected+'"><input type="hidden" name="view" value="'+view+'"><label for="scenario-week">Inspect week '+str(week)+'<input id="scenario-week" name="week" type="range" min="0" max="24" value="'+str(week)+'"></label><button type="submit" class="secondary">Update view</button><span class="pl-muted">Run assumption: '+('20%' if data['resistance_assumption'] == 'low' else '45%')+' initial resistance</span></form><div class="pl-stage"><nav class="pl-tabs" aria-label="Scenario result views">'
        for key, label in [('response', 'Burden and cell state'), ('prognosis', 'Response and prognosis'), ('evidence', 'Evidence and gaps')]:
            body += '<a href="'+url(view=key)+'"'+(' aria-current="page"' if view == key else '')+'>'+label+'</a>'
        body += '</nav>'
        if view == 'response':
            rows = {k: v['trajectory'] for k, v in data['forecasts'].items()}
            point = rows[selected][week]
            resistant = point['resistant_fraction'] * 100
            body += '<h2>Future burden trajectories</h2><p class="pl-muted">Shared starting state. The band is an illustrative scenario span, not a confidence interval. Solid: A; dashed: B; dotted: A+B.</p>'+chart(rows, selected, week)
            body += '<div class="pl-metrics" aria-live="polite"><div><span class="pl-muted">'+esc(PLANS[selected]['label'])+' · '+str(week)+'-week burden index</span><strong class="pl-value">'+f'{point["burden_index"]:.1f}'+'</strong><span class="pl-muted">Illustrative span '+f'{point["scenario_low"]:.1f}–{point["scenario_high"]:.1f}'+'</span></div><div><span class="pl-muted">Simulated cell composition, not assay results</span><div class="pl-cellbar"><span style="width:'+f'{100-resistant:.2f}'+'%;background:var(--pl-a)"></span><span style="width:'+f'{resistant:.2f}'+'%;background:var(--pl-ab)"></span></div><span class="pl-muted">Sensitive '+f'{100-resistant:.0f}'+'% · Resistant '+f'{resistant:.0f}'+'%</span></div></div>'
            body += '<h3>All three plans at the selected time</h3><div class="pl-scroll"><table class="pl-table"><thead><tr><th>Fictional plan</th><th>Burden index</th><th>Resistant fraction</th></tr></thead><tbody>'
            for key in PLANS:
                row = rows[key][week]
                body += '<tr><td>'+esc(PLANS[key]['label'])+'</td><td>'+f'{row["burden_index"]:.1f}'+'</td><td>'+f'{row["resistant_fraction"]*100:.0f}'+'%</td></tr>'
            body += '</tbody></table></div><p class="pl-muted">This toy model calculates two cell populations only. It does not identify real molecular mechanisms or recommend an optimal treatment.</p>'
        elif view == 'prognosis':
            body += '<h2>Prognosis illustration</h2><p class="pl-muted">Fixed mathematical illustrations, independent of patient data, burden trajectories and follow-up. Not PFS/OS predictions.</p>'+chart(data['survival_illustration']['curves'], selected, survival=True)+'<table class="pl-table"><tr><th>Endpoint</th><th>Status</th></tr><tr><td>Imaging response PR/PD</td><td>Unavailable; the burden index is not a RECIST measurement</td></tr><tr><td>PFS／OS</td><td>Unavailable; no applicable individual model</td></tr><tr><td>Toxicity / quality of life</td><td>Unavailable; requires separate data and models</td></tr></table>'
        else:
            body += '<h2>Sources, assumptions and scope</h2><dl class="pl-evidence"><dt>Data</dt><dd>Built-in synthetic patient P001. Each snapshot includes only observations available by its cutoff. No real patient, imaging or external-model data are ingested.</dd><dt>Computational inputs</dt><dd>Only the current burden index, assumed initial resistance and fictional growth/suppression parameters enter the calculation. Age, diagnosis, anatomy and molecular fields are not computational inputs. This is not a multimodal clinical model.</dd><dt>Follow-up update</dt><dd>Fixed synthetic observations are 82 at week 6 and 69 at week 12, with fictional A recorded as received treatment. Updating resets burden and retains the resistance assumption. It does not retrain or validate the model. Outcomes under unreceived plans remain unobserved.</dd><dt>Survival illustration</dt><dd>Independent mathematical curves demonstrate the layout only. Tumor shrinkage is not converted into survival, PFS or OS.</dd><dt>Review</dt><dd>Execution and scientific review are separate. Scientific review has not been performed; clinical_use=false. SHA-256 checks content consistency, not authorship or clinical validity.</dd></dl>'
        body += '</div><section class="pl-run"><div class="pl-row"><h3>Saved runs and follow-up versions</h3><a download="patient-scenario.json" href="/patient-lab/export?id='+stored['run_id']+'">Export JSON</a></div><p class="pl-muted">Run '+esc(stored['run_id'])+'<br>Created at '+esc(stored['created_at'])+'<br>Model '+esc(data['model_version'])+' · Scientific review not performed</p><details><summary>Input and result fingerprints</summary><p class="pl-muted">Input <code>'+esc(data['input_sha256'])+'</code><br>Result <code>'+esc(stored['sha256'])+'</code></p></details>'
        if data['parent_id']:
            body += '<p><a href="/patient-lab?id='+data['parent_id']+'">Open the previous frozen run</a></p>'
            parent = app.patient_lab.get(data['parent_id'])['result']
            observed = patient['observations'][-1]
            past = parent['forecasts']['a']['trajectory'][6]['burden_index']
            body += '<p class="pl-muted">Synthetic observation: '+str(observed['burden_index'])+'; previous A 6-week scenario '+f'{past:.1f}'+'. This checks the demonstration data flow only, not predictive performance. B and A+B have no corresponding observations.</p>'
        if patient['cutoff_week'] < 12:
            body += '<form method="post" action="/patient-lab/followup" class="pl-form">'+token+'<input type="hidden" name="parent_id" value="'+stored['run_id']+'"><button type="submit">Load '+str(patient['cutoff_week'] + 6)+' week synthetic follow-up as a new version</button></form>'
        else:
            body += '<p class="pl-muted">Both synthetic follow-ups are loaded. The demonstration workflow is complete. Reopen an earlier version or start a new baseline assumption.</p>'
        body += '</section>'
    body += '</div></div><h3>Recent saved scenarios (up to 30)</h3><div class="pl-history">'
    for run in app.patient_lab.history():
        body += '<a href="/patient-lab?id='+run['run_id']+'">P001 · cutoff '+str(run['result']['patient']['cutoff_week'])+'w · '+run['run_id'][:8]+'</a>'
    body += '</div><p class="pl-footer">Built-in synthetic scenarios only. No real patient uploads, drug dosing or external model calls. New runs are appended; earlier forecasts remain unchanged.</p></section>'
    return body


def frame(body):
    """English-only shell for the patient project; legacy tools retain their locale."""
    from .ui_shell import STYLE as shell_style
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Patient Scenario Lab · EmerisBiotech</title><style>' + shell_style + '</style></head><body><p style="padding:12px"><a href="/molecule-lab">Patient × Molecule Lab · data-derived assay response</a> · <a href="/cohort-lab">Virtual Trial Cohort</a></p>'
            '<a class="skip-link" href="#main-content">Skip to content</a>'
            '<header class="topbar" style="margin:0"><a class="brand" href="/patient-lab" style="color:#163c49">EmerisBiotech</a>'
            '<span>Patient Scenario Lab</span><span class="context-pill">Synthetic research · English</span></header>'
            '<main id="main-content" class="main-content">' + body +
            '<footer class="page-footer">EmerisBiotech · Synthetic research showcase · Not for clinical use</footer></main></body></html>')
