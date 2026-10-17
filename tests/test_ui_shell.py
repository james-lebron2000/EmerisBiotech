"""Contracts for navigation, native mobile menu and trusted shell boundaries."""

from html.parser import HTMLParser

import pytest

from vbt.ui_shell import STYLE, frame


class ShellParser(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.tags = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize('path, expected', [
    ('/', '/'), ('/assets?company=US:PFE', '/assets'), ('/market/export?id=abc', '/market'),
    ('/market/progress', '/coverage'), ('/record?kind=run&id=abc', '/records'),
    ('/mapping', '/mapping'), ('/disclosures', '/disclosures'), ('/notes', '/notes'),
])
def test_navigation_has_one_active_link_per_surface(path, expected):
    parser = ShellParser(frame('<h1>Body</h1>', path))
    selected = [attrs['href'] for tag, attrs in parser.tags
                if tag == 'a' and attrs.get('aria-current') == 'page']
    assert selected == [expected, expected]  # desktop and native mobile menu
    links = [attrs.get('href') for tag, attrs in parser.tags if tag == 'a']
    assert links.count('/assets') == 2


def test_native_menu_is_collapsed_and_main_is_keyboard_target():
    parser = ShellParser(frame('<h1>Body</h1>', '/market'))
    menus = [attrs for tag, attrs in parser.tags if tag == 'details' and attrs.get('class') == 'mobile-nav']
    assert len(menus) == 1 and 'open' not in menus[0]
    assert ('main', {'id': 'main-content', 'class': 'main-content', 'tabindex': '-1'}) in parser.tags
    assert any(tag == 'a' and attrs.get('class') == 'skip-link' and attrs.get('href') == '#main-content'
               for tag, attrs in parser.tags)
    assert any(tag == 'nav' and attrs.get('aria-label') == '移动端导航' for tag, attrs in parser.tags)
    assert not any(tag in ('script', 'iframe', 'img', 'link') for tag, _ in parser.tags)
    assert '@media(max-width:900px)' in STYLE and '.sidebar{display:none}' in STYLE
    assert '.mobile-nav:not([open])>.mobile-menu{display:none}' in STYLE


@pytest.mark.parametrize('path', ['/<script>alert(1)</script>', 'https://evil.test/market', '//evil.test/notes', '/[broken', 'http://[bad'])
def test_unknown_route_is_not_interpolated(path):
    document = frame('<p>Trusted rendered body</p>', path)
    assert '<title>研究工作台 · EmerisBiotech</title>' in document
    assert '<script>' not in document and 'evil.test' not in document
    assert 'aria-current="page"' not in document
    assert '<p>Trusted rendered body</p>' in document


@pytest.mark.parametrize('refresh', ['<script>alert(1)</script>', '<meta http-equiv="refresh" content="5;url=https://evil.test">',
                                     '<meta http-equiv="refresh" content="0">', '<meta http-equiv="refresh" content="5" onload="evil()">'])
def test_refresh_is_allowlisted(refresh):
    document = frame('<h1>Body</h1>', '/jobs', refresh)
    parser = ShellParser(document)
    assert not any(tag == 'meta' and attrs.get('http-equiv') == 'refresh' for tag, attrs in parser.tags)
    assert 'evil' not in document


def test_valid_refresh_preserves_background_task_behavior():
    document = frame('<h1>Body</h1>', '/jobs', '<meta http-equiv="refresh" content="5">')
    assert '<meta http-equiv="refresh" content="5">' in document
    assert 'prefers-reduced-motion' in STYLE
    assert 'background-attachment:local,local,scroll,scroll' in STYLE
