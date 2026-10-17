"""Synthetic source-layout and failure tests; not real pipeline validation."""
import hashlib
import json
from pathlib import Path

import pytest

from vbt.market import Market
from vbt import disclosures as mod


def chart(name='SYNTHETIC-1', target='TEST-TARGET ADC'):
    records = []
    for array, grid in [('dataIII', 'grid-iii'), ('dataNDA', 'grid-nda'), ('dataII', 'grid-ii'), ('dataI', 'grid-i')]:
        record = "{ name: '" + name + "', target: '" + target + "', indications: [\"SYNTHETIC disease\",\"SYNTHETIC other disease\"], type: 'tumor' }"
        records.append(f"const {array} = [[{record},]]; renderGrid('{grid}', {array});")
    return ('<h4>Clinical Development Pipeline</h4><p>*By Jul,2026</p><script>' + ''.join(records) + '</script>').encode()


@pytest.fixture
def market(tmp_path):
    return Market(tmp_path / 'workspace', tmp_path / 'state')


def test_structured_chart_exact_fields_and_dates():
    rows, meta = mod.parse_hengrui(chart())
    assert len(rows) == 8
    assert {r['phase'] for r in rows} == {'Phase III', 'Phase II', 'Phase I', 'NDA/BLA'}
    assert rows[0]['target'] == 'TEST-TARGET' and rows[0]['modality'] == 'ADC'
    assert rows[0]['drug_code'] is None
    assert rows[0]['source_locator'] == 'script const dataIII[0][0].indications[0]'
    assert meta['published_at'] is None and meta['as_of'] == '2026-07'
    assert meta['as_of_precision'] == 'month'


def test_missing_and_unknown_fields_preserved():
    rows, _ = mod.parse_hengrui(chart(name='HRS-0000', target='—'))
    assert rows[0]['drug_code'] == 'HRS-0000'
    assert rows[0]['target'] is None and rows[0]['modality'] is None
    assert rows[0]['missing_fields'] == ['target', 'modality']


@pytest.mark.parametrize('replacement', [
    b"name: window.secret", b"name: () => 'evil'", b"name: fetch('http://127.0.0.1/')",
    b"name: new Function('evil')()", b"name: 'ok', extra: 'schema drift'",
])
def test_data_literal_does_not_execute_code_or_guess_schema(replacement):
    bad = chart().replace(b"name: 'SYNTHETIC-1'", replacement)
    with pytest.raises(mod.ParseBlocked):
        mod.parse_hengrui(bad)


@pytest.mark.parametrize('mutation', [
    lambda x: x.replace(b"renderGrid('grid-i', dataI)", b"renderGrid('different', dataI)"),
    lambda x: x.replace(b'const dataIII', b'const changed'),
    lambda x: x.replace(b'</script>', b'const dataIII = [];</script>'),
    lambda x: x.replace(b'indications: ["SYNTHETIC disease","SYNTHETIC other disease"]', b'indications: []'),
])
def test_layout_drift_blocks_incomplete_parse(mutation):
    with pytest.raises(mod.ParseBlocked):
        mod.parse_hengrui(mutation(chart()))


def test_ingest_archive_hash_and_latest_success_snapshot(market):
    one = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart())
    assert one['status'] == 'succeeded' and one['count'] == 8
    assert one['sha256'] == hashlib.sha256(chart()).hexdigest()
    assert (market.root / one['archive_path']).read_bytes() == chart()
    provenance = json.loads((market.root / one['archive_path']).with_name('provenance.json').read_text())
    assert provenance['published_at'] is None and provenance['retrieved_at']
    assert len(mod.observations(market, 'HK:01276')) == 8
    assert not mod.observations(market, 'US:PFE')
    two = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart(name='SYNTHETIC-2'))
    assert len(mod.observations(market)) == 8 and len(mod.observations(market, history=True)) == 16
    assert {r['drug_name'] for r in mod.observations(market)} == {'SYNTHETIC-2'}
    assert all(r['capture_id'] == two['id'] for r in mod.observations(market))
    assert len(mod.captures(market, 'HK:01276')) == 2


def test_failed_parse_retains_previous_source_with_gap(market):
    good = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart())
    bad = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: b'<html>ACCESS CHECK</html>')
    assert bad['status'] == 'parse_blocked' and bad['count'] == 0 and bad['sha256']
    rows = mod.observations(market)
    assert len(rows) == 8 and rows[0]['capture_id'] == good['id']
    assert rows[0]['prior_capture_retained'] and rows[0]['latest_attempt_status'] == 'parse_blocked'
    assert next(x for x in mod.sources(market) if x['id'] == 'hengrui_pipeline')['latest']['id'] == bad['id']


def test_pdf_archived_without_inventing_observations(market):
    capture = mod.ingest(market, 'innovent_interim_pipeline_2025', fetch=lambda url: b'%PDF-1.7\nsynthetic')
    assert capture['status'] == 'parse_blocked' and capture['count'] == 0
    assert capture['published_at'] is None and capture['archive_path']
    assert not mod.observations(market, 'HK:01801')


