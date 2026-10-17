"""Synthetic UI/reconciliation checks; no assertions about real pipeline coverage."""
import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from vbt import disclosures
from vbt.disclosure_view import link_registry, render, select_observations
from vbt.market import Market


def chart(names=('HRS-0001',)):
    scripts = []
    for array, grid in [('dataIII', 'grid-iii'), ('dataNDA', 'grid-nda'), ('dataII', 'grid-ii'), ('dataI', 'grid-i')]:
        records = ','.join("{name:" + repr(name) + ",target:'Synthetic Target ADC',indications:['Synthetic disease','Synthetic other'],type:'tumor'}" for name in names)
        scripts.append('const ' + array + '=[[' + records + "]]; renderGrid('" + grid + "'," + array + ');')
    return ('<h4>Clinical Development Pipeline</h4><p>*By Jul,2026</p><script>' + ''.join(scripts) + '</script>').encode()


@pytest.fixture
def app(tmp_path):
    market = Market(tmp_path / 'workspace', tmp_path / 'state')
    return SimpleNamespace(market=market, workspace=tmp_path / 'workspace', state=tmp_path / 'state', token='synthetic-csrf-token')


def put_trial(market, company, nct, interventions, **extra):
    data = {'interventions': interventions, 'phases': ['PHASE2'], 'conditions': ['Synthetic registry disease'],
            'source_url': 'javascript:malicious()', 'source_updated': '2026-07-01', **extra}
    with market.db() as db:
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)', (company, nct, json.dumps(data), 'synthetic-hash', '2026-10-17T00:00:00+00:00'))


def observation(name='HRS-0001', code='HRS-0001', companies=None):
    return {'id': 'synthetic', 'drug_name': name, 'drug_code': code,
            'companies': companies or ['A:600276']}


def test_empty_state_refresh_forms_and_separate_dates(app):
    body = render(app, {})
    assert '官方管线披露与登记核对' in body
    assert '药物—适应症—阶段观察 · 0 条' in body
    assert '没有可展示的已结构化观察' in body
    assert body.count('action="/market/disclosure/enqueue"') == len(disclosures.SOURCES)
    assert 'name="source_id"' in body and 'synthetic-csrf-token' in body
    assert '来源内容截止时间' in body and '来源发布日期' in body
    assert 'value=""' in body
    assert 'value="尚未提供"' not in body
    assert '完整管线' in body and '药物所有权' in body


def test_company_without_disclosure_source_keeps_explicit_gap(app):
    app.market.add_company('A', '002653', 'Synthetic company', ['Synthetic Sponsor'], 'Synthetic fixture')
    body = render(app, {'company': 'A:002653'})
    assert '该证券尚未配置官方管线披露来源' in body
    assert '此处为空不表示公司没有研发管线' in body
    assert '/market/disclosure/enqueue' not in body
    with pytest.raises(ValueError):
        render(app, {'company': 'A:000000'})


def test_exact_name_code_only_same_company_and_no_alias_or_title_inference(app):
    m = app.market
    put_trial(m, 'A:600276', 'NCT00000001', [{'type': 'DRUG', 'name': '  ＨＲＳ-０００１ ', 'arm_roles': ['ACTIVE_COMPARATOR']}])
    put_trial(m, 'US:PFE', 'NCT00000002', [{'type': 'DRUG', 'name': 'HRS-0001', 'arm_roles': ['EXPERIMENTAL']}])
    put_trial(m, 'A:600276', 'NCT00000003', [{'type': 'DRUG', 'name': 'Different Generic', 'other_names': ['HRS-0001'], 'arm_roles': ['EXPERIMENTAL']}], title='HRS-0001')
    put_trial(m, 'A:600276', 'NCT00000004', [{'type': 'DRUG', 'name': 'HRS0001', 'arm_roles': ['EXPERIMENTAL']}])
    rows, malformed = link_registry(m, [observation()])
    assert malformed == 0
    links = rows[0]['registry_links']
    assert [(r['company'], r['nct']) for r in links] == [('A:600276', 'NCT00000001')]
    assert links[0]['url'] == 'https://clinicaltrials.gov/study/NCT00000001'
    assert links[0]['arm_roles'] == ['ACTIVE_COMPARATOR']
    assert rows[0]['linkage'] == 'exact_intervention_name'
    assert link_registry(m, [observation(name='Different code', code=None)])[0][0]['linkage'] == 'unresolved'


