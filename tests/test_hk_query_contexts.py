"""Offline HK query integration. Synthetic fixtures never establish asset rights."""
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape
import zipfile

import pytest

from vbt import hk_issuer_names as hk
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import candidate_query_context, candidate_query_contexts, proposals, resolve
from vbt.sponsor_review import review


def workbook():
    rows = [['List of Securities'], ['Updated as at 17/10/2020'],
        ['Stock Code', 'Name of Securities', 'Category'],
        ['00512', 'GRAND PHARMA', 'Equity'], ['02410', 'TYK MEDICINES-B', 'Equity']]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, row in enumerate(rows, 1):
        xml += f'<row r="{number}">' + ''.join(
            f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for i, value in enumerate(row)) + '</row>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', xml + '</sheetData></worksheet>')
    return buffer.getvalue()


def source(full='Grand Pharmaceutical Group Limited', *, stamp=None):
    stamp = stamp or datetime.now(timezone.utc)
    columns = ['col-stock-code', 'col-stock-name', 'col-shareholding', 'col-shareholding-percent']
    headers = ['Stock Code', 'Name', 'Shareholding in CCASS', '% of the total number of Issued Shares/Units']
    text = '<html><body><span>Shareholding Date: ' + stamp.strftime('%Y/%m/%d') + '</span>'
    text += '<table id="mutualmarket-result"><thead><tr>' + ''.join('<th>' + x + '</th>' for x in headers)
    text += '</tr></thead><tbody>'
    for row in [('512', full, '100', '1%'), ('2410', 'TYK Medicines, Inc', '100', '')]:
        text += '<tr>' + ''.join(f'<td class="{c}"><div class="mobile-list-body">{escape(v)}</div></td>'
                                for c, v in zip(columns, row)) + '</tr>'
    raw = (text + '</tbody></table></body></html>').encode()
    metadata = dict(url=hk.SOUTHBOUND_URL, method='GET', status=200, bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=stamp.isoformat())
    return raw, metadata


def candidates(market):
    with market.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM issuer_candidates ORDER BY id')]