def test_fetch_error_is_visible_and_releases_lease(market):
    def failure(url):
        raise OSError('HTTP 403')
    result = mod.ingest(market, 'pfizer_pipeline', fetch=failure)
    assert result['status'] == 'fetch_failed' and result['sha256'] is None and result['count'] == 0
    assert '403' in result['error']
    assert market.claim('disclosure:pfizer_pipeline')


@pytest.mark.parametrize('url', [
    'http://www.hengrui.com/en/pipeline.html', 'https://127.0.0.1/',
    'https://www.hengrui.com.evil.example/en/pipeline.html',
    'https://user:pass@www.hengrui.com/en/pipeline.html',
    'https://www.hengrui.com:444/en/pipeline.html',
    'https://www.hengrui.com/en/pipeline.html#fragment',
    'https://www.hengrui.com/arbitrary/private/path',
    'https://clinicaltrials.gov/api/v2/studies',
])
def test_url_allowlist_rejects_unsafe_or_unconfigured_sources(url):
    with pytest.raises(ValueError):
        mod.validate_url(url)


def test_injected_redirect_or_http_failure_cannot_publish(market):
    for extra in [{'url': 'https://127.0.0.1/'}, {'status': 302}, {'url': mod.SOURCES['pfizer_pipeline']['url']}]:
        result = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url, extra=extra: {'raw': chart(), **extra})
        assert result['status'] == 'fetch_failed' and result['count'] == 0
    assert not mod.observations(market)


def test_fetch_rejects_arbitrary_url_before_network(monkeypatch):
    monkeypatch.setattr(mod.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Network must not run'))
    with pytest.raises(ValueError, match='URL rejected'):
        mod.fetch('https://127.0.0.1/')


def test_fetch_enforces_tls_and_refuses_redirect(monkeypatch):
    def fake_run(args, **kwargs):
        assert '--location' not in args and '--insecure' not in args
        assert args[args.index('--proto') + 1] == '=https'
        assert args[-1] == mod.SOURCES['hengrui_pipeline']['url']
        Path(args[args.index('--output') + 1]).write_bytes(b'')
        Path(args[args.index('--dump-header') + 1]).write_text('Location: https://127.0.0.1/\n')
        return type('Result', (), {'returncode': 0, 'stdout': b'302', 'stderr': b''})()
    monkeypatch.setattr(mod.subprocess, 'run', fake_run)
    with pytest.raises(OSError, match='302'):
        mod.fetch(mod.SOURCES['hengrui_pipeline']['url'])


def test_cancel_before_fetch_and_after_capture(market):
    ids = []
    first = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: pytest.fail('Cancelled fetch'), cancelled=lambda: True, on_start=ids.append)
    assert first['status'] == 'cancelled' and ids == [first['id']]
    flag = [False]
    def fetch(url):
        flag[0] = True
        return chart()
    second = mod.ingest(market, 'hengrui_pipeline', fetch=fetch, cancelled=lambda: flag[0])
    assert second['status'] == 'cancelled' and second['sha256']
    assert not mod.observations(market)


def test_cancel_at_publication_keeps_prior_snapshot(market):
    mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart(name='SYNTHETIC-OLD'))
    count = [0]
    def cancelled():
        count[0] += 1
        return count[0] >= 3
    result = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart(name='SYNTHETIC-NEW'), cancelled=cancelled)
    assert result['status'] == 'cancelled' and result['count'] == 0
    assert {r['drug_name'] for r in mod.observations(market)} == {'SYNTHETIC-OLD'}


def test_lost_lease_cannot_publish_or_release_new_owner(market):
    def capture(url):
        with market.db() as db:
            db.execute("UPDATE leases SET owner='new-owner' WHERE key='disclosure:hengrui_pipeline'")
        return chart()
    result = mod.ingest(market, 'hengrui_pipeline', fetch=capture)
    assert result['status'] == 'superseded' and result['count'] == 0
    assert not mod.observations(market)
    with market.db() as db:
        assert db.execute("SELECT owner FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone()[0] == 'new-owner'


def test_overlap_pagination_and_no_trial_count_inflation(market):
    assert market.claim('disclosure:hengrui_pipeline')
    assert mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: pytest.fail('Overlapping fetch')) is None
    market.release('disclosure:hengrui_pipeline')
    mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart())
    assert len(mod.observations(market, limit=2, offset=2)) == 2
    assert market.count('trials') == 0 and market.count('companies') == 0
    with pytest.raises(ValueError):
        mod.observations(market, limit=0)
    with pytest.raises(ValueError):
        mod.captures(market, offset=-1)


def test_stale_running_capture_recovered_only_after_lease_expiry(market):
    def inspect_while_running(capture_id):
        assert mod.captures(market)[0]['status'] == 'running'
    result = mod.ingest(market, 'hengrui_pipeline', fetch=lambda url: chart(), on_start=inspect_while_running)
    with market.db() as db:
        db.execute("UPDATE disclosure_captures SET status='running' WHERE id=?", (result['id'],))
    assert mod.captures(market)[0]['status'] == 'interrupted'
    assert 'expired' in mod.captures(market)[0]['error']


