"""Human-facing research pages. Bundled cases are retrospective demonstrations."""
import html

def e(value):return html.escape(str(value if value is not None else 'Not recorded'),quote=True)
CASES={'osmr-uc': {'title': 'OSMR × Ulcerative colitis',
             'tag': 'Retrospective counterevidence case',
             'question': 'Is an expression association sufficient to support a clinical-success judgment?',
             'summary': 'Observational associations provide leads; intervention-specific clinical '
                        'counterevidence narrows the conclusion.',
             'decision': 'Stop using expression association alone as a clinical-success or investment '
                         'signal. Retain exploratory biomarker research.',
             'support': 'The existing GSE92415 analysis included 59 patients. The median difference was '
                        '0.314 for OSM (95% CI 0.011–0.644) and 0.287 for OSMR (95% CI −0.103–0.641), '
                        'nonresponders minus responders.',
             'opposition': 'The Roche phase II vixarelimab UC summary reported remission counts of 3/27 and '
                           '1/26, versus 3/26 with placebo; it reported no observed benefit and early '
                           'termination.',
             'limits': ['The observational cohort received golimumab; its results cannot establish '
                        'OSMR-blockade efficacy.',
                        'The GEO week-6 response definition still requires protocol-level verification; '
                        'causal adjustment is incomplete.',
                        'The outcome is already known and is excluded from prospective forecasting or '
                        'investment performance.',
                        'This is a summary of earlier analysis, not a rerun on this page. Original run: '
                        '2026-09-25T085153-545299+0000-515a3584. Original files are not redistributed in '
                        'this public repository.'],
             'sources': [('GSE92415 original cohort',
                          'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92415',
                          'Observational source'),
                         ('Roche GA44839 results summary',
                          'https://forpatients.roche.com/content/dam/patient-platform/lps/global/ga44839/LPS_GA44839_final-results_November2025_English.pdf',
                          '2025-11-05, pages 1 and 5')],
             'next': 'Verify cohort endpoints and confounding before independent-cohort validation.'},
 'bcma-autoimmune': {'title': 'BCMA × Autoimmunity',
                     'tag': 'Unvalidated research draft',
                     'question': 'Do pathogenic antibodies depend on BCMA-positive cells?',
                     'summary': 'Separate cell dependence, functional antibodies and material availability '
                                'into testable questions.',
                     'decision': 'Evidence is insufficient for a success probability or investment judgment.',
                     'support': 'A research question and proposed experimental comparison exist; this '
                                'demonstration supplies no independently reviewed scientific support source.',
                     'opposition': 'Counterevidence searching is incomplete. Unknown does not mean no '
                                   'counterevidence.',
                     'limits': ['Sample materials, budget and an accountable lead are not confirmed.',
                                'No candidate molecule, clinical result or calibrated probability is '
                                'provided.'],
                     'sources': [],
                     'next': 'Define the patient scope and material sources, then search supporting and '
                             'opposing evidence.'},
 'napi2b-adc': {'title': 'NaPi2b × Ovarian cancer ADC',
                'tag': 'Unvalidated research draft',
                'question': 'How do expression, internalization and payload sensitivity affect the '
                            'therapeutic window?',
                'summary': 'Evaluate target expression, drug format and safety window separately.',
                'decision': 'Evidence is insufficient; first confirm the differentiation hypothesis and '
                            'available models.',
                'support': 'A question and comparator concept exist; this demonstration supplies no '
                           'independently reviewed scientific support source.',
                'opposition': 'Normal-tissue expression, nonspecific payload toxicity and resistance require '
                              'investigation; this project has no experimental results for these questions.',
                'limits': ['This is not an owned drug asset; drug, model and rights are unconfirmed.',
                           'Counterevidence searching is incomplete; no success probability is produced.'],
                'sources': [],
                'next': 'Check tumor/normal models, internalization endpoints, payload controls and material '
                        'authorization.'}}
ACTIONS={'follow_up':'Collect further evidence','revise':'Revise the research hypothesis','stop':'Stop this inference'}

def cards():
 return ''.join('<a class="case-card" href="/case?id='+key+'"><span class="eyebrow">'+e(v['tag'])+'</span><h3>'+e(v['title'])+'</h3><p>'+e(v['question'])+'</p><span class="text-link">Open research →</span></a>' for key,v in CASES.items())

