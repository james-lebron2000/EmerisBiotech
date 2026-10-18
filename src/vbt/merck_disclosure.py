"""Replay the explicit Merck HTML program table, retaining date conflicts.

The mobile and desktop representations must agree. No JavaScript is executed,
and no phase, indication, target, ownership or effective date is inferred from
an asset name, a page modification timestamp or the retrieval time.
"""
from collections import Counter
from datetime import date
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .disclosures import ParseBlocked

VERSION = 'merck_dual_table_v1'
PHASES = {'phase-2': 'Phase 2', 'phase-3': 'Phase 3', 'under-review': 'Under Review'}
MONTHS = {name: index for index, name in enumerate(
    ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'), 1)}
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'))
BLOCK = frozenset(('p', 'div', 'section', 'header', 'footer', 'h1', 'h2', 'h3',
                   'h4', 'h5', 'h6', 'table', 'thead', 'tbody', 'tr', 'td', 'th',
                   'li', 'ul', 'ol', 'br'))


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children, self.closed = [], tag in VOID

    def attr(self, name, default=''):
        values = [value for key, value in self.attrs if key == name]
        if len(values) > 1:
            raise ParseBlocked('Duplicate structured table attribute: ' + name)
        return values[0] if values else default

    def has(self, token):
        return token in (self.attr('class') or '').split()

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def text(self, skip_classes=(), visible_only=False):
        # Script/style text cannot supply an asset, a phase or an effective date.
        if (self.tag in ('script', 'style') or any(self.has(token) for token in skip_classes)
                or visible_only and _hidden(self)):
            return ''
        return re.sub(r'\s+', ' ', ''.join(
            child.text(skip_classes, visible_only) + (' ' if child.tag in BLOCK else '')
            if isinstance(child, Node) else child for child in self.children)).strip()


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('document')
        self.stack, self.nodes = [self.root], 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 100_000 or len(self.stack) > 120:
            raise ParseBlocked('Official HTML exceeds bounded table complexity')
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].closed = True
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def _one(nodes, description):
    values = list(nodes)
    if len(values) != 1:
        raise ParseBlocked('Expected exactly one ' + description)
    return values[0]


def _required(value, description):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ParseBlocked('Missing or oversized ' + description)
    return value.strip()


def _hidden(node):
    return (any(key == 'hidden' for key, _ in node.attrs)
            or node.attr('aria-hidden') == 'true'
            or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', node.attr('style') or '', re.I)))


def _visible(node):
    while node is not None:
        if _hidden(node):
            raise ParseBlocked('Structured pipeline section is explicitly hidden')
        node = node.parent


def _indications(container, tag):
    rows = [node for node in container.walk() if node.tag == tag and node.has('pipeline-program-indication')]
    if not rows or len(rows) > 1000:
        raise ParseBlocked('Missing or oversized indication table')
    result = []
    for row in rows:
        _visible(row)
        if not row.closed:
            raise ParseBlocked('Unclosed indication row')
        phase = row.attr('data-phase-slug')
        if phase not in PHASES:
            raise ParseBlocked('Unknown indication phase; no highest-phase inference')
        main = row
        if tag == 'tr':
            cells = [node for node in row.children if isinstance(node, Node) and node.tag == 'td']
            if len(cells) != 2:
                raise ParseBlocked('Desktop indication requires content and phase cells')
            main = cells[0]
        title_node = _one((node for node in main.walk() if node.has('pipeline-program-indication-title')),
                          'indication title')
        _visible(title_node)
        title = title_node.text(visible_only=True)
        title = _required(title, 'indication title')
        content = _one((node for node in main.walk() if node.has('pipeline-program-indication-content')),
                       'indication content')
        _visible(content)
        status_node = _one((node for node in row.walk() if node.has('pipeline-phase-bars' if tag != 'tr' else 'phase-bars-table-data')),
                           'phase representation')
        _visible(status_node)
        status = status_node.text(visible_only=True)
        if phase == 'under-review' and not re.fullmatch(r'under\s*review\s*\([^()]{1,100}\)', status, re.I):
            raise ParseBlocked('Under-review jurisdiction is missing or changed')
        if phase != 'under-review' and status and not re.fullmatch(r'\([^()]{1,100}\)', status):
            raise ParseBlocked('Unexpected phase annotation')
        references = []
        for link in (node for node in content.walk() if node.tag == 'a'):
            label, href = link.text(), link.attr('href')
            try:
                parsed = urlsplit(href)
            except ValueError:
                raise ParseBlocked('Malformed source reference') from None
            if re.fullmatch(r'NCT[0-9]{8}', label):
                _visible(link)
                if (parsed.scheme != 'https' or parsed.netloc not in ('clinicaltrials.gov', 'www.clinicaltrials.gov', 'classic.clinicaltrials.gov')
                        or parsed.path not in ('/study/' + label, '/ct2/show/' + label) or parsed.fragment):
                    raise ParseBlocked('Displayed NCT is not bound to its official registry identifier path')
                references.append(label)
        result.append({'indication': title, 'phase_slug': phase, 'display_phase': status,
                       'nct_ids': sorted(set(references)), 'source_reference_text': content.text(visible_only=True)})
    return result


def _dates(document):
    visible = document.root.text(visible_only=True)
    pattern = re.compile(r'(?:Updated|current as of|pipeline as of)\s+('
                         + '|'.join(MONTHS) + r')\s+(\d{1,2}),\s*(\d{4})', re.I)
    assertions = []
    for match in pattern.finditer(visible):
        month = next(index for name, index in MONTHS.items() if name.casefold() == match[1].casefold())
        try:
            value = date(int(match[3]), month, int(match[2])).isoformat()
        except ValueError:
            raise ParseBlocked('Invalid explicit pipeline date') from None
        assertions.append({'date': value, 'source_text': match[0]})
    values = sorted({row['date'] for row in assertions})
    return {'published_at': None, 'as_of': values[0] if len(values) == 1 else None,
            'as_of_precision': 'day' if len(values) == 1 else 'conflicting_source_dates' if values else 'unknown',
            'content_date_status': 'explicit' if len(values) == 1 else 'conflicting_explicit_dates' if values else 'not_stated',
            'source_date_assertions': assertions, 'source_date_values': values,
            'date_locator': ' / '.join(row['source_text'] for row in assertions) or None}


def parse_merck(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 20_000_000:
        raise ParseBlocked('Empty or oversized official HTML')
    document = Document()
    document.feed(raw.decode('utf-8', errors='strict'))
    document.close()
    section = _one((node for node in document.root.walk() if node.tag == 'section'
                    and node.has('pipeline-programs')), 'pipeline-programs section')
    _visible(section)
    if not section.closed:
        raise ParseBlocked('Unclosed pipeline-programs section')
    for heading in ('Molecule Name', 'Therapeutic Area', 'Potential Indications', 'Modality'):
        if heading not in section.text():
            raise ParseBlocked('Official pipeline table header changed: ' + heading)
    programs = [node for node in section.walk() if node.tag == 'tr' and node.has('pipeline-program')]
    if not programs or len(programs) > 1000:
        raise ParseBlocked('No bounded program table; empty extraction cannot be published')
    rows, identifiers, annotation_differences = [], set(), 0
    for program in programs:
        if not program.closed:
            raise ParseBlocked('Unclosed program row')
        _visible(program)
        identifier = _required(program.attr('id'), 'source program ID')
        if not re.fullmatch(r'pipeline-program-[0-9]+', identifier) or identifier in identifiers:
            raise ParseBlocked('Invalid or duplicate source program ID')
        identifiers.add(identifier)
        filter_name = _required(program.attr('data-name'), 'source filter key')
        code = program.attr('data-code')
        if not isinstance(code, str) or len(code) > 4000:
            raise ParseBlocked('Invalid original code attribute')
        code = code.strip() or None
        direct = [node for node in program.children if isinstance(node, Node) and node.tag == 'td']
        mobile = _one((node for node in direct if node.has('pipeline-mobile-table-data')), 'mobile representation')
        desktop = _one((node for node in direct if node.has('pipeline-program-indications')), 'desktop indication cell')
        area_node = _one((node for node in direct if node.has('pipeline-program-t-area')), 'therapeutic area')
        modality_node = _one((node for node in direct if node.has('pipeline-program-modality')), 'modality')
        for node in (mobile, desktop, area_node, modality_node):_visible(node)
        therapeutic = area_node.text(visible_only=True)
        modality = modality_node.text(visible_only=True) or None
        for node in program.walk():
            if node.tag == 'h4' and node.has('pipeline-program-name'):_visible(node)
        names = [node.text(('pipeline-program-code', 'pipeline-program-indicators'), visible_only=True)
                 for node in program.walk() if node.tag == 'h4' and node.has('pipeline-program-name')]
        if len(names) != 2 or not names[0] or names[0] != names[1]:
            raise ParseBlocked('Visible mobile/desktop program names disagree')
        name = _required(names[1], 'visible original program name')
        left, right = _indications(mobile, 'div'), _indications(desktop, 'tr')
        compare = lambda records: [{**row, 'display_phase': re.sub(r'\s+', '', row['display_phase']).casefold()
                                   if row['phase_slug'] == 'under-review' else ''} for row in records]
        if compare(left) != compare(right):
            raise ParseBlocked('Mobile and desktop indication, phase or source references disagree')
        if Counter(_required(program.attr('data-phase'), 'program phase list').split('|')) != Counter(row['phase_slug'] for row in right):
            raise ParseBlocked('Program phase aggregation differs from explicit indication rows')
        for index, indication in enumerate(right):
            if indication['phase_slug'] != 'under-review' and left[index]['display_phase'] != indication['display_phase']:
                annotation_differences += 1
            rows.append({'drug_code': code, 'drug_name': name, 'target': None, 'modality': modality,
                         'therapeutic_area': _required(therapeutic, 'therapeutic area'),
                         'indication': indication['indication'], 'phase': indication['display_phase'] if indication['phase_slug'] == 'under-review' else PHASES[indication['phase_slug']] + (' ' + indication['display_phase'] if indication['display_phase'] else ''),
                         'source_locator': 'section.pipeline-programs #' + identifier + ' desktop indication[' + str(index) + ']',
                         'source_program_id': identifier, 'source_indication_index': index,
                         'source_filter_name': filter_name,
                         'source_phase_slug': indication['phase_slug'],
                         'source_phase_display_mobile': left[index]['display_phase'],
                         'source_phase_display_desktop': indication['display_phase'],
                         'source_nct_ids': indication['nct_ids'],
                         'source_nct_status': 'disclosure_links_only_not_independent_registry_verification',
                         'source_reference_text': indication['source_reference_text'],
                         'missing_fields': [key for key, value in [('drug_code', code), ('target', None), ('modality', modality)] if value is None]})
    return rows, {**_dates(document), 'program_count': len(programs),
                  'representation_check': 'mobile_desktop_names_indications_phase_slugs_registry_ids_agree',
                  'representation_annotation_difference_count': annotation_differences,
                  'full_pipeline_verified': False,
                  'scope': 'Explicit late-stage HTML program-indication-phase rows only. Phase 1 is not shown; Phase 2 represents selected mechanisms. Not all assets, rights or complete current portfolio.'}
