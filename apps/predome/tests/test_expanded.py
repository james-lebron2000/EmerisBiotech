"""Fail-closed, read-only release serving; no fitting or external requests."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from emeris_live import expanded_api
from emeris_live.main import create_app
from emeris_live.predome_statistics import sha


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    data = root / 'predome_data/historical_v3'
    data.mkdir(parents=True)
    artifact = root / 'artifact.json'
    artifact.write_text('{"status":"development_replay"}', encoding='utf-8')
    report = root / expanded_api.REPORT
    report.parent.mkdir(parents=True)
    report.write_text('# 扩展研究报告\n8例已见重放，不是新验证。', encoding='utf-8')
    record = {
        'benchmark': {'new_independent_test': False, 'clinical_prediction_validated': False},
        'artifact_manifest': [
            {'path': str(p.relative_to(root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in [artifact, report]
        ],
    }
    def freeze():
        envelope = {'record': record, 'sha256': sha(record)}
        (data / 'release.json').write_text(json.dumps(envelope, ensure_ascii=False), encoding='utf-8')
        return envelope
    freeze()
    monkeypatch.setattr(expanded_api, 'ROOT', root)
    monkeypatch.setattr(expanded_api, 'DATA', data)
    client = TestClient(create_app(tmp_path / 'store'))
    client.headers['Origin'] = 'http://testserver'
    return client, record, artifact, report, data, freeze


def test_verified_readonly_routes_and_scientific_boundaries(bundle):
    client, record, _, report, _, _ = bundle
    response = client.get('/api/predome/expanded')
    assert response.status_code == 200
    assert response.json() == {'record': record, 'sha256': sha(record), 'integrity_verified': True}
    exported = client.get('/api/predome/expanded/export')
    assert exported.status_code == 200
    assert exported.json() == response.json()
    assert 'attachment' in exported.headers['content-disposition']
    downloaded = client.get('/api/predome/expanded/report')
    assert downloaded.status_code == 200
    assert downloaded.text == report.read_text()
    assert 'attachment' in downloaded.headers['content-disposition']
    for suffix in ['', '/export', '/report']:
        for method in ['post', 'put', 'delete']:
            assert getattr(client, method)('/api/predome/expanded' + suffix).status_code == 405
    health = client.get('/api/health').json()
    assert health['version'] == '1.2.0'
    for field in ['clinical_use', 'clinical_prediction_validated', 'formal_training_completed', 'weight_training']:
        assert health[field] is False
    assert 'expanded_historical_development_replay' in health['research_workflows']


@pytest.mark.parametrize('kind', ['artifact_tamper', 'report_tamper', 'missing_file', 'record_tamper', 'missing_release', 'bad_json', 'empty_manifest', 'bad_manifest', 'missing_sha'])
def test_invalid_release_fails_closed(bundle, kind):
    client, record, artifact, report, data, freeze = bundle
    if kind == 'artifact_tamper': artifact.write_text('tampered')
    elif kind == 'report_tamper': report.write_text('tampered')
    elif kind == 'missing_file': artifact.unlink()
    elif kind == 'record_tamper':
        envelope = json.loads((data / 'release.json').read_text())
        envelope['record']['benchmark']['new_independent_test'] = True
        (data / 'release.json').write_text(json.dumps(envelope))
    elif kind == 'missing_release': (data / 'release.json').unlink()
    elif kind == 'bad_json': (data / 'release.json').write_text('[')
    elif kind == 'empty_manifest': record['artifact_manifest'] = []; freeze()
    elif kind == 'bad_manifest': record['artifact_manifest'] = [None]; freeze()
    elif kind == 'missing_sha': del record['artifact_manifest'][0]['sha256']; freeze()
    for suffix in ['', '/export', '/report']:
        response = client.get('/api/predome/expanded' + suffix)
        assert response.status_code == 503
        assert 'integrity_verified' not in response.json()
        assert str(data) not in response.text


@pytest.mark.parametrize('kind', ['relative_escape', 'absolute_path', 'symlink_escape', 'duplicate', 'nonstring_path'])
def test_manifest_cannot_read_outside_root_or_duplicate_artifacts(bundle, kind, tmp_path):
    client, record, artifact, _, _, freeze = bundle
    outside = tmp_path / 'outside.json'
    outside.write_text('outside')
    item = record['artifact_manifest'][0]
    item['sha256'] = hashlib.sha256(outside.read_bytes()).hexdigest()
    if kind == 'relative_escape': item['path'] = '../outside.json'
    elif kind == 'absolute_path': item['path'] = str(outside)
    elif kind == 'symlink_escape':
        artifact.unlink(); artifact.symlink_to(outside)
    elif kind == 'duplicate':
        item['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        record['artifact_manifest'].append(dict(item))
    elif kind == 'nonstring_path': item['path'] = {'bad': 'path'}
    freeze()
    assert client.get('/api/predome/expanded').status_code == 503


def test_unlisted_report_is_not_served(bundle):
    client, record, _, _, _, freeze = bundle
    record['artifact_manifest'] = record['artifact_manifest'][:1]
    freeze()
    assert client.get('/api/predome/expanded').status_code == 200
    assert client.get('/api/predome/expanded/report').status_code == 503


def test_page_has_separate_expanded_section_without_replacing_prior_records(bundle):
    client, *_ = bundle
    response = client.get('/predome')
    assert response.status_code == 200
    html = response.text
    assert html.index('id="expanded"') < html.index('id="historical"')
    assert '/static/predome-expanded.js' in html
    assert '/static/predome-history.js' in html
    assert '已见历史重放' in html
    assert '新增6例按已知阴性结果定向收集' in html


def test_frontend_missing_fields_and_untrusted_content(tmp_path):
    """Exercise actual frontend without network: missing data and URL/text injection."""
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which('node'):
        pytest.skip('Node unavailable for frontend security check')
    source = Path(__file__).resolve().parents[1] / 'static/predome-expanded.js'
    harness = tmp_path / 'frontend-check.js'
    harness.write_text(r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
  constructor(tag) {this.tagName=tag;this.dataset={};this.childNodes=[];this._text='';}
  set textContent(v) {this._text=String(v);this.childNodes=[];}
  get textContent() {return this._text+this.childNodes.map(x=>x.textContent).join('');}
  set innerHTML(v) {throw Error('Unsafe innerHTML used');}
  append(...children) {this.childNodes.push(...children);}
  replaceChildren(...children) {this.childNodes=[...children];}
  addEventListener(name,fn) {this.listener=fn;}
}
async function exercise(record) {
  const elements=new Map();
  const document={getElementById(id){if(!elements.has(id)){const e=new Element('div');if(['expanded-metrics','expanded-cv','expanded-cases'].includes(id))e.tBodies=[new Element('tbody')];elements.set(id,e);}return elements.get(id);},createElement:tag=>new Element(tag)};
  const context={document,URL,Set,Map,console,fetch:async()=>({ok:true,json:async()=>({integrity_verified:true,sha256:'test',record})})};
  vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert(!elements.get('expanded-status').textContent.includes('未能展示'));
  return elements;
}
(async()=>{
  await exercise({});
  const payload='<img src=x onerror=alert(1)>';
  const elements=await exercise({benchmark:{counts:{train:18,test:8},training_ncts:['NCT1'],test_predictions:[{nct_id:'NCT2',probabilities:{constant_0_5:.5}}],metrics:{constant_0_5:{n:8,brier:.25,log_loss:.69,auroc:null}},training_cv:{folds:[]}},dataset_rows:[{nct_id:'NCT1',trial_name:payload,feature_source:{url:'javascript:alert(1)'},label_source:{url:'https://example.org/result'}}],prior_evidence_records:[{record_id:payload,source:{url:'javascript:alert(1)'},applicability:[{target_nct_id:'NCT2',differences:[payload]}]}]});
  assert(elements.get('expanded-cases').tBodies[0].textContent.includes(payload));
  assert(elements.get('expanded-priors').textContent.includes(payload));
  function visit(node){if(node.href)assert(new URL(node.href).protocol.startsWith('http'));for(const child of node.childNodes)visit(child);if(node.tBodies)node.tBodies.forEach(visit);}
  [...elements.values()].forEach(visit);
  elements.get('expanded-search').listener({target:{value:'does-not-exist'}});
  assert.equal(elements.get('expanded-cases').tBodies[0].childNodes.length,0);
  console.log('Frontend missing-field and injection checks passed');
})().catch(error=>{console.error(error);process.exit(1)});
''', encoding='utf-8')
    result = subprocess.run(['node', str(harness), str(source)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
