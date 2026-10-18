"""Synthetic HK query-source rendering, without accepting a sponsor association."""
import json
from html.parser import HTMLParser

import pytest

from vbt.hk_issuer_names import SOUTHBOUND_URL
from vbt.sponsor_view import _query_details


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def context(**changes):
    proof = dict(receipt_id='a' * 32, english_name='SYNTHETIC HK Pharma Ltd.',
                 content_as_of='2026-10-16', retrieved_at='2026-10-17T01:23:45+00:00',
                 source_url=SOUTHBOUND_URL, source_locator='#mutualmarket-result tbody tr[2] td.col-stock-name',
                 raw_sha256='b' * 64, scope='exact_hk_security_to_official_english_name_query_seed_only',
                 registry_alias_proven=False, human_sign_off=False, drug_ownership_verified=False,
                 drug_developer_status_verified=False, directory_date_status='future_dated')
    proof.update(changes)
    return dict(origin='verified_hk_official_name', name_original='SYNTHETIC HK Pharma Ltd.', hk_name_evidence=proof)


@pytest.mark.parametrize('encoded', [False, True])
def test_saved_hk_query_seed_has_explicit_source_original_dates_locator_and_uuid(encoded):
    value = context()
    body = _query_details(json.dumps(value) if encoded else value)
    assert 'HKEX 官方英文名称（检索依据）' in body and '来源类型未识别' not in body
    for expected in ('名称凭据 UUID', 'a' * 32, 'SYNTHETIC HK Pharma Ltd.', '来源内容日期',
                     'datetime="2026-10-16"', '原件取回时间', 'datetime="2026-10-17T01:23:45+00:00"',
                     '2026-10-17 09:23 UTC+8', '#mutualmarket-result tbody tr[2] td.col-stock-name', 'b' * 64):
        assert expected in body
    links = [attrs for tag, attrs in Page(body).tags if tag == 'a']
    assert len(links) == 1 and links[0]['href'] == SOUTHBOUND_URL
    assert links[0]['target'] == '_blank' and links[0]['rel'] == 'noopener noreferrer'
    assert 'query_seed_only：仅用作检索种子' in body and '登记申办方别名尚未证明' in body
    assert '互联互通持股子集' in body and '不能作为全市场药企目录' in body
    assert '未来日期异常' in body and '不能解除该异常或证明申办方关联' in body
    assert '本视图未重新验真' in body
    assert all('open' not in attrs for tag, attrs in Page(body).tags if tag == 'details')
    assert not any(tag in ('form', 'button') for tag, _ in Page(body).tags)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk',
    'https://www3.hkexnews.hk.evil.invalid/sdw/search/mutualmarket.aspx?t=hk',
    'https://user:password@www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk',
    'https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk&redirect=evil',
    'https://example.invalid/source', None])
def test_hk_source_url_requires_exact_existing_official_https_source(url):
    body = _query_details(context(source_url=url))
    assert not any(tag == 'a' for tag, _ in Page(body).tags)
    assert 'HKEX 来源链接尚未核实' in body
    assert 'user:password@' not in body and 'javascript:' not in body


def test_hk_source_text_is_escaped_without_fabricating_alias_or_review_proof():
    payload = '<img src=x onerror=evil()><script>evil()</script>'
    value = context(receipt_id=payload, english_name=payload, content_as_of=payload,
                    retrieved_at=payload, source_locator=payload, raw_sha256=payload, scope=payload,
                    registry_alias_proven=True, human_sign_off=True, drug_ownership_verified=True)
    value['name_original'] = payload
    body = _query_details(value)
    assert payload not in body and '&lt;img' in body and '&lt;script&gt;' in body
    assert not any(tag in ('img', 'script') for tag, _ in Page(body).tags)
    assert not any(key.startswith('on') for _, attrs in Page(body).tags for key in attrs)
    assert '登记申办方别名尚未证明' in body
    assert '已通过人工签署' not in body and '已证明药物权益' not in body


@pytest.mark.parametrize('proof', [None, [], 'missing'])
def test_missing_hk_proof_is_explicitly_unknown(proof):
    value = context()
    value['hk_name_evidence'] = proof
    body = _query_details(value)
    assert 'HKEX 名称凭据未保存或格式尚未核实' in body
    assert '不能据此确认登记申办方别名' in body
    assert not any(tag == 'a' for tag, _ in Page(body).tags)


def test_existing_a_share_and_unrecorded_query_sources_keep_their_prior_meaning():
    body = _query_details({'origin': 'verified_exchange_english_name', 'name_original': 'SYNTHETIC A Pharma',
                           'directory_evidence': {'source': 'SH', 'receipt_id': 'c' * 64}})
    assert '交易所目录英文全称（已核对原始归档）' in body and 'c' * 64 in body
    assert 'HKEX' not in body
    assert '历史记录未保存' in _query_details('{}')
