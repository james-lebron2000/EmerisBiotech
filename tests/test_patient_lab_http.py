"""Real loopback HTTP, strict CSP, CSRF and persisted synthetic scenario flow."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import pytest
from vbt.workbench import Workbench, handler, page


@pytest.fixture
def live(tmp_path):
    app = Workbench(tmp_path / 'workspace', tmp_path / 'state')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


def post(base, path, values, origin=None):
    return urllib.request.urlopen(urllib.request.Request(base + path, data=urlencode(values).encode(), headers={'Origin': origin or base}))


def test_complete_flow_and_export(live):
    app, base = live
    response = urllib.request.urlopen(base + '/patient-lab')
    body = response.read().decode()
    assert 'P001' in body and 'DEMO-P' not in body
    assert 'Synthetic patient' in body and 'No scenario has been run yet' in body
    assert 'script-src' not in response.headers['Content-Security-Policy']
    assert not app.patient_lab.history()
    response = post(base, '/patient-lab/run', {'token': app.token, 'resistance': 'low'})
    assert '/patient-lab?id=' in response.url
    run_id = response.url.split('id=')[1]
    body = response.read().decode()
    assert 'Future burden trajectories' in body and 'Export JSON' in body
    raw = urllib.request.urlopen(base + '/patient-lab/export?id=' + run_id)
    assert raw.headers.get_content_type() == 'application/json'
    original = json.load(raw)
    for query, text in [('view=prognosis', 'Not PFS/OS predictions'), ('view=evidence', 'Age, diagnosis, anatomy and molecular fields are not computational inputs'), ('plan=b&week=24', 'Plan B · 24-week burden index')]:
        assert text in urllib.request.urlopen(base + '/patient-lab?id=' + run_id + '&' + query).read().decode()
    first = post(base, '/patient-lab/followup', {'token': app.token, 'parent_id': run_id})
    assert 'Data cutoff: week 6' in first.read().decode()
    child_id = first.url.split('id=')[1]
    second = post(base, '/patient-lab/followup', {'token': app.token, 'parent_id': child_id})
    assert 'The demonstration workflow is complete' in second.read().decode()
    assert original == json.load(urllib.request.urlopen(base + '/patient-lab/export?id=' + run_id))
    assert len(app.patient_lab.history()) == 3


def test_requests_require_csrf_and_validated_inputs(live):
    app, base = live
    for values, origin, status in [({'token': 'bad'}, base, 403), ({'token': app.token}, 'https://elsewhere.test', 403), ({'token': app.token, 'patient_id': 'real'}, base, 400), ({'token': app.token, 'dose': '50'}, base, 400)]:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(base, '/patient-lab/run', values, origin)
        assert error.value.code == status
    assert not app.patient_lab.history()
    for query in ['id=../../x', 'plan=unknown', 'week=NaN', 'view=<script>', 'extra=1']:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + '/patient-lab?' + query)
        assert error.value.code == 404


def test_rendering_does_not_create_runs(tmp_path):
    app = Workbench(tmp_path / 'w', tmp_path / 's')
    body = page(app, '/patient-lab', {})
    assert 'Patient Scenario Lab' in body
    run = app.patient_lab.run({})
    before = app.patient_lab.history()
    for view in ('response', 'prognosis', 'evidence'):
        body = page(app, '/patient-lab', {'id': run['run_id'], 'view': view})
        assert '<script' not in body
    assert before == app.patient_lab.history()