def test_code_and_name_match_one_trial_once_with_explicit_roles(app):
    put_trial(app.market, 'A:600276', 'NCT00000005', [
        {'type': 'DRUG', 'name': 'HRS-0001', 'arm_roles': ['EXPERIMENTAL']},
        {'type': 'BIOLOGICAL', 'name': 'Synthetic Generic', 'arm_roles': ['ACTIVE_COMPARATOR']},
    ])
    rows, _ = link_registry(app.market, [observation(name='Synthetic Generic')])
    assert len(rows[0]['registry_links']) == 1
    link = rows[0]['registry_links'][0]
    assert set(link['intervention_names']) == {'HRS-0001', 'Synthetic Generic'}
    assert link['arm_roles'] == ['ACTIVE_COMPARATOR', 'EXPERIMENTAL']


def test_malformed_registry_rows_are_reported_without_invented_links(app):
    with app.market.db() as db:
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)', ('A:600276', 'NCT00000006', '{broken json', 'synthetic', '2026-10-17'))
    rows, malformed = link_registry(app.market, [observation()])
    assert malformed == 1 and not rows[0]['registry_links']


def test_pagination_is_latest_success_only_with_visible_failed_attempt(app):
    m = app.market
    old = disclosures.ingest(m, 'hengrui_pipeline', fetch=lambda _: chart(('HRS-OLD',)))
    good = disclosures.ingest(m, 'hengrui_pipeline', fetch=lambda _: chart(tuple('HRS-' + str(i).zfill(4) for i in range(8))))
    failed = disclosures.ingest(m, 'hengrui_pipeline', fetch=lambda _: b'<html>Access challenge</html>')
    sources = disclosures.sources(m)
    first, total, state = select_observations(m, sources)
    second, other_total, _ = select_observations(m, sources, offset=50)
    assert total == other_total == 64 and len(first) == 50 and len(second) == 14
    assert not ({r['id'] for r in first} & {r['id'] for r in second})
    assert {r['capture_id'] for r in first + second} == {good['id']}
    assert all(r['prior_capture_retained'] and r['latest_attempt_status'] == failed['status'] for r in first)
    assert state['successful']['hengrui_pipeline']['id'] == good['id']
    assert old['id'] != good['id']
    body = render(app, {})
    assert '药物—适应症—阶段观察 · 64 条' in body
    assert '仍展示最近成功批次' in body
    assert '2026-07' in body and '来源发布日期' in body
    assert 'offset=50' in body
    assert 'HRS-OLD' not in body


def test_filters_treat_percent_and_underscore_as_literal_and_escape_html(app):
    m = app.market
    disclosures.ingest(m, 'hengrui_pipeline', fetch=lambda _: chart(('SYNTHETIC_%', 'SYNTHETIC-OTHER')))
    sources = disclosures.sources(m)
    rows, total, _ = select_observations(m, sources, query='_%', phase='Phase II')
    assert total == 2 and {r['drug_name'] for r in rows} == {'SYNTHETIC_%'}
    body = render(app, {'q': '"><script>alert(1)</script>', 'offset': 'malformed'})
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body
    with pytest.raises(ValueError):
        render(app, {'source_id': 'unconfigured'})
    with pytest.raises(ValueError):
        render(app, {'q': 'x' * 1001})


def test_render_linked_comparator_never_labels_owned_pipeline(app):
    disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    put_trial(app.market, 'A:600276', 'NCT00000007', [{'type': 'DRUG', 'name': 'HRS-0001', 'arm_roles': ['ACTIVE_COMPARATOR']}])
    body = render(app, {'company': 'A:600276', 'phase': 'Phase II'})
    assert 'NCT00000007' in body and '活性对照组' in body
    assert 'https://clinicaltrials.gov/study/NCT00000007' in body
    assert 'javascript:malicious' not in body
    assert '登记阶段：PHASE2' in body
    assert '登记适应症：Synthetic registry disease' in body
    assert '2 条具有同公司登记名称关联' in body
    assert '分组角色不确认资产所有权' in body
    assert 'SHA-256' in body and 'script const dataII' in body


class Structure(HTMLParser):
    """Inspect usable markup without tying tests to presentation rules."""
    def __init__(self, body):
        super().__init__()
        self.elements = []
        self.detail_depth = 0
        self.main_text = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.detail_depth += 1
        self.elements.append((tag, dict(attrs), self.detail_depth))

    def handle_endtag(self, tag):
        if tag == 'details':
            self.detail_depth -= 1

    def handle_data(self, data):
        if not self.detail_depth:
            self.main_text.append(data)


def test_table_keyboard_region_labels_and_collapsible_technical_evidence(app):
    result = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    parsed = Structure(render(app, {'company': 'A:600276'}))
    regions = [attrs for tag, attrs, _ in parsed.elements if attrs.get('role') == 'region']
    assert len(regions) == 1 and regions[0]['tabindex'] == '0'
    assert regions[0]['aria-label'] and regions[0]['aria-describedby']
    assert len([attrs for tag, attrs, _ in parsed.elements if tag == 'th' and attrs.get('scope') == 'col']) == 5
    controls = {attrs['id'] for tag, attrs, _ in parsed.elements if tag in ('input', 'select') and 'id' in attrs}
    labels = {attrs['for'] for tag, attrs, _ in parsed.elements if tag == 'label'}
    assert controls == labels
    visible = ''.join(parsed.main_text)
    # Long reproducibility references remain available in closed details, while
    # the drug, source dates, missing association and research boundary stay visible.
    assert result['id'] not in visible and 'script const dataII' not in visible
    assert 'HRS-0001' in visible and '内容截止' in visible and '精确名称关联待解决' in visible
    assert '药物所有权' in visible


def test_source_dates_preserve_precision_and_exact_timestamp_with_timezone(app):
    result = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    with app.market.db() as db:
        db.execute('UPDATE disclosure_captures SET finished=?,published_at=? WHERE id=?',
                   ('2026-10-16T23:45:00+00:00', '2026-07-02', result['id']))
    body = render(app, {'company': 'A:600276'})
    assert '2026/10/17 07:45 台北' in body
    assert 'datetime="2026-10-16T23:45:00+00:00"' in body
    assert 'title="原始时间：2026-10-16T23:45:00+00:00"' in body
    assert 'datetime="2026-07"' in body and '2026 年 07 月' in body
    assert 'datetime="2026-07-02"' in body and '2026/07/02' in body
    with app.market.db() as db:
        db.execute('UPDATE disclosure_captures SET finished=? WHERE id=?',
                   ('2026-10-16T23:45:00', result['id']))
    assert '2026/10/16 23:45（时区未提供）' in render(app, {'company': 'A:600276'})


def test_source_failed_attempt_remains_visible_without_discarding_success(app):
    successful = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart())
    failed = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: b'<html>Access challenge</html>')
    parsed = Structure(render(app, {'company': 'A:600276'}))
    visible = ''.join(parsed.main_text)
    assert '结构化提取待完成' in visible
    assert '存在来源缺口' in visible and '仍展示最近成功批次' in visible
    assert '8 条结构化观察' in visible and 'HRS-0001' in visible
    assert successful['id'] not in visible and failed['id'] not in visible
    assert '此处为空不表示公司没有研发管线' not in visible
