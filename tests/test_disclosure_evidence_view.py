"""Synthetic UI evidence availability; no claim about clinical or ownership review."""
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from vbt import disclosures
from vbt.disclosure_view import _source_card, render
from vbt.market import Market


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.links = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.depth += 1
        if tag == 'a':
            self.links.append((dict(attrs), self.depth))

    def handle_endtag(self, tag):
        if tag == 'details':
            self.depth -= 1

    def evidence(self):
        return [(attrs, depth) for attrs, depth in self.links
                if urlsplit(attrs.get('href', '')).path == '/market/disclosure-evidence']


def chart():
    arrays = []
    for name, grid in [('dataIII', 'grid-iii'), ('dataNDA', 'grid-nda'), ('dataII', 'grid-ii'), ('dataI', 'grid-i')]:
        arrays.append("const " + name + "=[[{name:'SYNTHETIC-001',target:'Synthetic target',indications:['Synthetic indication'],type:'tumor'}]];renderGrid('" + grid + "'," + name + ');')
    return ('<h4>Clinical Development Pipeline</h4><p>*By Jul,2026</p><script>' + ''.join(arrays) + '</script>').encode()


@pytest.fixture
def app(tmp_path):
    app = SimpleNamespace(workspace=tmp_path / 'workspace', state=tmp_path / 'state', token='synthetic<&token')
    app.market = Market(app.workspace, app.state)
    return app


def config(source_id='hengrui_pipeline'):
    return {'id': source_id, **disclosures.SOURCES[source_id]}


def test_verified_evidence_controls_are_visible_and_fixed_to_one_capture(app):
    capture = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    body = render(app, {'company': 'A:600276', 'source_id': 'hengrui_pipeline'})
    links = Page(body).evidence()
    assert len(links) == 2 and all(depth == 0 for _, depth in links)
    targets = [parse_qs(urlsplit(link['href']).query) for link, _ in links]
    assert targets == [{'id': [capture['id']], 'file': ['source']}, {'id': [capture['id']], 'file': ['record']}]
    assert all(link['target'] == '_blank' and link['rel'] == 'noopener noreferrer' for link, _ in links)
    assert '顶部官方来源为站点当前页；这里固定于该来源批次' in body
    assert '网页原件按源文本只读打开' in body and '打开时再次核对完整性' in body
    assert '观察行按药物—适应症—阶段计数' in body
    assert 'synthetic&lt;&amp;token' in body
    for link, _ in links:
        params = {key: values[0] for key, values in parse_qs(urlsplit(link['href']).query).items()}
        raw, media = disclosures.evidence_file(app.market, params['id'], params['file'])
        assert raw and (media.startswith('text/plain') or media.startswith('application/json'))


@pytest.mark.parametrize('damage', ['missing', 'changed', 'observation'])
def test_read_integrity_blocked_hides_all_evidence_controls_and_keeps_unknown_count(app, damage):
    capture = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    path = app.market.root / capture['archive_path']
    if damage == 'missing':
        path.unlink()
    elif damage == 'changed':
        path.write_bytes(b'Synthetic changed source')
    else:
        with app.market.db() as db:
            db.execute("UPDATE disclosure_observations SET phase='changed' WHERE capture_id=?", (capture['id'],))
    body = render(app, {'source_id': 'hengrui_pipeline'})
    assert not Page(body).evidence()
    assert '原件完整性受阻，观察暂不展示' in body
    assert '可核验观察数量未知' in body
    assert 'SYNTHETIC-001' not in body
    with app.market.db() as db:
        assert db.execute('SELECT status FROM disclosure_captures WHERE id=?', (capture['id'],)).fetchone()[0] == 'succeeded'


@pytest.mark.parametrize('integrity', [None, {}, {'integrity_status': 'blocked'}, {'integrity_status': 'unknown'}, {'status': 'verified'}])
def test_source_card_never_infers_usable_evidence_from_execution_success_alone(app, integrity):
    capture = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    body = _source_card({**config(), 'latest': capture}, app.token, {}, capture, integrity)
    assert not Page(body).evidence()
    assert '封存附件入口暂不可用' in body


@pytest.mark.parametrize('changes', [{'id': '../outside'}, {'id': '<img src=x onerror=evil()>'},
    {'source_id': 'merck_pipeline'}, {'company': 'US:MRK'}, {'url': 'https://example.invalid/source'}, {'status': 'parse_blocked'}])
def test_verified_flag_cannot_offer_wrong_source_company_id_or_failed_capture(app, changes):
    capture = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    body = _source_card({**config(), 'latest': capture}, app.token, {}, {**capture, **changes}, {'integrity_status': 'verified'})
    assert not Page(body).evidence()
    assert '封存附件入口暂不可用' in body
    assert '<img' not in body


def test_latest_failure_uses_only_the_older_verified_capture_for_evidence(app):
    successful = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    failed = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: b'<html>Synthetic unsupported layout</html>')
    body = render(app, {'source_id': 'hengrui_pipeline'})
    links = Page(body).evidence()
    assert len(links) == 2
    assert all(parse_qs(urlsplit(link['href']).query)['id'] == [successful['id']] for link, _ in links)
    assert all(failed['id'] not in link['href'] for link, _ in links)
    assert '当前仍展示最近成功批次' in body


def test_never_collected_source_has_no_evidence_action(app):
    body = render(app, {'source_id': 'hengrui_pipeline'})
    assert not Page(body).evidence()
    assert '已配置，尚未抓取' in body
