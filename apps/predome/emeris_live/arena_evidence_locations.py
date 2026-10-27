"""Closed optional evidence-location metadata; standard library, no I/O.

Page references preserve recorded source numbering. They do not establish a
universal PDF one-based scheme, and are never inferred from URLs or anchors.
An existing explicit pdf_page_1_based field retains its separate original
meaning. Locations/versions do not verify a source, scientific review or a
historical availability date.
"""
from copy import deepcopy
import unicodedata


FIELDS = ('page_references', 'source_anchor', 'document_version')
LOCATION_KEYS = frozenset(FIELDS)


def validate_locations(item):
    """Return detached copies of only present fields, preserving null/empty lists.

    Unknown keys are ignored, not recursively copied or interpreted. Strings
    retain their exact recorded content; blank-only and control/format/surrogate
    characters or line separators are rejected rather than normalized.
    """
    if type(item) is not dict:
        raise ValueError('invalid_evidence_location')
    result = {}
    for field in FIELDS:
        if field not in item:
            continue
        value = item[field]
        if value is None:
            result[field] = None
            continue
        if field == 'page_references':
            if (type(value) is not list or len(value) > 100
                    or any(type(page) is not int or not 1 <= page <= 10000 for page in value)
                    or len(set(value)) != len(value)):
                raise ValueError('invalid_evidence_location')
        else:
            maximum = 1000 if field == 'source_anchor' else 200
            if (type(value) is not str or not value.strip() or len(value) > maximum
                    or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') or char in '\u2028\u2029' for char in value)):
                raise ValueError('invalid_evidence_location')
        result[field] = deepcopy(value)
    return result
