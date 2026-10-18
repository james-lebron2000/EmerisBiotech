"""Synthetic UI/reconciliation checks; no assertions about real pipeline coverage."""
import json
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

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


@pytest.mark.parametrize('company',['','A:600276'])
def test_disclosure_header_retains_company_in_actual_registry_and_asset_routes(app,company):
    app.market.add_company('A','600276','Synthetic issuer',['Synthetic sponsor'],'Synthetic fixture')
    body=render(app,{'company':company,'q':'HRS-0001','phase':'Phase II'})
    header=Structure(body.split('</header>',1)[0])
    targets=[attrs['href'] for tag,attrs,_ in header.elements if tag=='a' and urlsplit(attrs.get('href','')).path in ('/market','/assets')]
    assert len(targets)==2
    from vbt import market_view,market_asset_view
    for target in targets:
        params={key:value[0] for key,value in parse_qs(urlsplit(target).query).items()}
        assert params==({'company':company} if company else {})
        page=market_view.render(app,params) if urlsplit(target).path=='/market' else market_asset_view.render(app,params)
        assert 'Synthetic issuer' in page


def test_disclosure_nct_link_opens_only_same_company_local_observation(app):
    disclosures.ingest(app.market,'hengrui_pipeline',fetch=lambda _:chart())
    app.market.add_company('A','600276','Synthetic current issuer',['Synthetic sponsor'],'Synthetic fixture')
    app.market.add_company('US','OTHER','Other unrelated issuer',['Other sponsor'],'Synthetic fixture')
    for company in ('A:600276','US:OTHER'):
        put_trial(app.market,company,'NCT00000007',[{'type':'DRUG','name':'HRS-0001','arm_roles':['ACTIVE_COMPARATOR']}])
    body=render(app,{'company':'A:600276','phase':'Phase II'})
    targets={attrs['href'] for tag,attrs,_ in Structure(body).elements if tag=='a' and urlsplit(attrs.get('href','')).path=='/market' and 'q=' in attrs.get('href','')}
    assert len(targets)==1
    target=targets.pop()
    assert parse_qs(urlsplit(target).query)=={'company':['A:600276'],'q':['NCT00000007']}
    assert '&amp;' in body and '分组角色不确认资产所有权' in body
    from vbt.market_view import render as registry
    params={key:value[0] for key,value in parse_qs(urlsplit(target).query).items()}
    local=registry(app,params)
    assert '共 1 条证券—试验关联' in local
    assert 'Other unrelated issuer' not in local.split('<tbody>',1)[1].split('</tbody>',1)[0]


@pytest.mark.parametrize('invalid',['NCT0000007','nct00000007','NCT00000007\"><script>evil()</script>'])
def test_invalid_registry_nct_cannot_become_a_local_context_link(app,invalid):
    disclosures.ingest(app.market,'hengrui_pipeline',fetch=lambda _:chart())
    put_trial(app.market,'A:600276',invalid,[{'type':'DRUG','name':'HRS-0001'}])
    body=render(app,{'company':'A:600276'})
    assert '查看同公司本地登记记录' not in body
    assert not any(tag in ('img','script') for tag,_,_ in Structure(body).elements)


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


