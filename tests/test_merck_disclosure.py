"""Synthetic layout/drift tests; actual publisher observations are not fixtures."""
import re
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlencode
import urllib.request
import urllib.error

import pytest

from vbt import disclosures
from vbt.market import Market
from vbt.merck_disclosure import parse_merck
from vbt.workbench import Workbench, page, handler


def chart(*, phase='phase-2', conflict=False, annotation=False):
    title = 'SYNTHETIC disease'
    content = '<p>(<a href="https://clinicaltrials.gov/study/NCT00000001">NCT00000001</a>)</p>'
    def indication(tag, heading, display):
        core = '<' + heading + ' class="pipeline-program-indication-title">' + title + '</' + heading + '><div class="pipeline-program-indication-content">' + content + '</div>'
        if tag == 'tr':
            core = '<td>' + core + '</td><td class="phase-bars-table-data">' + display + '</td>'
        else:
            core += '<div class="pipeline-phase-bars">' + display + '</div>'
        return '<' + tag + ' class="pipeline-program-indication" data-phase-slug="' + phase + '">' + core + '</' + tag + '>'
    mobile_status = 'Under Review (EU)' if phase == 'under-review' else ''
    desktop_status = 'Under review(EU)' if phase == 'under-review' else '(US)' if annotation else ''
    program = ('<tr class="pipeline-program" id="pipeline-program-1" data-name="FILTER-KEY" data-code="(TEST-001)" data-phase="' + phase + '">'
               '<td class="pipeline-mobile-table-data"><h4 class="pipeline-program-name">SYNTHETIC DRUG™ <span class="pipeline-program-code">(TEST-001)</span></h4>'
               + indication('div', 'h5', mobile_status) + '</td><td><h4 class="pipeline-program-name">SYNTHETIC DRUG™</h4></td>'
               '<td class="pipeline-program-t-area">SYNTHETIC therapeutic area</td><td class="pipeline-program-indications"><table>'
               + indication('tr', 'h6', desktop_status) + '</table></td><td class="pipeline-program-modality">SYNTHETIC modality</td></tr>')
    return ('<p>Updated August 5, 2026.</p><section class="pipeline-programs"><table><thead><tr>'
            '<th>Molecule Name</th><th>Therapeutic Area</th><th>Potential Indications</th><th>Modality</th>'
            '</tr></thead><tbody>' + program + '</tbody></table></section>'
            + ('<p>The information was current as of April 5, 2026.</p>' if conflict else '')).encode()


def test_replays_visible_name_indication_phase_and_exact_source_references():
    rows, metadata = parse_merck(chart())
    assert len(rows) == 1 and metadata['program_count'] == 1
    assert rows[0]['drug_name'] == 'SYNTHETIC DRUG™'
    assert rows[0]['source_filter_name'] == 'FILTER-KEY'  # A UI key is not a drug synonym.
    assert rows[0]['drug_code'] == '(TEST-001)' and rows[0]['target'] is None
    assert rows[0]['phase'] == 'Phase 2' and rows[0]['source_nct_ids'] == ['NCT00000001']
    assert rows[0]['source_nct_status'].startswith('disclosure_links_only')
    assert metadata['as_of'] == '2026-08-05' and metadata['published_at'] is None
    assert metadata['full_pipeline_verified'] is False


def test_under_review_keeps_jurisdiction_and_is_not_phase_three():
    rows, _ = parse_merck(chart(phase='under-review'))
    assert rows[0]['phase'] == 'Under review(EU)'
    assert rows[0]['source_phase_slug'] == 'under-review'
    assert rows[0]['source_phase_display_mobile'] == 'Under Review (EU)'


def test_date_conflicts_are_retained_and_cannot_become_retrieval_date():
    _, metadata = parse_merck(chart(conflict=True))
    assert metadata['as_of'] is None and metadata['as_of_precision'] == 'conflicting_source_dates'
    assert metadata['source_date_values'] == ['2026-04-05', '2026-08-05']
    assert len(metadata['source_date_assertions']) == 2


def test_optional_layout_annotation_discrepancy_remains_explicit():
    rows, metadata = parse_merck(chart(phase='phase-3', annotation=True))
    assert rows[0]['phase'] == 'Phase 3 (US)'
    assert rows[0]['source_phase_display_mobile'] == ''
    assert rows[0]['source_phase_display_desktop'] == '(US)'
    assert metadata['representation_annotation_difference_count'] == 1