def registry(alias='Grand Pharmaceutical Group Limited'):
    return json.dumps({'studies': [{'protocolSection': {
        'identificationModule': {'nctId': 'NCT00000001'},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': alias}}}}]}).encode()


@pytest.fixture
def market(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    m.refresh_universe('HK', fetch=lambda url: workbook())
    refresh_candidates(m, [dict(market='HK', ticker=r['ticker'], name=r['name'],
        classification='candidate' if r['ticker'] == '00512' else 'uncertain',
        source='HK', reason='Synthetic engineering fixture only', evidence=[{'kind': 'synthetic'}])
        for r in m.rows('listings')])
    return m


def seal(market, full='Grand Pharmaceutical Group Limited', *, stamp=None):
    raw, metadata = source(full, stamp=stamp)
    return hk.seal_southbound(market, raw, metadata)


def test_verified_hk_name_overrides_usable_short_query_and_keeps_scope(market):
    candidate = candidates(market)[0]
    old = candidate_query_context(market, candidate)
    assert old['origin'] == 'listing_display_name' and old['query'] == 'grand pharma'
    with market.db() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='hk_issuer_name_receipts'").fetchone()
    seal(market)
    context = candidate_query_context(market, candidate)
    assert context['origin'] == 'verified_hk_official_name'
    assert context['query'] == 'grand pharmaceutical group'
    assert context['name_original'] == 'Grand Pharmaceutical Group Limited'
    proof = context['hk_name_evidence']
    assert proof['scope'].endswith('query_seed_only') and proof['human_sign_off'] is False
    assert proof['registry_alias_proven'] is False and proof['drug_ownership_verified'] is False
    assert market.count('companies') == market.count('trials') == 0


def test_batch_uses_one_hk_reader_and_does_not_lift_uncertain_classification(market, monkeypatch):
    seal(market); rows = candidates(market); calls = []
    original = hk.query_evidences
    def read(market, rows, **kwargs):
        calls.append(len(rows)); return original(market, rows, **kwargs)
    monkeypatch.setattr(hk, 'query_evidences', read)
    contexts = candidate_query_contexts(market, rows)
    assert calls == [2]
    assert all(c['origin'] == 'verified_hk_official_name' for c in contexts.values())
    assert candidates(market)[1]['classification'] == 'uncertain'


def test_real_resolver_freezes_uuid_receipt_and_keeps_suggestion_pending(market):
    seal(market); result = resolve(market, 'HK:00512', fetch=lambda url: registry())
    assert result['status'] == 'suggested' and result['query'] == 'grand pharmaceutical group'
    frozen = json.loads(result['query_evidence_json']); proof = frozen['hk_name_evidence']
    assert frozen['origin'] == 'verified_hk_official_name' and len(proof['receipt_id']) == 32
    with market.db() as db:
        ledger = db.execute('SELECT * FROM hk_issuer_name_receipts WHERE id=?', (proof['receipt_id'],)).fetchone()
    serial = hk.sealed_names._serial({k:v for k,v in proof.items() if k != 'receipt_id'})
    assert ledger['payload_json'] == serial and ledger['payload_sha256'] == hashlib.sha256(serial.encode()).hexdigest()
    assert proposals(market)[0]['status'] == 'pending' and not proposals(market)[0]['query_input_stale']
    from vbt.identity_collection import _gate, _row, eligible_proposals
    with market.db() as db:
        assert 'US official directories only' in _gate(dict(_row(db, proposals(market)[0]['id'])))
    assert eligible_proposals(market) == []
    assert market.count('companies') == market.count('trials') == 0


@pytest.mark.parametrize('field,value', [('receipt_id', '0'*32), ('raw_sha256', '0'*64),
    ('raw_bytes', float('nan')), ('scope', 'approved'), ('human_sign_off', True),
    ('candidate_hash', '0'*64), ('capture_id', '0'*32)])
def test_tampered_frozen_proof_cannot_be_accepted(market, field, value):
    seal(market); result = resolve(market, 'HK:00512', fetch=lambda url: registry())
    frozen = json.loads(result['query_evidence_json']); frozen['hk_name_evidence'][field] = value
    with market.db() as db:
        db.execute('UPDATE sponsor_resolution_runs SET query_evidence_json=? WHERE id=?',
                   (json.dumps(frozen), result['run_id']))
    row = proposals(market)[0]
    assert row['query_input_stale'] and 'Frozen query evidence' in row['stale_reason']
    with pytest.raises(ValueError, match='Frozen query evidence'):
        review(market, row['id'], 'accepted', 'Synthetic reviewer', 'Synthetic explicit review only')
    assert market.count('companies') == 0


def test_lost_current_proof_blocks_even_if_listing_fallback_query_is_identical(market):
    sealed = seal(market, 'Grand Pharma Limited')
    result = resolve(market, 'HK:00512', fetch=lambda url: registry('Grand Pharma Limited'))
    assert result['status'] == 'suggested' and result['query'] == 'grand pharma'
    (market.root / 'hk_issuer_names/captures' / sealed['capture_id'] / 'source.html').write_bytes(b'tampered')
    assert candidate_query_context(market, candidates(market)[0])['query'] == 'grand pharma'
    row = proposals(market)[0]
    assert row['query_input_stale'] and 'Current verified HK name evidence unavailable' in row['stale_reason']
    with pytest.raises(ValueError, match='Current verified HK name evidence unavailable'):
        review(market, row['id'], 'accepted', 'Synthetic reviewer', 'Synthetic explicit review only')
    assert market.count('companies') == 0


def test_same_query_new_receipt_does_not_invalidate_old_frozen_receipt(market):
    seal(market); result = resolve(market, 'HK:00512', fetch=lambda url: registry())
    old = json.loads(result['query_evidence_json'])['hk_name_evidence']['receipt_id']
    market.refresh_universe('HK', fetch=lambda url: workbook())
    seal(market)
    current = candidate_query_context(market, candidates(market)[0])['hk_name_evidence']
    assert current['receipt_id'] != old
    assert not proposals(market)[0]['query_input_stale']
    # This is an explicit synthetic human-declared review, never an automated HK review.
    review(market, proposals(market)[0]['id'], 'accepted', 'Synthetic reviewer', 'Synthetic explicit entity review only')
    assert market.count('companies') == 1 and market.count('trials') == 0
    with market.db() as db:
        assert db.execute('SELECT reviewer_type FROM sponsor_review_evidence').fetchone()[0] == 'human_declared'


def test_old_frozen_original_is_checked_even_when_fresh_same_name_source_is_valid(market):
    first = seal(market); resolve(market, 'HK:00512', fetch=lambda url: registry())
    seal(market, stamp=datetime.now(timezone.utc) + timedelta(seconds=1))
    (market.root / 'hk_issuer_names/captures' / first['capture_id'] / 'source.html').write_bytes(b'changed old source')
    assert candidate_query_context(market, candidates(market)[0])['origin'] == 'verified_hk_official_name'
    row = proposals(market)[0]
    assert row['query_input_stale'] and 'Frozen query evidence' in row['stale_reason']


@pytest.mark.parametrize('payload', ['[]', 'null'])
def test_damaged_capture_payload_has_readable_stale_reason(market, payload):
    seal(market); result = resolve(market, 'HK:00512', fetch=lambda url: registry())
    frozen = json.loads(result['query_evidence_json'])
    from vbt.sponsor_resolution import _hk_frozen_name_issue
    with market.db() as db:
        db.execute('DROP TRIGGER hk_issuer_name_captures_no_update')
        db.execute('UPDATE hk_issuer_name_captures SET payload_json=?,payload_sha256=? WHERE id=?',
                   (payload, hashlib.sha256(payload.encode()).hexdigest(), frozen['hk_name_evidence']['capture_id']))
        candidate = dict(db.execute('SELECT * FROM issuer_candidates WHERE id=?', ('HK:00512',)).fetchone())
        reason = _hk_frozen_name_issue(market, db, candidate, result, frozen, frozen)
    assert 'payload must be an object' in reason


def test_historical_receipt_older_than_current_freshness_window_can_still_be_reverified(market, monkeypatch):
    actual_datetime = hk.datetime; past = datetime.now(timezone.utc) - timedelta(days=9)
    class PastDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return past if tz is not None else past.replace(tzinfo=None)
    monkeypatch.setattr(hk, 'datetime', PastDatetime)
    seal(market, stamp=past); result = resolve(market, 'HK:00512', fetch=lambda url: registry())
    old = json.loads(result['query_evidence_json'])['hk_name_evidence']['receipt_id']
    monkeypatch.setattr(hk, 'datetime', actual_datetime)
    seal(market)
    current = candidate_query_context(market, candidates(market)[0])['hk_name_evidence']
    assert current['receipt_id'] != old
    assert not proposals(market)[0]['query_input_stale']


def test_changed_hk_query_reopens_old_zero_and_same_semantics_does_not(market):
    from vbt.market_traversal import readiness
    old = resolve(market, 'HK:00512', fetch=lambda url: b'{"studies":[]}')
    assert old['status'] == 'zero'
    assert next(r for r in readiness(market, 'HK')['rows'] if r['id'] == 'HK:00512')['readiness'] == 'zero_wait'
    seal(market)
    assert next(r for r in readiness(market, 'HK')['rows'] if r['id'] == 'HK:00512')['readiness'] == 'ready'
    latest = resolve(market, 'HK:00512', fetch=lambda url: b'{"studies":[]}')
    assert latest['query'] == 'grand pharmaceutical group'
    market.refresh_universe('HK', fetch=lambda url: workbook()); seal(market)
    assert next(r for r in readiness(market, 'HK')['rows'] if r['id'] == 'HK:00512')['readiness'] == 'zero_wait'


def test_source_change_during_fetch_retains_raw_but_does_not_publish_proposal(market):
    sealed = seal(market)
    def fetch(url):
        (market.root / 'hk_issuer_names/captures' / sealed['capture_id'] / 'source.html').write_bytes(b'changed')
        return registry()
    result = resolve(market, 'HK:00512', fetch=fetch)
    assert result['status'] == 'blocked' and proposals(market) == []
    assert (market.root / 'captures' / result['run_id'] / '0.bin').is_file()


def test_offline_seal_cli_does_not_construct_market_recover_jobs_or_fetch(market, tmp_path, capsys, monkeypatch):
    from vbt.cli import main
    raw, metadata = source(); raw_path = tmp_path / 'source.html'; raw_path.write_bytes(raw)
    meta_path = tmp_path / 'metadata.json'; meta_path.write_text(json.dumps(metadata))
    before = {k: market.rows(k) for k in ('runs', 'companies', 'trials')}
    monkeypatch.setattr(Market, '__init__', lambda *args, **kwargs: pytest.fail('Offline CLI must not recover jobs'))
    monkeypatch.setattr('vbt.market_sources.fetch', lambda *args, **kwargs: pytest.fail('Offline CLI must not fetch'))
    args = ['--workspace', str(market.root.parent.parent), '--state-dir', str(market.state),
            'market-hk-names', 'seal', '--raw', str(raw_path), '--metadata', str(meta_path)]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'sealed_query_seeds_only' and result['companies_added'] == 0
    assert {k: market.rows(k) for k in before} == before
    assert candidate_query_context(market, candidates(market)[0])['origin'] == 'verified_hk_official_name'
    assert main(args[:args.index('seal')] + args[args.index('seal') + 1:]) == 0
    assert json.loads(capsys.readouterr().out)['added'] == 0


@pytest.mark.parametrize('problem', ['bad_status', 'duplicate_json', 'oversized', 'missing_database'])
def test_offline_cli_invalid_inputs_fail_without_creating_state(tmp_path, capsys, problem):
    from vbt.cli import main
    raw, metadata = source()
    if problem == 'bad_status': metadata['status'] = '200'
    if problem == 'oversized': raw = b'x' * (hk.MAX_HTML_BYTES + 1)
    raw_path = tmp_path / 'source.html'; raw_path.write_bytes(raw)
    meta_path = tmp_path / 'metadata.json'; meta_path.write_text(json.dumps(metadata) if problem != 'duplicate_json' else '{"status":200,"status":200}')
    state = tmp_path / 'not-created'
    assert main(['--workspace', str(tmp_path / 'workspace'), '--state-dir', str(state),
        'market-hk-names', '--raw', str(raw_path), '--metadata', str(meta_path)]) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'blocked'
    assert not state.exists()


def test_offline_cli_does_not_resolve_away_state_symlink(market, tmp_path, capsys):
    from vbt.cli import main
    raw, metadata = source()
    raw_path = tmp_path / 'source.html'; raw_path.write_bytes(raw)
    meta_path = tmp_path / 'metadata.json'; meta_path.write_text(json.dumps(metadata))
    alias = tmp_path / 'state-alias'; alias.symlink_to(market.state, target_is_directory=True)
    assert main(['--workspace', str(market.root.parent.parent), '--state-dir', str(alias),
        'market-hk-names', 'seal', '--raw', str(raw_path), '--metadata', str(meta_path)]) == 2
    assert 'non-aliased' in json.loads(capsys.readouterr().err)['error']
    with market.db() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='hk_issuer_name_receipts'").fetchone()
