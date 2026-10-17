"""Default GET/POST policy integration with fake transports; no live requests."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt import bse_sources as bse, market_sources as sources, source_rate
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import resolve, proposals


@pytest.fixture
def market(tmp_path):
    m = Market(tmp_path / 'workspace', tmp_path / 'state')
    m.add_company('US', 'SYN', 'Syntheticnova Therapeutics Inc.',
                  ['Syntheticnova Therapeutics Inc.'], 'Synthetic scheduling fixture only')
    return m


def directory(name='Synthetic'):
    return json.dumps({'fields': ['name', 'ticker', 'exchange'],
                       'data': [[name, 'SYN', 'NYSE']]}).encode()


def trial(nct='NCT00000001', token=None):
    result = {'studies': [{'protocolSection': {
        'identificationModule': {'nctId': nct},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': 'Syntheticnova Therapeutics Inc.'}},
        'armsInterventionsModule': {'interventions': [{'type': 'DRUG', 'name': 'Synthetic drug'}]},
    }}]}
    if token: result['nextPageToken'] = token
    return json.dumps(result).encode()


def bse_page():
    return json.dumps([{'content': [{'xxzqdm': '920001', 'xxzqjc': 'Synthetic listed issuer',
        'xxhyzl': '医药制造业', 'xxfcbj': '2', 'xxzqjb': 'T'}],
        'totalPages': 1, 'totalElements': 1, 'size': 20, 'number': 0,
        'numberOfElements': 1, 'firstPage': True, 'lastPage': True}]).encode()


def transport(monkeypatch, response):
    requests = []
    def request(args, **kwargs):
        requests.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=response(args[-1]) + b'\n200', stderr=b'')
    monkeypatch.setattr('subprocess.run', request)
    return requests


def candidate(market):
    refresh_candidates(market, [{'market': 'US', 'ticker': 'SYNT',
        'name': 'Syntheticnova Therapeutics Inc. - Common Stock', 'classification': 'candidate',
        'reason': 'Synthetic fixture only', 'source': 'Synthetic', 'evidence': [{'kind': 'synthetic'}]}])


def test_default_directory_get_reserves_state_host_slot_then_archives(market, monkeypatch):
    slots = []
    monkeypatch.setattr(source_rate, 'wait_slot', lambda state, url, **kw: slots.append((state, url)))
    requests = transport(monkeypatch, lambda url: directory())
    rid = market.refresh_universe('US')
    assert slots == [(market.state, sources.URLS['US'])] and len(requests) == 1
    assert market.rows('runs')[-1]['status'] == 'succeeded'
    assert (market.root / 'captures' / rid / '0.bin').read_bytes() == directory()


def test_default_company_get_schedules_every_page(market, monkeypatch):
    slots = []
    monkeypatch.setattr(source_rate, 'wait_slot', lambda state, url, **kw: slots.append((state, url)))
    transport(monkeypatch, lambda url: trial('NCT00000002') if 'pageToken=' in url else trial(token='next'))
    market.sync_company('US:SYN')
    assert len(slots) == 2 and all(state == market.state for state, url in slots)
    assert all(urlsplit(url).hostname == 'clinicaltrials.gov' for state, url in slots)
    assert 'pageToken=next' in slots[1][1]
    assert market.rows('runs')[-1]['status'] == 'succeeded' and market.count('trials') == 2


def test_default_sponsor_resolution_get_uses_shared_policy(market, monkeypatch):
    candidate(market)
    slots = []
    monkeypatch.setattr(source_rate, 'wait_slot', lambda state, url, **kw: slots.append((state, url)))
    transport(monkeypatch, lambda url: trial())
    result = resolve(market, 'US:SYNT')
    assert result['status'] == 'suggested' and len(slots) == 1
    assert slots[0][0] == market.state and urlsplit(slots[0][1]).hostname == 'clinicaltrials.gov'
    assert proposals(market)[0]['status'] == 'pending'


@pytest.mark.parametrize('target', ['directory', 'company', 'resolution'])
def test_cancelled_slot_wait_never_gets_or_replaces_observations(market, monkeypatch, target):
    market.refresh_universe('US', fetch=lambda url: directory('Previous'))
    market.sync_company('US:SYN', fetch=lambda url: trial())
    candidate(market)
    before = (market.rows('listings'), market.rows('trials'))
    def blocked(*args, **kwargs): raise InterruptedError('Synthetic cancellation during source slot wait')
    monkeypatch.setattr(source_rate, 'wait_slot', blocked)
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: pytest.fail('Cancelled source wait must not GET'))
    if target == 'directory':
        market.refresh_universe('US'); status = market.rows('runs')[-1]['status']
    elif target == 'company':
        market.sync_company('US:SYN'); status = market.rows('runs')[-1]['status']
    else:
        status = resolve(market, 'US:SYNT')['status']
        assert proposals(market) == []
    assert status == 'cancelled'
    assert (market.rows('listings'), market.rows('trials')) == before


def test_cancel_after_slot_before_get_does_not_start_transport(market, monkeypatch):
    cancelled = [False]
    monkeypatch.setattr(source_rate, 'wait_slot', lambda *a, **kw: cancelled.__setitem__(0, True))
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: pytest.fail('No GET after cancellation'))
    market.refresh_universe('US', cancelled=lambda: cancelled[0])
    assert market.rows('runs')[-1]['status'] == 'cancelled'


def test_injected_synthetic_fetch_bypasses_all_physical_waits(market, monkeypatch):
    monkeypatch.setattr(source_rate, 'wait_slot', lambda *a, **kw: pytest.fail('Synthetic fetch must bypass waiting'))
    market.refresh_universe('US', fetch=lambda url: directory())
    market.sync_company('US:SYN', fetch=lambda url: trial())
    candidate(market)
    assert resolve(market, 'US:SYNT', fetch=lambda url: trial())['status'] == 'suggested'
    market.refresh_universe('BSE', bse_fetch=lambda url, data: bse_page())
    assert all(row['status'] == 'succeeded' for row in market.rows('runs'))


def test_cancel_during_company_publication_rolls_back_record_lineage_and_success(market, monkeypatch):
    market.sync_company('US:SYN', fetch=lambda url: trial())
    before = (market.rows('trials'), market.rows('changes'))
    cancelled = [False]
    original_db = market.db
    def db():
        connection = original_db()
        connection.create_function('fixture_cancel', 0, lambda: cancelled.__setitem__(0, True) or 1)
        return connection
    monkeypatch.setattr(market, 'db', db)
    with market.db() as connection:
        connection.execute("CREATE TRIGGER fixture_cancel_publication AFTER INSERT ON trials WHEN NEW.nct='NCT00000002' BEGIN SELECT fixture_cancel(); END")
    market.sync_company('US:SYN', fetch=lambda url: trial('NCT00000002'), cancelled=lambda: cancelled[0])
    assert cancelled[0] and market.rows('runs')[-1]['status'] == 'cancelled'
    assert (market.rows('trials'), market.rows('changes')) == before
    with market.db() as connection:
        assert connection.execute('SELECT count(*) FROM trial_lineage').fetchone()[0] == 1


def test_cancel_at_directory_success_marker_still_rolls_back_snapshot(market, monkeypatch):
    previous = market.refresh_universe('US', fetch=lambda url: directory('Previous'))
    before = market.rows('listings')
    cancelled = [False]
    original_db = market.db
    def db():
        connection = original_db()
        connection.create_function('fixture_cancel', 0, lambda: cancelled.__setitem__(0, True) or 1)
        return connection
    monkeypatch.setattr(market, 'db', db)
    with market.db() as connection:
        connection.execute("CREATE TRIGGER fixture_cancel_success AFTER UPDATE OF status ON runs WHEN NEW.status='succeeded' BEGIN SELECT fixture_cancel(); END")
    market.refresh_universe('US', fetch=lambda url: directory('New'), cancelled=lambda: cancelled[0])
    assert market.rows('listings') == before and market.rows('runs')[-1]['status'] == 'cancelled'
    with market.db() as connection:
        assert connection.execute("SELECT run_id FROM universe_snapshots WHERE source='US'").fetchone()[0] == previous


def test_default_get_transport_tls_no_redirects_or_credentials(monkeypatch):
    requests = transport(monkeypatch, lambda url: b'public bytes')
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC SECRET MUST NOT LEAVE ENV')
    assert sources.fetch(sources.URLS['US']) == b'public bytes'
    args, kwargs = requests[0]
    assert args[1] == '--disable' and '--insecure' not in args and '--location' not in args
    assert args[args.index('--max-redirs') + 1] == '0'
    assert kwargs['timeout'] == 100 and 'OPENAI_API_KEY' not in kwargs['env']


@pytest.mark.parametrize('status', [b'201', b'301', b'302', b'403'])
def test_default_get_non_200_never_returns_plausible_json(monkeypatch, status):
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=directory() + b'\n' + status, stderr=b''))
    with pytest.raises(ValueError, match='HTTP 200'): sources.fetch(sources.URLS['US'])


class Response:
    def __init__(self, raw, url=bse.ENDPOINT, status=200): self.raw, self.url, self.status = raw, url, status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.raw[:limit]
    def geturl(self): return self.url
    def getcode(self): return self.status


def bse_transport(monkeypatch, *, status=200, url=bse.ENDPOINT):
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return Response(bse_page(), url=url, status=status)
    def opener(*handlers):
        assert len(handlers) == 1 and isinstance(handlers[0], bse._NoRedirect)
        return Opener()
    monkeypatch.setattr(bse, 'build_opener', opener)
    return requests


def test_default_bse_post_schedules_all_pages_and_final_first_page(market, monkeypatch):
    slots = []
    monkeypatch.setattr(source_rate, 'wait_slot', lambda state, url, **kw: slots.append((state, url)))
    requests = bse_transport(monkeypatch)
    rid = market.refresh_universe('BSE')
    assert market.rows('runs')[-1]['status'] == 'succeeded'
    assert slots == [(market.state, bse.ENDPOINT)] * 2 and len(requests) == 2
    assert all(request.get_method() == 'POST' and timeout == 30 for request, timeout in requests)
    assert all(parse_qs(request.data.decode())['typejb'] == ['T'] for request, timeout in requests)
    assert len(list((market.root / 'captures' / rid).glob('*.bin'))) == 2


def test_real_bse_universe_requires_explicit_local_state_before_post(monkeypatch):
    monkeypatch.setattr(bse, 'build_opener', lambda *a: pytest.fail('Missing state must not POST'))
    with pytest.raises(ValueError, match='local state'): bse.fetch_universe()


def test_cancelled_bse_slot_wait_never_posts_or_replaces_snapshot(market, monkeypatch):
    market.refresh_universe('BSE', bse_fetch=lambda url, data: bse_page())
    before = market.rows('listings')
    def cancelled(*a, **kw): raise InterruptedError('Synthetic cancellation during BSE slot wait')
    monkeypatch.setattr(source_rate, 'wait_slot', cancelled)
    monkeypatch.setattr(bse, 'build_opener', lambda *a: pytest.fail('Cancelled wait must not POST'))
    market.refresh_universe('BSE')
    assert market.rows('runs')[-1]['status'] == 'cancelled' and market.rows('listings') == before


def test_bse_non_200_response_is_accurately_archived_and_rejected(market, monkeypatch):
    market.refresh_universe('BSE', bse_fetch=lambda url, data: bse_page())
    before = market.rows('listings')
    monkeypatch.setattr(source_rate, 'wait_slot', lambda *a, **kw: None)
    bse_transport(monkeypatch, status=201)
    rid = market.refresh_universe('BSE')
    assert market.rows('runs')[-1]['status'] == 'failed' and market.rows('listings') == before
    capture = json.loads((market.root / 'captures' / rid / '0.json').read_text())
    assert capture['status'] == 201 and capture['method'] == 'POST'


def test_bse_final_url_and_redirect_are_rejected(monkeypatch):
    bse_transport(monkeypatch, url='https://evil.example/source')
    with pytest.raises(ValueError, match='URL changed'): bse.fetch_page(bse.ENDPOINT, data=bse.request_body(0))
    assert bse._NoRedirect().redirect_request(None, None, 302, 'Redirect', {}, 'https://evil.example/') is None


def test_bse_code_is_ascii_not_unicode_digit_lookalike():
    raw = bse_page().replace(b'920001', '920٠٠١'.encode())
    with pytest.raises(ValueError, match='security code'): bse.parse(raw)


@pytest.mark.parametrize('url', ['https://@clinicaltrials.gov/', 'https://clinicaltrials.gov/#section', ' https://clinicaltrials.gov/', 'https://clinicaltrials.gov/\nwrong'])
def test_get_validation_rejects_empty_userinfo_fragments_and_control_characters(url):
    with pytest.raises(ValueError): sources.validate_url(url)


def test_market_state_is_rejected_before_external_directory_creation(tmp_path):
    from pathlib import Path
    missing = Path('/Volumes/NotAMountedVolume/vbt-scheduling-fixture')
    assert not missing.exists()
    with pytest.raises(ValueError, match='local'):
        Market(tmp_path / 'workspace', missing)
    assert not missing.exists()


def test_unicode_nct_digits_never_publish_company_or_sponsor_evidence(market):
    market.sync_company('US:SYN', fetch=lambda url: trial())
    before = market.rows('trials')
    market.sync_company('US:SYN', fetch=lambda url: trial('NCT0000000١'))
    assert market.rows('runs')[-1]['status'] == 'failed' and market.rows('trials') == before
    candidate(market)
    assert resolve(market, 'US:SYNT', fetch=lambda url: trial('NCT0000000١'))['status'] == 'failed'
    assert proposals(market) == []
