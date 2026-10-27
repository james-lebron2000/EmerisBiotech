"""Project mission and evidence milestones; never infers scientific approval."""
MISSION='Evaluate whether traceable, calibrated phase II/III outcome forecasts can produce an investment advantage after costs.'
MILESTONES=[
 {'id':'data','title':'Build a replayable clinical-event dataset','acceptance':'Define trial, asset, indication and labels; retain historical feature availability, failures and unresolved cases; verify commercial-use rights.','next':'Freeze the first indication, forecast cutoff, eligibility rules and data handoff manifest.'},
 {'id':'clinical','title':'Test forecasting against simple baselines','acceptance':'Evaluate phases II and III separately with rolling temporal holdouts and asset-level separation; report calibration, Brier score, log loss, coverage and uncertainty.','next':'Compare phase/indication baselines and simple models before testing incremental AI value.'},
 {'id':'investment','title':'Test whether forecasts translate to investment returns','acceptance':'Freeze the strategy before temporal validation; include first tradable prices, failures/delistings, costs, liquidity and drawdown; compare with benchmarks.','next':'Define market scope and price sources, then freeze a simulated-portfolio protocol.'},
 {'id':'prospective','title':'Complete prospective simulated evaluation','acceptance':'Seal forecasts and decisions before outcomes become public; retain abstentions and failures, apply prespecified evaluation rules and avoid retrospective selection.','next':'Register future events and forecast cutoffs, then begin simulated observation.'},
 {'id':'cloud','title':'Deliver a reliable private workbench','acceptance':'Verify remote access, persistence, record migration, backup/restore and operational monitoring in the target environment.','next':'Confirm the target host and domain; a deployment package is not a live service.'}]
STATES={'planned':'Planned','in_progress':'In progress','blocked':'Blocked','evidence_submitted':'Evidence submitted; independent review pending'}

def validate_update(app,milestone,state,author,note,reference):
    if milestone not in {m['id'] for m in MILESTONES} or state not in STATES:raise ValueError('Unknown milestone or state')
    if not 1<=len(author.strip())<=100 or not 10<=len(note.strip())<=10000:raise ValueError('Provide a recorder and a progress note of 10–10000 characters')
    if len(reference)>300:raise ValueError('Evidence reference is too long')
    if reference and reference not in {r['id'] for r in app.records()}:raise ValueError('Referenced research record not found')
    if state=='evidence_submitted' and not reference:raise ValueError('Submitted evidence must reference an existing research record; submission does not imply acceptance')