@pytest.mark.parametrize('decoy', [
    b'<!-- <script>const dataIII = []; renderGrid("grid-iii", dataIII);</script> -->',
    b'<p>const dataIII = []; renderGrid("grid-iii", dataIII);</p>',
    b'<script>/* const dataIII = []; renderGrid("grid-iii", dataIII); */</script>',
    b'<script>// const dataIII = []; renderGrid("grid-iii", dataIII);\n</script>',
    b'<script>const decoy="const dataIII = []; renderGrid(\'grid-iii\', dataIII);";</script>',
    b'<script>const decoy=`const dataIII=[]; ${`nested const dataIII=[];`}`;</script>',
    b'<script>function unused(){const dataIII=[];renderGrid("grid-iii",dataIII);}</script>',
    b'<script type="application/ld+json">{"text":"const dataIII = [];"}</script>',
    b'<template><script>const dataIII = []; renderGrid("grid-iii",dataIII);</script></template>',
])
def test_parser_ignores_nonexecuting_declaration_decoys(decoy):
    rows, _ = mod.parse_hengrui(decoy + chart())
    assert len(rows) == 8
    assert {row['drug_name'] for row in rows} == {'SYNTHETIC-1'}


@pytest.mark.parametrize('wrapper', [
    lambda x: b'<!--' + x + b'-->',
    lambda x: x.replace(b'<script>', b'<script type="application/json">'),
    lambda x: x.replace(b'<script>', b'<template><script>').replace(b'</script>', b'</script></template>'),
    lambda x: x.replace(b'<script>', b'<p>').replace(b'</script>', b'</p>'),
])
def test_chart_text_without_real_script_cannot_be_a_disclosure(wrapper):
    with pytest.raises(mod.ParseBlocked):
        mod.parse_hengrui(wrapper(chart()))


@pytest.mark.parametrize('suffix', [b'.concat([])', b'+ []', b'.map(x=>x)', b' && []'])
def test_literal_prefix_of_expression_is_rejected(suffix):
    with pytest.raises(mod.ParseBlocked, match='semicolon'):
        mod.parse_hengrui(chart().replace(b']];', b']]' + suffix + b';'))


def test_asof_excludes_comments_hidden_text_and_javascript():
    decoys=(b'<!-- *By Jan,1900 --> <script>const date="*By Jan,1901";</script>'
            b'<div hidden>*By Jan,1902</div><div class="hidden">*By Jan,1903</div>'
            b'<div style="display:none">*By Jan,1904</div><template>*By Jan,1905</template>')
    _, metadata=mod.parse_hengrui(decoys + chart())
    assert metadata['as_of']=='2026-07' and metadata['date_locator']=='*By Jul,2026'
    _, ambiguous=mod.parse_hengrui(b'<p>*By Jan,2025</p>'+chart())
    assert ambiguous['as_of'] is None and ambiguous['as_of_precision']=='unknown'


def test_final_provenance_metadata_matches_capture_and_parser(market):
    result=mod.ingest(market,'hengrui_pipeline',fetch=lambda url:chart())
    provenance=json.loads((market.root/result['archive_path']).with_name('provenance.json').read_text())
    metadata=json.loads(result['metadata'])
    assert provenance['metadata']==metadata
    assert provenance['parser']==metadata['parser']=='hengrui_javascript_literals_v1'
    for key in ('as_of','as_of_precision','published_at','scope','date_locator'):
        assert provenance[key]==metadata[key]
    assert provenance['as_of']==result['as_of']=='2026-07'
    first=mod.observations(market)[0]
    assert first['data']['source_script_index']>=1 and first['data']['source_line']>=1


def test_changed_owner_even_with_live_expiry_interrupts_old_capture(market):
    result=mod.ingest(market,'hengrui_pipeline',fetch=lambda url:chart())
    with market.db() as db:
        db.execute("UPDATE disclosure_captures SET status='running' WHERE id=?",(result['id'],))
        db.execute("INSERT INTO leases(key,until,owner) VALUES ('disclosure:hengrui_pipeline',1e15,'different-owner')")
    assert mod.captures(market)[0]['status']=='interrupted'
    with market.db() as db:
        assert db.execute("SELECT owner FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone()[0]=='different-owner'


def test_capture_insert_failure_releases_own_lease(market):
    mod._schema(market)
    with market.db() as db:
        db.executescript("CREATE TRIGGER reject_capture BEFORE INSERT ON disclosure_captures BEGIN SELECT RAISE(ABORT,'synthetic insert failure'); END;")
    with pytest.raises(Exception,match='synthetic insert failure'):
        mod.ingest(market,'hengrui_pipeline',fetch=lambda url:pytest.fail('Insert failed before fetch'))
    with market.db() as db:
        assert db.execute("SELECT 1 FROM leases WHERE key='disclosure:hengrui_pipeline'").fetchone() is None
