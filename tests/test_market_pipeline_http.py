"""Local synthetic controls enqueue real durable jobs, never fabricate acceptance."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from vbt.workbench import Workbench, handler
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import resolve


@pytest.fixture
def served(tmp_path):
    app=Workbench(tmp_path/'w',tmp_path/'s');market=Market(app.workspace,app.state)
    refresh_candidates(market,[dict(market='US',ticker='SYN',name='Syntheticnova Therapeutics Inc.',
        classification='candidate',reason='Synthetic fixture',source='synthetic',evidence=['synthetic'])])
    payload={'totalCount':1,'studies':[{'protocolSection':{
        'identificationModule':{'nctId':'NCT00000001'},
        'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Syntheticnova Therapeutics Inc.'}}}}]}
    resolve(market,'US:SYN',fetch=lambda _:json.dumps(payload).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:yield app,market,base
    finally:server.shutdown();server.server_close()


def post(app,base,path,fields,*,origin=True,token=None):
    body=urlencode({'token':app.token if token is None else token,**fields}).encode()
    headers={'Origin':base} if origin else {}
    return urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers=headers))


def test_observation_and_queueing_do_not_accept_a_sponsor(served):
    app,market,base=served
    body=urllib.request.urlopen(base+'/mapping').read().decode()
    assert '严格来源名称核对' in body and '尚无自动来源核对运行记录' in body
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM collection_jobs').fetchone()[0]==0
    assert post(app,base,'/market/identity-batch',{}).status==200
    assert post(app,base,'/market/identity-batch',{}).status==200
    with market.db() as db:
        assert db.execute("SELECT count(*) FROM collection_jobs WHERE kind='identity'").fetchone()[0]==1
        assert db.execute("SELECT status FROM sponsor_proposals").fetchone()[0]=='pending'
        assert db.execute('SELECT count(*) FROM companies').fetchone()[0]==0


def test_traverse_all_queues_only_ready_scope_and_survives_repeated_submission(served):
    app,market,base=served
    refresh_candidates(market,[dict(market='US',ticker='NEXT',name='Syntheticnext Therapeutics Inc.',
        classification='candidate',reason='Synthetic fixture',source='synthetic',evidence=['synthetic'])])
    for _ in range(2):assert post(app,base,'/market/resolve-all',{'market':'US'}).status==200
    with market.db() as db:
        assert [tuple(r) for r in db.execute("SELECT kind,reference FROM collection_jobs")]==[('resolve','US:NEXT')]
        assert db.execute('SELECT count(*) FROM market_traversal_batches').fetchone()[0]==2


def test_identity_schedule_is_explicit_and_does_not_start_a_job(served):
    app,market,base=served
    assert market.setting('identity.enabled') is None
    assert post(app,base,'/market/identity-schedule',{'enabled':'1'}).status==200
    assert market.setting('identity.enabled')=='1'
    assert post(app,base,'/market/identity-schedule',{'enabled':'0'}).status==200
    assert market.setting('identity.enabled')=='0'
    with market.db() as db:assert db.execute('SELECT count(*) FROM collection_jobs').fetchone()[0]==0


@pytest.mark.parametrize('path,fields,origin,token,expected',[
    ('/market/identity-batch',{},False,None,403),
    ('/market/identity-batch',{},True,'wrong',403),
    ('/market/identity-schedule',{'enabled':'unknown'},True,None,400),
    ('/market/resolve-all',{'market':'unknown'},True,None,400),
])
def test_controls_keep_origin_token_and_bounded_fields(served,path,fields,origin,token,expected):
    app,market,base=served
    with pytest.raises(urllib.error.HTTPError) as error:post(app,base,path,fields,origin=origin,token=token)
    assert error.value.code==expected


def test_unknown_evidence_is_not_a_successful_download(served):
    _,_,base=served
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(base+'/market/identity-evidence?id='+('0'*32)+'&file=proof')
    assert error.value.code==404