def home_intro():
 return '<section class="hero"><div class="eyebrow">EMERIS BIOTECH · RESEARCH WORKSPACE</div><h1>Give each clinical judgment<br>a reviewable evidence trail.</h1><p>Review evidence and counterevidence, and retain every decision and revision.</p><a class="button" href="/case?id=osmr-uc">Explore a research workflow</a> <a class="secondary" href="/guide">View the walkthrough</a><div class="hero-note">Research prototype · Clinical forecasting and investment returns are not validated</div></section><section class="section-head"><h2>Start with a specific question</h2><span>1 retrospective case · 2 unvalidated drafts</span></section><div class="case-grid">'+cards()+'</div>'

def case_page(app,key):
 if key not in CASES:raise ValueError('Research case not found')
 c=CASES[key];events=[r for r in app.rows('decisions') if r['case_id']==key]
 body='<a href="/">← Back to workbench</a><div class="section-head"><div><span class="eyebrow">'+e(c['tag'])+'</span><h1>'+e(c['title'])+'</h1></div><span class="badge">Demonstration material · As of 2026-09-25</span></div><p class="lead">'+e(c['question'])+'</p><div class="notice">This page uses fixed retrospective cases and drafts, not live forecasts. Saved decisions are marked as demonstration records and are excluded from formal research approval.</div><div class="card conclusion"><span class="eyebrow">Current research conclusion</span><h2>'+e(c['decision'])+'</h2><p>'+e(c['summary'])+'</p></div><div class="split"><div class="card"><span class="eyebrow">Supporting evidence and leads</span><h2>Known evidence</h2><p>'+e(c['support'])+'</p></div><div class="card counter"><span class="eyebrow">Counterevidence and unknowns</span><h2>Limitations to address</h2><p>'+e(c['opposition'])+'</p></div></div><div class="card"><h2>Sources and limitations</h2>'
 for title,url,loc in c['sources']:body+='<p><a target="_blank" rel="noopener noreferrer" href="'+e(url)+'">'+e(title)+' ↗</a><br><span class="muted">'+e(loc)+'</span></p>'
 if not c['sources']:body+='<p class="muted">No independently reviewable source is available; further evidence is required.</p>'
 body+='<ul>'+''.join('<li>'+e(x)+'</li>' for x in c['limits'])+'</ul><p><b>Next step: </b>'+e(c['next'])+'</p></div><form class="card" method="post" action="/decision"><h2>Record your decision</h2><p class="muted">Demonstration records are append-only; earlier decisions remain unchanged.</p><input type="hidden" name="token" value="'+app.token+'"><input type="hidden" name="case_id" value="'+e(key)+'"><label for="decision-author">Recorder</label><input id="decision-author" name="author" required maxlength="100" placeholder="Enter a name or demonstration alias"><label for="decision-action">Research action</label><select id="decision-action" name="action">'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in ACTIONS.items())+'</select><label for="decision-reason">Decision rationale</label><textarea id="decision-reason" name="reason" required minlength="10" maxlength="5000" placeholder="Which evidence changed the decision? What still needs testing?"></textarea><button>Save demonstration decision</button></form><h2>Decision timeline</h2>'
 for x in events:body+='<div class="card"><span class="badge">Demonstration record</span> <b>'+e(ACTIONS[x['action']])+'</b><p class="body">'+e(x['reason'])+'</p><p class="ref">'+e(x['author'])+' · '+e(x['created'])+'</p></div>'
 if not events:body+='<div class="empty">No decisions recorded yet. Review the evidence before saving a first decision.</div>'
 return body

def guide():
 return '<span class="eyebrow">PRODUCT WALKTHROUGH</span><h1>A six-minute research walkthrough.</h1><p class="lead">For first-time use and product demonstrations. No model key required.</p><div class="card"><h2>01 · Define the question</h2><p>Start with the OSMR retrospective case and state that its outcome is already known.</p><a href="/case?id=osmr-uc">Open the case →</a></div><div class="card"><h2>02 · Compare evidence and counterevidence</h2><p>Inspect observational associations, clinical counterevidence, source locations and unresolved limitations together.</p></div><div class="card"><h2>03 · Record a decision</h2><p>Enter a demonstration alias and a rationale of at least ten characters, choose follow-up, revision or stopping, then inspect the saved timeline.</p></div><div class="card"><h2>04 · Inspect history and tasks</h2><p>Open a historical snapshot to inspect details and start integrity verification. A fresh installation explicitly shows an empty state.</p><a href="/records">History →</a> · <a href="/jobs">Monitor tasks →</a></div><div class="card"><h2>05 · Explain the next stage</h2><p>Review acceptance criteria for clinical forecasting, investment validation and cloud delivery. Engineering tests and demonstrations are not forecasting performance.</p><a href="/mission">Goals and milestones →</a></div>'

