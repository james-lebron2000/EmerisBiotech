import json
from datetime import datetime,timezone,timedelta
import pytest
from vbt.programs import Programs,compare_interval
from vbt.workbench import Workbench,page
from test_programs import PROFILE,add

@pytest.fixture
def prepared(tmp_path):
    s=Programs(tmp_path/'state');pid=s.create(PROFILE,'test researcher')
    a=add(s,pid,'support','reviewed');b=add(s,pid,'counter','reviewed')
    s.add_experiment(pid,dict(question='Engineering test only',design='synthetic unit-separated numeric fixture',success='lower confidence bound reaches one',stop='upper confidence bound below zero',owner='test fixture owner',currency='CNY',cost=1,days=1,evidence_ids=[a,b],metric='synthetic effect',unit='SD',direction='higher',success_threshold=1,stop_threshold=0),'test researcher',s.get(pid)['revision'])
    return s,pid,[a,b]

def result(s,pid,estimate=1.5,low=1.2,high=2.0,**changes):
    p=s.get(pid)
    values=dict(experiment_id=p['experiments'][-1]['event_id'],metric='synthetic effect',unit='SD',independent_unit='synthetic patient fixture',population='engineering test only, no clinical observation',method='reported 95 percent test interval fixture',limitations='fabricated engineering data; no biological interpretation',source='https://example.invalid/engineering-fixture',available_at='2020-01-01T00:00:00+00:00',n=20,estimate=estimate,ci_low=low,ci_high=high)
    values.update(changes)
    s.add_result(pid,values,'test researcher',p['revision'])
    return s.get(pid)['results'][-1]['event_id']

@pytest.mark.parametrize('direction,low,high,expected',[
    ('higher',1,2,'meets_recorded_threshold'),('higher',-2,0,'reaches_recorded_stop'),('higher',-.1,1.1,'inconclusive_between_thresholds'),
    ('lower',-1,0,'meets_recorded_threshold'),('lower',1,2,'reaches_recorded_stop'),('lower',-.1,1.1,'inconclusive_between_thresholds')])
def test_interval_comparison(direction,low,high,expected):
    c=dict(direction=direction,success_threshold=1 if direction=='higher' else 0,stop_threshold=0 if direction=='higher' else 1)
    assert compare_interval(c,dict(ci_low=low,ci_high=high))==expected

def test_frozen_plan_result_review_decision_export(prepared):
    s,pid,refs=prepared;before=s.get(pid)['experiments'][-1]
    rid=result(s,pid,assessment='meets_fake_approval',success_threshold=-999)
    p=s.get(pid);r=p['results'][-1]
    assert r['assessment']=='meets_recorded_threshold' and r['criterion']['success_threshold']==1
    assert r['timing']=='historical_source' and r['review']=='pending'
    assert r['plan_revision']==next(e['digest'] for e in p['events'] if e['id']==before['event_id'])
    decision=dict(action='advance',evidence_ids=refs,result_ids=[rid],reason='Test fixture threshold compared, pending review',remaining_risk='no clinical value established')
    with pytest.raises(ValueError,match='仍待审阅'):s.decide(pid,decision,'tester',p['revision'])
    s.review_result(pid,dict(result_id=rid,review='reviewed',reason='Checked engineering fixture and calculation only'),'test reviewer',p['revision'])
    s.decide(pid,decision,'tester',s.get(pid)['revision'])
    export=json.loads(s.export(pid));assert export['program']['decisions'][-1]['result_ids']==[rid]
    assert export['clinical_validation']=='not_inferred'
    assert s.get(pid)['events'][-3]['payload'].get('assessment')=='meets_recorded_threshold'