@pytest.mark.parametrize('attribute', [b'hidden', b'aria-hidden="true"', b'style="display:none"'])
def test_hidden_indication_or_fields_cannot_supply_active_rows(attribute):
    raw = chart().replace(b'<tr class="pipeline-program-indication"', b'<tr ' + attribute + b' class="pipeline-program-indication"')
    with pytest.raises(disclosures.ParseBlocked):parse_merck(raw)
    raw = chart().replace(b'<h5 class="pipeline-program-indication-title"', b'<h5 ' + attribute + b' class="pipeline-program-indication-title"')
    with pytest.raises(disclosures.ParseBlocked):parse_merck(raw)


def test_hidden_or_script_date_does_not_supply_content_effective_date():
    raw = chart().replace(b'<p>Updated', b'<p hidden>Updated')
    _, metadata = parse_merck(raw + b'<script>"Updated August 5, 2026"</script>')
    assert metadata['as_of'] is None and metadata['as_of_precision'] == 'unknown'


def test_hidden_descendant_is_not_part_of_visible_drug_name():
    raw = chart().replace('SYNTHETIC DRUG™'.encode(), 'SYNTHETIC DRUG™ <span hidden>OLD HIDDEN BRAND</span>'.encode())
    rows, _ = parse_merck(raw)
    assert rows[0]['drug_name'] == 'SYNTHETIC DRUG™'


@pytest.mark.parametrize('change', [
    lambda raw: raw.replace(b'pipeline-programs', b'unknown-section'),
    lambda raw: raw.replace(b'<section class="pipeline-programs">', b'<section class="pipeline-programs" hidden>'),
    lambda raw: raw.replace(b'data-phase-slug="phase-2"', b'data-phase-slug="phase-3"', 1),
    lambda raw: raw.replace(b'data-phase="phase-2"', b'data-phase="phase-3"'),
    lambda raw: raw.replace(b'data-name="FILTER-KEY"', b'data-name="FILTER-KEY" data-name="other"'),
    lambda raw: raw.replace(b'id="pipeline-program-1"', b'id="unbound"'),
    lambda raw: raw.replace(b'NCT00000001</a>', b'NCT00000002</a>', 1),
    lambda raw: raw.replace(b'https://clinicaltrials.gov/', b'https://malicious.test/', 1),
    lambda raw: raw.replace(b'pipeline-program-indication-title', b'changed-title', 1),
    lambda raw: raw.replace(b'SYNTHETIC DRUG', b'OTHER DRUG', 1),
    lambda raw: raw.replace(b'<tbody>', b'<tbody><tr class="pipeline-program" id="pipeline-program-1"></tr>'),
    lambda raw: raw.split(b'</section>')[0],
])
def test_schema_drift_conflict_or_truncation_cannot_publish_partial_rows(change):
    with pytest.raises(disclosures.ParseBlocked):
        parse_merck(change(chart()))


@pytest.mark.parametrize('host,path', [
    ('clinicaltrials.gov', '/ct2/show/'), ('classic.clinicaltrials.gov', '/ct2/show/'),
    ('www.clinicaltrials.gov', '/study/'),
])
def test_official_legacy_links_remain_source_claims_not_network_requests(host, path):
    raw = chart().replace(b'https://clinicaltrials.gov/study/', ('https://' + host + path).encode())
    rows, _ = parse_merck(raw)
    assert rows[0]['source_nct_ids'] == ['NCT00000001']
    assert rows[0]['source_nct_status'].endswith('not_independent_registry_verification')


def test_ingest_and_read_gate_replay_new_source_and_reject_tampering(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart(conflict=True))
    assert result['status'] == 'succeeded' and result['count'] == 1
    assert disclosures.capture_integrity(market, result)['integrity_status'] == 'verified'
    assert disclosures.observations(market, company='US:MRK')[0]['phase'] == 'Phase 2'
    with market.db() as db:
        db.execute("UPDATE disclosure_observations SET data='{}' WHERE capture_id=?", (result['id'],))
    assert disclosures.observations(market, company='US:MRK') == []
    assert disclosures.capture_integrity(market, result)['integrity_status'] == 'blocked'
    assert disclosures.captures(market)[0]['status'] == 'succeeded'


def test_failed_new_layout_preserves_previous_verified_observations(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    old = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart())
    failure = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: b'<html>access challenge</html>')
    assert failure['status'] == 'parse_blocked' and failure['count'] == 0
    rows = disclosures.observations(market, company='US:MRK')
    assert len(rows) == 1 and rows[0]['capture_id'] == old['id']


