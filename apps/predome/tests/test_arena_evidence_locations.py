"""Optional source locations are recorded metadata, never scientific approval."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from emeris_live.arena_evidence_locations import LOCATION_KEYS, validate_locations


def test_present_only_detached_values_keep_order_null_and_empty_without_normalization():
    value = {'page_references': [10, 1, 10000], 'source_anchor': ' Table2 · 主要估计目标 ',
             'document_version': 'SAP amendment2023-09-18', 'text': 'unaltered source'}
    before = deepcopy(value)
    result = validate_locations(value)
    assert LOCATION_KEYS == {'page_references', 'source_anchor', 'document_version'}
    assert result == {key: value[key] for key in LOCATION_KEYS}
    assert value == before and result['page_references'] is not value['page_references']
    result['page_references'].append(42)
    assert value == before
    assert validate_locations({'page_references': []}) == {'page_references': []}
    assert validate_locations(dict.fromkeys(LOCATION_KEYS)) == dict.fromkeys(LOCATION_KEYS)


def test_absent_metadata_is_not_inferred_from_url_dates_explicit_pdf_page_or_private_objects():
    class NeverCopy:
        def __deepcopy__(self, memo):
            raise AssertionError('Unknown fields must not be copied')
    item = {'source_url': 'https://example.org/study/2024#page=9',
            'pdf_page_1_based': 9, 'document_date': '2023-09-18',
            'available_at': '2024-01-01', 'text': 'Page9, version2.0',
            'raw_prompt': NeverCopy(), 'scientific_review': 'claimed-approval'}
    assert validate_locations(item) == {}


@pytest.mark.parametrize('value', [None, [], 1, 'not a dict'])
def test_nonobject_has_fixed_failure_without_interpreting_input(value):
    with pytest.raises(ValueError, match='^invalid_evidence_location$'):
        validate_locations(value)


@pytest.mark.parametrize('value', [True, 1, '1', (1,), {1}, {}, [True], [False],
    [0], [-1], [10001], [1.0], ['1'], [None], [[1]], [1, 1], list(range(1, 102))])
def test_page_references_are_only_bounded_unique_positive_exact_integers(value):
    with pytest.raises(ValueError, match='^invalid_evidence_location$'):
        validate_locations({'page_references': value})


def test_page_limit_boundaries_are_preserved_not_sorted_or_reinterpreted():
    assert validate_locations({'page_references': list(range(100, 0, -1))})['page_references'] == list(range(100, 0, -1))
    assert validate_locations({'page_references': [1, 10000]}) == {'page_references': [1, 10000]}
    assert validate_locations({'source_anchor': 'x' * 1000, 'document_version': 'v' * 200}) == {
        'source_anchor': 'x' * 1000, 'document_version': 'v' * 200}


@pytest.mark.parametrize('field', ['source_anchor', 'document_version'])
@pytest.mark.parametrize('value', ['', '   ', '\t', True, 1, 1.0, [], {},
    'PRIVATE\x00TEXT', 'PRIVATE\nTEXT', 'PRIVATE\rTEXT', 'PRIVATE\x7fTEXT',
    'PRIVATE\x85TEXT', 'PRIVATE\u200bTEXT', 'PRIVATE\u202eTEXT',
    'PRIVATE\ud800TEXT', 'PRIVATE\u2028TEXT', 'PRIVATE\u2029TEXT'])
def test_blank_untyped_or_control_bearing_strings_are_not_cleaned_or_echoed(field, value):
    with pytest.raises(ValueError) as error:
        validate_locations({field: value})
    assert str(error.value) == 'invalid_evidence_location'


@pytest.mark.parametrize('field,limit', [('source_anchor', 1000), ('document_version', 200)])
def test_overlong_strings_are_rejected_instead_of_truncated(field, limit):
    with pytest.raises(ValueError, match='^invalid_evidence_location$'):
        validate_locations({field: 'x' * (limit + 1)})


def test_helper_import_is_standard_library_and_has_no_io_side_effects(tmp_path):
    app = Path(__file__).resolve().parents[1]
    program = '''import os,sys,importlib.abc
class DenyHeavy(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'httpx','fastapi','sqlite3','requests'} or fullname.startswith('emeris_live.arena_providers'):
   raise RuntimeError('Shared location validation must remain below its consumers')
sys.meta_path.insert(0,DenyHeavy())
def guard(event,args):
 if event.startswith('socket.') or event in {'sqlite3.connect','subprocess.Popen','os.system','os.mkdir','os.remove','os.rename'}:
  raise RuntimeError('Import attempted runtime activity')
 if event=='open':
  mode,flags=args[1],args[2]
  if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
   raise RuntimeError('Import attempted a write')
sys.addaudithook(guard)
sys.path.insert(0,sys.argv[1])
from emeris_live.arena_evidence_locations import validate_locations
assert validate_locations({'page_references':[]})=={'page_references':[]}
'''
    process = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(app)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.returncode == 0, process.stderr
    assert list(tmp_path.iterdir()) == []