def historical_capture(app, monkeypatch, *, precision='day', phase_values=None):
    """Synthetic view contract; the separate read gate is mocked, not validated."""
    disclosures.initialize_storage(app.market)
    source_id, capture_id = 'ascentage_annual_pipeline_2025', 'a' * 32
    metadata = {'historical_disclosure': True, 'as_of_precision': precision,
        'reporting_period_original': 'ANNUAL REPORT 2025', 'published_at': None,
        'original_retrieved_at': '2026-10-17T07:31:57+00:00',
        'retrieved_at': '2000-01-01T00:00:00+00:00', 'imported_at': '2026-10-18T03:00:00+00:00',
        'human_sign_off': False, 'drug_ownership_verified': False, 'scientific_review': 'pending',
        'bundle_sha256': 'b' * 64, 'raw_reference': 'artifacts/market/synthetic-frozen.pdf',
        'visual_review': {'reviewer_type': 'assistant_visual_review', 'method': 'full_selected_chart_row_visual_review',
                          'reviewed_at': '2026-10-17', 'coverage': 'all_rows_in_selected_chart', 'human_sign_off': False}}
    phases = phase_values or ['Clinical POC', 'Phase II/III', 'Pre-clinical']
    as_of = '2025-12-31' if precision == 'day' else None
    with app.market.db() as db:
        db.execute('''INSERT INTO disclosure_captures
          (id,source_id,company,started,finished,status,url,sha256,bytes,archive_path,content_type,
           published_at,as_of,metadata,count,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (capture_id, source_id, 'HK:06855', metadata['imported_at'], metadata['imported_at'], 'succeeded',
           disclosures.SOURCES[source_id]['url'], 'c' * 64, 100, 'synthetic/source.bin', 'application/pdf',
           None, as_of, json.dumps(metadata), len(phases), ''))
        for number, phase in enumerate(phases, 1):
            original = {'locator': {'pdf_page': 17, 'printed_page': '16', 'chart_row_ordinal': number,
                        'row_bbox_pdf_pt': [73, 343, 516, 355], 'drug_cell_bbox_pdf_pt': [73, 343, 126, 368],
                        'phase_header_bbox_pdf_pt': [329, 327, 390, 342]}, 'phase_original': phase,
                        'study_label_original': 'Synthetic study', 'footnote_markers': ['1']}
            data = {'original_observation': original, 'missing_fields': ['target', 'modality'],
                    'phase_semantics': 'issuer_chart_terminal_column_only', 'clinical_phase_code': None}
            db.execute('INSERT INTO disclosure_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('synthetic-' + str(number), capture_id, source_id, 'HK:06855', 'SYN-001',
                        'Synthetic frozen drug', None, None, 'Synthetic historical disease', phase,
                        'PDF page 17, chart row ' + str(number), json.dumps(data)))
    monkeypatch.setattr(disclosures, 'capture_integrity', lambda market, capture:
                        {'integrity_status': 'verified', 'errors': []})
    return source_id, capture_id


def test_offline_source_has_manual_intake_and_distinct_historical_dates(app, monkeypatch):
    source_id, _ = historical_capture(app, monkeypatch)
    body = render(app, {'source_id': source_id})
    assert '离线历史 PDF 图表' in body and '历史 PDF 图表观察' in body
    assert '验收本地历史原件' in body and '排队刷新官方来源' not in body
    assert '不进行定时网络刷新或下载' in body
    assert 'ANNUAL REPORT 2025' in body and '2025/12/31' in body
    assert '原件取回时间' in body and '2026/10/17 15:31 台北' in body
    assert '本地验收入库' in body and '2026/10/18 11:00 台北' in body
    assert '2000/01/01' not in body
    assert '来源发布日期</dt><dd><span class="muted">尚未提供' in body
    assert '已配置来源 1 个 · 离线历史来源 1 个 · 当前可核验结构化来源 1 个' in body


def test_unknown_report_date_does_not_become_annual_year_or_import_date(app, monkeypatch):
    source_id, _ = historical_capture(app, monkeypatch, precision='unresolved_report_date')
    body = render(app, {'source_id': source_id})
    assert '未知（报告日期尚未确定）' in body
    assert 'ANNUAL REPORT 2025' in body
    assert '2025/12/31' not in body and '2024/12/31' not in body


def test_historical_locator_and_visual_review_are_not_human_or_scientific_approval(app, monkeypatch):
    source_id, _ = historical_capture(app, monkeypatch)
    body = render(app, {'source_id': source_id})
    for expected in ('物理 PDF 页', '印刷页码', '图表行', '[73, 343, 516, 355]',
                     '[329, 327, 390, 342]', '助理视觉审查，非人工签署', '科学审查待完成',
                     '药物权益未独立核验', 'full_selected_chart_row_visual_review', '转录包 SHA-256'):
        assert expected in body
    assert '不推断登记临床分期编码或最高阶段' in body
    assert '尚未提供' in body  # target/modality remain missing, not inferred.


def test_phase_options_are_actual_originals_and_combined_get_filters_work(app, monkeypatch):
    source_id, _ = historical_capture(app, monkeypatch)
    body = render(app, {'company': 'HK:06855', 'source_id': source_id, 'q': 'Synthetic', 'phase': 'Phase II/III'})
    parsed = Structure(body)
    options = {attrs.get('value'): attrs for tag, attrs, _ in parsed.elements if tag == 'option'}
    assert {'Clinical POC', 'Phase II/III', 'Pre-clinical'} <= set(options)
    assert 'selected' in options['Phase II/III']
    assert 'PHASE3' not in options and 'NDA/BLA' not in options
    assert '药物—适应症—阶段观察 · 1 条' in body
    fields = {attrs.get('name'): attrs for tag, attrs, _ in parsed.elements if tag in ('input', 'select')}
    assert fields['q']['value'] == 'Synthetic'


def test_historical_integrity_failure_hides_rows_without_faking_zero_source_count(app, monkeypatch):
    source_id, _ = historical_capture(app, monkeypatch)
    monkeypatch.setattr(disclosures, 'capture_integrity', lambda market, capture:
                        {'integrity_status': 'blocked', 'errors': ['Synthetic original PDF is missing']})
    body = render(app, {'source_id': source_id})
    assert '原件完整性受阻，观察暂不展示' in body
    assert '可核验观察数量未知' in body and '未知数量不能记作零' in body
    assert 'Synthetic frozen drug' not in body and '3 条结构化观察' not in body
    assert '当前可核验结构化来源 0 个' in body and '配置后失败或受阻 1 个' in body
    assert '最近执行状态：结构化提取完成' in body


def test_network_archive_tampering_also_closes_ui_gate(app):
    capture = disclosures.ingest(app.market, 'hengrui_pipeline', fetch=lambda _: chart(('HRS-SHOULD-HIDE',)))
    (app.market.root / capture['archive_path']).write_bytes(b'tampered')
    body = render(app, {'source_id': 'hengrui_pipeline'})
    assert '原件完整性受阻，观察暂不展示' in body
    assert 'HRS-SHOULD-HIDE' not in body and '可核验观察数量未知' in body
    with app.market.db() as db:
        assert db.execute('SELECT status FROM disclosure_captures WHERE id=?', (capture['id'],)).fetchone()[0] == 'succeeded'


def test_historical_metadata_and_locator_are_escaped(app, monkeypatch):
    source_id, capture_id = historical_capture(app, monkeypatch)
    attack = '<img src=x onerror=evil()><script>evil()</script>'
    with app.market.db() as db:
        cap = db.execute('SELECT metadata FROM disclosure_captures WHERE id=?', (capture_id,)).fetchone()
        metadata = json.loads(cap[0]); metadata['reporting_period_original'] = attack
        metadata['visual_review']['method'] = attack
        db.execute('UPDATE disclosure_captures SET metadata=? WHERE id=?', (json.dumps(metadata), capture_id))
        for row in db.execute('SELECT id,data FROM disclosure_observations WHERE capture_id=?', (capture_id,)).fetchall():
            data = json.loads(row['data']); data['original_observation']['locator']['printed_page'] = attack
            db.execute('UPDATE disclosure_observations SET data=? WHERE id=?', (json.dumps(data), row['id']))
    body = render(app, {'source_id': source_id})
    assert attack not in body and '&lt;img' in body
    assert not any(tag in ('img', 'script') for tag, _, _ in Structure(body).elements)