def record_gates(record,read):
 data=record.get('data') if isinstance(record.get('data'),dict) else {}
 integrity=data.get('record_integrity')
 if not isinstance(integrity,str):integrity=None
 integrity_labels={'complete':'Saved state: complete record','incomplete':'Saved state: incomplete record','pending':'Saved state: review required'}
 review={}
 if record['kind']=='run' and (record['path']/'review.json').is_file():
  saved=read(record['path']/'review.json')
  if isinstance(saved,dict):review=saved
 signature=review.get('human_signature')
 if 'human_signature' not in review:human='Sign-off state unknown'
 elif signature is None or signature=='':human='No human sign-off recorded'
 elif isinstance(signature,str) and signature.strip() and review.get('reviewer_type')=='human':human='Sign-off field recorded · Not verified'
 else:human='Sign-off field requires review'
 executions={'succeeded':'Execution succeeded','failed':'Execution failed','blocked':'Execution blocked','interrupted':'Task interrupted','running':'Running','pending':'Execution pending','completed':'Cycle completed','snapshot':'Frozen','recorded':'Recorded'}
 reviews={'pass':'Saved state: passed','passed':'Saved state: passed','revise':'Saved state: revision required','insufficient_evidence':'Saved state: insufficient evidence','pending':'Saved state: review pending'}
 status=record.get('status') if isinstance(record.get('status'),str) else None
 scientific=record.get('review') if isinstance(record.get('review'),str) else None
 values=[('Execution / record',executions.get(status,'Execution state unknown')),
         ('Record integrity',integrity_labels.get(integrity,'Integrity state unknown')),
         ('Scientific review',reviews.get(scientific,'Scientific-review state unknown')),
         ('Human sign-off',human)]
 return '<section class="record-gates" aria-label="Four independent research-record states">'+''.join('<div><span>'+e(label)+'</span><strong>'+e(value)+'</strong></div>' for label,value in values)+'</section><p class="data-note">These are saved fields for this version, not new verification. The four states are separate; a sign-off field does not establish a valid signature or research approval.</p>'


def record_summary(record,read):
 data=record['data'];path=record['path'];kind=record['kind'];body='<div class="card"><h2>Research summary</h2>'
 if kind=='run':
  errors=data.get('errors',[])
  if not isinstance(errors,list):
   body+='<p class="notice">Error record format requires review; this does not establish absence of execution issues.</p>';errors=[]
  body+='<p>'+('This analysis has issues that require attention.' if errors else 'Review the results and scientific review below; completion does not mean the conclusions passed.')+'</p>'
  for error in errors:body+='<p class="notice">'+e(error)+'</p>'
  results=read(path/'work/results.json')
  if isinstance(results,list) and results:
   unreadable=sum(not isinstance(row,dict) for row in results)
   if unreadable:body+='<p class="notice">Results contain '+str(unreadable)+' rows whose format requires review; only readable rows are transcribed below, not a complete result.</p>'
   body+='<div class="scroll"><table><tr><th>Gene</th><th>Cohort</th><th>Nonresponders / responders</th><th>Median difference</th><th>95% CI</th></tr>'
   for r in results:
    if not isinstance(r,dict):continue
    def fmt(k):
     value=r.get(k);return f'{value:.3f}' if isinstance(value,(int,float)) else 'Not estimated'
    body+='<tr><td>'+e(r.get('gene'))+'</td><td>'+e(r.get('cohort'))+'</td><td>'+e(r.get('n_nonresponder'))+' / '+e(r.get('n_responder'))+'</td><td>'+fmt('median_difference')+'</td><td>'+fmt('ci_low')+' – '+fmt('ci_high')+'</td></tr>'
   body+='</table></div>'
  else:body+='<p class="data-note">Statistical results are empty, unavailable or in an unverified format; a negative result cannot be inferred.</p>'
 elif kind=='cycle':
  d=data.get('decision',{})
  if not isinstance(d,dict):body+='<p class="notice">Decision record format requires review.</p>';d={}
  body+='<h3>'+e(d.get('scope','View the complete decision record'))+'</h3><p>'+e(d.get('retained_work',''))+'</p><p class="muted">Retrospective research; excluded from prospective forecast performance.</p>'
 else:
  plan=read(path/'plan.json')
  if not isinstance(plan,dict):body+='<p class="notice">Research plan format requires review.</p>';plan={}
  hypotheses=plan.get('hypotheses',[])
  if not isinstance(hypotheses,list):body+='<p class="notice">Research hypothesis list format requires review.</p>';hypotheses=[]
  for h in hypotheses:
   if isinstance(h,dict):body+='<h3>'+e(h.get('title'))+'</h3><p>'+e(h.get('question'))+'</p>'
   else:body+='<p class="notice">Research hypothesis record format requires review.</p>'
 return body+'</div>'
