import importlib.util
from pathlib import Path
import pytest
from vbt.registry import Registry
from vbt.datasets import read_manifest

@pytest.fixture
def fixture_data(tmp_path):
    spec=importlib.util.spec_from_file_location('make_demo',Path(__file__).parents[1]/'examples'/'make_demo.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.build(tmp_path/'synthetic_data')

@pytest.fixture
def registry(tmp_path,fixture_data):
    r=Registry(tmp_path/'workspace',tmp_path/'local_state')
    for ds in read_manifest(fixture_data/'manifest.json',fixture_data):r.register(ds,fixture_data)
    yield r
    r.close()