def test_customer_view_displays_conflict_without_using_capture_finish_as_content_date(tmp_path):
    app = Workbench(tmp_path / 'workspace', tmp_path / 'state')
    market = Market(app.workspace, app.state)
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart(conflict=True))
    body = page(app, '/disclosures', {'company': 'US:MRK'})
    assert '来源自身的截止日期存在冲突' in body
    values = re.findall(r'<dt>来源内容截止时间</dt><dd>(.*?)</dd>', body, re.S)
    assert values and all('冲突' in value and 'datetime=' not in value for value in values)
    assert result['as_of'] is None


def test_archived_evidence_works_readonly_and_does_not_expose_lease_tokens(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart(conflict=True))
    statements = []
    def connect():
        db = sqlite3.connect(market.path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row;db.set_trace_callback(statements.append)
        return db
    readonly = SimpleNamespace(root=market.root, db=connect)
    raw, media = disclosures.evidence_file(readonly, result['id'], 'source')
    assert raw == chart(conflict=True) and media == 'text/plain; charset=utf-8'
    raw, media = disclosures.evidence_file(readonly, result['id'], 'record')
    record = json.loads(raw)
    assert media.startswith('application/json') and record['capture']['id'] == result['id']
    assert len(record['observations']) == 1 and record['metadata']['as_of'] is None
    assert 'lease_owner' not in record['capture'] and record['human_sign_off'] is False
    assert not any(sql.lstrip().upper().startswith(('CREATE','INSERT','UPDATE','DELETE','ALTER','REPLACE')) for sql in statements)


def test_evidence_reader_verifies_the_same_observation_snapshot_it_returns(tmp_path, monkeypatch):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart())
    with market.db() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("UPDATE disclosure_observations SET phase='tampered' WHERE capture_id=?", (result['id'],))
    original = disclosures.capture_integrity
    def repair_after_snapshot(market, capture, **kwargs):
        with market.db() as writer:
            writer.execute("UPDATE disclosure_observations SET phase='Phase 2' WHERE capture_id=?", (result['id'],))
        return original(market, capture, **kwargs)
    monkeypatch.setattr(disclosures, 'capture_integrity', repair_after_snapshot)
    with pytest.raises(ValueError, match='read gate'):
        disclosures.evidence_file(market, result['id'], 'record')


@pytest.mark.parametrize('damage', ['raw', 'rows', 'path', 'hardlink', 'company'])
def test_evidence_reader_rejects_changed_or_aliased_evidence(tmp_path, damage):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart())
    path = market.root / result['archive_path']
    if damage == 'raw':path.write_bytes(path.read_bytes() + b' ')
    elif damage == 'rows':
        with market.db() as db:db.execute("UPDATE disclosure_observations SET phase='Phase 3' WHERE capture_id=?", (result['id'],))
    elif damage == 'path':
        with market.db() as db:db.execute("UPDATE disclosure_captures SET archive_path='../../outside' WHERE id=?", (result['id'],))
    elif damage == 'company':
        with market.db() as db:db.execute("UPDATE disclosure_captures SET company='US:WRONG' WHERE id=?", (result['id'],))
    else:
        import os
        os.link(path, tmp_path / 'aliased-raw')
    with pytest.raises(ValueError):disclosures.evidence_file(market, result['id'], 'source')


def test_evidence_http_uses_same_host_gate_and_sandbox_for_plain_source(tmp_path):
    app = Workbench(tmp_path / 'workspace', tmp_path / 'state')
    market = Market(app.workspace, app.state)
    result = disclosures.ingest(market, 'merck_pipeline', fetch=lambda _: chart())
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    target = base + '/market/disclosure-evidence?' + urlencode({'id':result['id'],'file':'source'})
    try:
        with opener.open(target) as response:
            assert response.status == 200 and response.read() == chart()
            assert response.headers['Content-Type'].startswith('text/plain')
            assert response.headers['Content-Security-Policy'].startswith('sandbox; ')
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        with pytest.raises(urllib.error.HTTPError) as error:
            opener.open(urllib.request.Request(target, headers={'Host':'malicious.test'}))
        assert error.value.code == 403
        for reference in ({'id':result['id'],'file':'../../secret'}, {'id':'../','file':'source'}):
            with pytest.raises(urllib.error.HTTPError) as error:
                opener.open(base + '/market/disclosure-evidence?' + urlencode(reference))
            assert error.value.code == 404
    finally:
        server.shutdown();server.server_close()
