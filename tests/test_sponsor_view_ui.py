"""Pure synthetic rendering checks; forms do not execute mapping decisions."""
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from vbt import sponsor_view
from vbt.market import Market


class Document(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.elements, self.details, self.forms, self.visible_text = [], [], [], []
        self._details, self._form, self._summary = [], None, False
        self.feed(body)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.elements.append((tag, attrs))
        if tag == 'details':
            detail = {'attrs': attrs, 'summary': ''}
            self.details.append(detail)
            self._details.append(detail)
        elif tag == 'summary':
            self._summary = True
        elif tag == 'form':
            self._form = {'attrs': attrs, 'fields': [], 'details': list(self._details)}
            self.forms.append(self._form)
        elif tag in ('input', 'select', 'textarea') and self._form is not None:
            self._form['fields'].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'details':
            self._details.pop()
        elif tag == 'summary':
            self._summary = False
        elif tag == 'form':
            self._form = None

    def handle_data(self, text):
        if self._summary and self._details:
            self._details[-1]['summary'] += text
        if not self._details or (len(self._details) == 1 and self._summary):
            self.visible_text.append(text)


def proposal(**extra):
    return dict(candidate_name='Synthetic Therapeutics Inc.', alias='Synthetic Therapeutics',
                candidate_id='US:SYN', match_type='normalized_exact', resolution_status='suggested',
                source_url='https://clinicaltrials.gov/study/NCT00000001', evidence_nct=['NCT00000001'],
                resolved_at='2026-10-17T01:00:00+00:00', run_id='synthetic-run',
                candidate_changed=False, id='synthetic-proposal', **extra)


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = SimpleNamespace(workspace=tmp_path / 'workspace', state=tmp_path / 'state', token='synthetic-token')
    app.market = Market(app.workspace, app.state)
    monkeypatch.setattr(sponsor_view, 'candidate_rows', lambda *args, **kwargs: ([], 0))
    monkeypatch.setattr(sponsor_view, 'proposals', lambda *args, **kwargs: [proposal()])
    monkeypatch.setattr(sponsor_view, 'resolution_runs', lambda *args, **kwargs: [
        dict(candidate_id='US:SYN', status='suggested', query='Synthetic Therapeutics', proposal_count=1,
             error='', started='2026-10-16', id='synthetic-run')])
    return app


def review_tables(app, typed=True):
    with app.market.db() as db:
        db.execute('CREATE TABLE sponsor_proposal_reviews (id TEXT,proposal_id TEXT,candidate_hash TEXT,decision TEXT,reviewer TEXT,reason TEXT,created TEXT)')
        if typed:
            db.execute('CREATE TABLE sponsor_review_evidence (review_id TEXT,reviewer_type TEXT,evidence_json TEXT,evidence_sha256 TEXT)')


def put_review(app, key, reviewer_type=None, **extra):
    values = dict(proposal_id='synthetic-proposal', candidate_hash='synthetic-hash', decision='accepted',
                  reviewer='Synthetic reviewer', reason='Synthetic mapping reason', created='2026-10-17T01:00:00+00:00')
    values.update(extra)
    with app.market.db() as db:
        db.execute('INSERT INTO sponsor_proposal_reviews VALUES (?,?,?,?,?,?,?)', (key, *values.values()))
        if reviewer_type:
            db.execute('INSERT INTO sponsor_review_evidence VALUES (?,?,?,?)', (key, reviewer_type, '{}', 'synthetic-hash'))


def test_pending_forms_and_history_are_collapsed_while_key_facts_remain_visible(app):
    review_tables(app)
    put_review(app, 'review-one', 'automated_source_review')
    body = sponsor_view.render(app, {})
    doc = Document(body)
    summaries = [detail['summary'] for detail in doc.details]
    assert '查看证据并记录决定' in summaries
    assert '近期检索与缺口 · 1 条' in summaries
    assert '最近主体核对记录 · 1 条' in summaries
    assert all('open' not in detail['attrs'] for detail in doc.details)
    visible = ''.join(doc.visible_text)
    for expected in ['Synthetic Therapeutics Inc.', 'Synthetic Therapeutics', 'US:SYN',
                     'normalized_exact', '规范名称相符，待核对', '登记来源 NCT00000001', '建议 · 待主体核对']:
        assert expected in visible
    assert '主体核对依据' not in visible and 'Synthetic mapping reason' not in visible
    assert '当前为关联建议，尚待主体核对' in body


def test_existing_post_actions_csrf_fields_and_constraints_survive_folding(app, monkeypatch):
    monkeypatch.setattr(sponsor_view, 'candidate_rows', lambda *args, **kwargs:
                        ([dict(name='Synthetic Therapeutics Inc.', id='US:SYN', market='US')], 1))
    doc = Document(sponsor_view.render(app, {}))
    actions = {form['attrs'].get('action') for form in doc.forms if form['attrs'].get('method') == 'post'}
    assert actions == {'/market/resolve-batch', '/market/resolve', '/market/proposal-review',
                       '/market/resolve-all', '/market/identity-batch', '/market/identity-schedule'}
    for form in doc.forms:
        if form['attrs'].get('method') == 'post':
            token = [attrs for _, attrs in form['fields'] if attrs.get('name') == 'token']
            assert token == [dict(type='hidden', name='token', value='synthetic-token')]
    form = next(form for form in doc.forms if form['attrs'].get('action') == '/market/proposal-review')
    assert form['details'][-1]['summary'] == '查看证据并记录决定'
    fields = {attrs['name']: attrs for _, attrs in form['fields']}
    assert set(fields) == {'token', 'proposal_id', 'reviewer', 'reason', 'decision'}
    assert fields['proposal_id']['value'] == 'synthetic-proposal'
    assert fields['reviewer']['maxlength'] == '100' and 'required' in fields['reviewer']
    assert fields['reason']['minlength'] == '10' and fields['reason']['maxlength'] == '5000'
    assert 'required' in fields['reason']
    assert {attrs['value'] for tag, attrs in doc.elements if tag == 'option'} >= {'accepted', 'rejected'}
    assert '将登记该申办方关联并排队抓取临床登记' in sponsor_view.render(app, {})


def test_changed_candidate_keeps_reason_but_cannot_submit_review(app, monkeypatch):
    changed = proposal()
    changed['candidate_changed'] = True
    monkeypatch.setattr(sponsor_view, 'proposals', lambda *args, **kwargs: [changed])
    body = sponsor_view.render(app, {})
    assert '候选识别依据已变化，需要重新检索' in ''.join(Document(body).visible_text)
    assert not any(form['attrs'].get('action') == '/market/proposal-review' for form in Document(body).forms)
    assert '查看证据并记录决定' in body and '证据批次' in body


def test_automated_declared_and_unknown_review_types_do_not_become_human_signoff(app):
    review_tables(app)
    put_review(app, 'auto', 'automated_source_review')
    put_review(app, 'declared', 'human_declared')
    put_review(app, 'missing')
    body = sponsor_view.render(app, {})
    assert '自动双源名称核对，非人工签署' in body
    assert '记录人声明的主体核对' in body
    assert '核对类型未记录' in body
    assert '人工签署：无（自动来源核对）' in body
    assert body.count('人工签署：未验证') == 2
    assert '人工签署通过' not in body


def test_legacy_review_and_date_precision_remain_distinct(app):
    review_tables(app, typed=False)
    put_review(app, 'legacy', created='2026-10-15')
    body = sponsor_view.render(app, {})
    # Additive collector initialization creates the typed table; absent legacy
    # evidence remains unknown rather than inferred to be a human attestation.
    assert '核对类型未记录' in body and '人工签署：未验证' in body
    assert 'datetime="2026-10-15" title="2026-10-15">2026-10-15</time>' in body
    assert 'datetime="2026-10-16" title="2026-10-16">2026-10-16</time>' in body
    assert 'datetime="2026-10-17T01:00:00+00:00"' in body and '2026-10-17 09:00 UTC+8' in body


def test_curated_source_review_is_explicitly_nonhuman_and_readable(app):
    review_tables(app)
    put_review(app, 'curated', 'curated_official_source_review')
    body = sponsor_view.render(app, {})
    assert '官方资料与登记名称核对，非人工签署' in body
    assert '人工签署：无（自动来源核对）' in body
    assert '查看已记录的来源依据与校验值' in body


def test_empty_pending_and_empty_histories_are_explicit(app, monkeypatch):
    monkeypatch.setattr(sponsor_view, 'proposals', lambda *args, **kwargs: [])
    monkeypatch.setattr(sponsor_view, 'resolution_runs', lambda *args, **kwargs: [])
    review_tables(app)
    body = sponsor_view.render(app, {})
    assert '暂时没有待核对建议' in body
    assert '尚未记录申办方检索' in body and '尚未记录主体核对决定' in body
    assert '近期检索与缺口 · 0 条' in body and '最近主体核对记录 · 0 条' in body
    assert all('open' not in detail['attrs'] for detail in Document(body).details)


def test_source_and_submission_values_cannot_inject_markup(app, monkeypatch):
    payload = '\"><img src=x onerror=evil()><script>evil()</script>'
    row = proposal()
    row.update(candidate_name=payload, alias=payload, candidate_id=payload, match_type=payload,
               source_url='javascript:evil()', evidence_nct=[payload], run_id=payload, id=payload,
               resolved_at=payload, resolution_status=payload)
    monkeypatch.setattr(sponsor_view, 'proposals', lambda *args, **kwargs: [row])
    app.token = payload
    review_tables(app)
    put_review(app, 'evil', 'human_declared', reviewer=payload, reason=payload, created=payload)
    body = sponsor_view.render(app, {'q': payload})
    doc = Document(body)
    assert '&lt;img' in body and '&lt;script&gt;' in body
    assert not any(tag in ('img', 'script') for tag, _ in doc.elements)
    assert not any(key.startswith('on') for _, attrs in doc.elements for key in attrs)
    assert not any(attrs.get('href', '').startswith('javascript:') for _, attrs in doc.elements)
    form = next(form for form in doc.forms if form['attrs'].get('action') == '/market/proposal-review')
    fields = {attrs['name']: attrs for _, attrs in form['fields']}
    assert fields['token']['value'] == payload and fields['proposal_id']['value'] == payload


def test_stale_name_input_hides_acceptance_and_retains_frozen_seed_origin(app, monkeypatch):
    import json
    row = proposal(query_input_stale=True, stale_reason='Lookup input changed', query_used='gan and lee pharmaceuticals',
        query_evidence_json=json.dumps({'origin':'verified_exchange_english_name',
            'name_original':'Gan & Lee Pharmaceuticals.', 'directory_evidence':{
                'source':'SH','source_run_id':'synthetic-source-run','receipt_id':'synthetic-receipt','raw_sha256':'a'*64}}))
    monkeypatch.setattr(sponsor_view,'proposals',lambda *args,**kwargs:[row])
    body=sponsor_view.render(app,{})
    assert '查询名称或其来源依据已变化或暂不可验证，需要重新检索' in body
    assert '交易所目录英文全称（已核对原始归档）' in body
    assert 'Gan &amp; Lee Pharmaceuticals.' in body and 'synthetic-receipt' in body
    assert '英文全称只用于检索，不确认申办方、母子公司关系或药物权益' in body
    assert not any(form['attrs'].get('action')=='/market/proposal-review' for form in Document(body).forms)


def test_query_evidence_strings_are_text_not_markup_and_missing_history_stays_unknown(app,monkeypatch):
    import json
    row=proposal(query_used='synthetic',query_evidence_json=json.dumps({
        'origin':'verified_exchange_english_name','name_original':'<script>evil()</script>',
        'directory_evidence':{'receipt_id':'<img src=x onerror=evil()>'}}))
    monkeypatch.setattr(sponsor_view,'proposals',lambda *args,**kwargs:[row])
    body=sponsor_view.render(app,{})
    assert '&lt;script&gt;' in body and '&lt;img' in body
    assert not any(tag in ('img','script') for tag,_ in Document(body).elements)
    assert '查询种子来源与原文：历史记录未保存' in body


def test_candidate_cards_request_one_batch_context_not_individual_receipts(app,monkeypatch):
    rows=[dict(name='合成公司'+str(i),id='A:'+str(i),market='A') for i in range(2)]
    monkeypatch.setattr(sponsor_view,'candidate_rows',lambda *args,**kwargs:(rows,2))
    calls=[]
    def contexts(market,candidates):
        calls.append(candidates)
        return {row['id']:{'query':'synthetic pharma','origin':'verified_exchange_english_name',
            'name_original':'Synthetic Pharma Co., Ltd.','directory_evidence':{}} for row in candidates}
    monkeypatch.setattr(sponsor_view,'candidate_query_contexts',contexts)
    body=sponsor_view.render(app,{})
    assert calls==[rows] and body.count('名称原文：Synthetic Pharma Co., Ltd.')==2