@pytest.mark.parametrize('change',[
    {'metric':'different metric'}, {'unit':'mg'}, {'estimate':float('nan')}, {'ci_low':3}, {'ci_high':0}, {'n':1}, {'n':'2.5'},
    {'source':'javascript:alert(1)'}, {'available_at':'2020-01-01T00:00:00'}, {'experiment_id':'unknown'},
    {'available_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}])
def test_invalid_or_incomparable_result_rejected(prepared,change):
    s,pid,_=prepared
    with pytest.raises(ValueError):result(s,pid,**change)
    assert s.get(pid)['results']==[]

def test_no_cherry_picking_stop_and_scope_change(prepared):
    s,pid,refs=prepared;a=result(s,pid);b=result(s,pid,-1,-2,-.2)
    decision=dict(action='advance',evidence_ids=refs,result_ids=[a],reason='Cannot select only favorable result',remaining_risk='conflicting evidence exists')
    with pytest.raises(ValueError,match='全部结果'):s.decide(pid,decision,'tester',s.get(pid)['revision'])
    for rid in [a,b]:s.review_result(pid,dict(result_id=rid,review='reviewed',reason='Checked all synthetic fixtures, no clinical validity'),'tester',s.get(pid)['revision'])
    decision['result_ids']=[a,b]
    with pytest.raises(ValueError,match='停止门槛'):s.decide(pid,decision,'tester',s.get(pid)['revision'])
    decision['action']='stop';s.decide(pid,decision,'tester',s.get(pid)['revision'])
    old=s.get(pid)['experiments'][-1]['event_id']
    s.revise(pid,dict(PROFILE,revision_reason='Different hypothesis following stopping evidence'),'tester',s.get(pid)['revision'])
    assert all(not r['current_scope'] for r in s.get(pid)['results'])
    with pytest.raises(ValueError,match='当前范围'):result(s,pid,experiment_id=old)
    with pytest.raises(ValueError,match='当前假说'):s.decide(pid,decision,'tester',s.get(pid)['revision'])

def test_result_cross_project_reference_rejected(prepared):
    s,pid,refs=prepared;other=s.create(PROFILE,'tester');rid=result(s,pid)
    with pytest.raises(ValueError,match='不属于当前项目'):s.review_result(other,dict(result_id=rid,review='reviewed',reason='Invalid cross-project review fixture'),'tester',s.get(other)['revision'])

def test_legacy_plan_not_retroactively_scored(prepared):
    s,pid,refs=prepared
    s.add_experiment(pid,dict(question='Legacy unstructured plan',design='unstructured plan created without numeric criterion',success='free text success',stop='free text stop',owner='test owner',currency='CNY',cost=1,days=1,evidence_ids=refs),'tester',s.get(pid)['revision'])
    result(s,pid);assert s.get(pid)['results'][-1]['assessment']=='no_frozen_numeric_criterion'

def test_result_page_keeps_assessment_separate_and_ids_unique(prepared,tmp_path):
    s,pid,_=prepared;result(s,pid)
    app=Workbench(tmp_path/'workspace',s.state);body=page(app,'/programs',{'id':pid})
    assert '所报区间达到记录的成功门槛' in body and '来源早于计划' in body and '结果记录、门槛比对和科学审阅分别保存' in body
    assert '项目已验证' not in body
    from html.parser import HTMLParser
    class IDs(HTMLParser):
        def __init__(self):super().__init__();self.ids=[]
        def handle_starttag(self,tag,attrs):self.ids.extend(v for k,v in attrs if k=='id')
    parser=IDs();parser.feed(body);assert len(parser.ids)==len(set(parser.ids))

def test_http_result_write_and_decision_references(prepared,tmp_path):
    import threading,urllib.request
    from http.server import ThreadingHTTPServer
    from urllib.parse import urlencode
    from vbt.workbench import handler
    s,pid,refs=prepared;app=Workbench(tmp_path/'workspace',s.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    def post(values):
        data=dict(values,id=pid,revision=s.get(pid)['revision'],author='engineering tester',token=app.token)
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(data,doseq=True).encode(),headers={'Origin':base}))
    try:
        payload=dict(operation='result',experiment_id=s.get(pid)['experiments'][0]['event_id'],metric='synthetic effect',unit='SD',independent_unit='synthetic patient',population='engineering fixture only',method='synthetic interval fixture',limitations='not a biological result',source='https://example.invalid/test',available_at='2020-01-01T00:00:00Z',n=20,estimate=1,ci_low=-1,ci_high=2)
        body=post(payload).read().decode();assert '所报区间尚不足以判定' in body
        rid=s.get(pid)['results'][0]['event_id']
        post(dict(operation='decision',action='hold',evidence_ids=refs,result_ids=[rid],reason='Wait for new independent evidence before proceeding',remaining_risk='No clinical benefit established'))
        export=json.load(urllib.request.urlopen(base+'/program/export?id='+pid));assert export['program']['decisions'][-1]['result_ids']==[rid]
    finally:server.shutdown();server.server_close()
