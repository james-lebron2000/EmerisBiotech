"""Public build uses authoritative templates rather than a second model."""
import importlib.util
import json
import re
from pathlib import Path
from vbt.patient_lab import ScenarioRequest, canonical, digest, result


def test_public_templates_match_authoritative_engine():
    script = Path(__file__).resolve().parents[1] / 'scripts/build_patient_site.py'
    spec = importlib.util.spec_from_file_location('build_patient_site', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = module.bundle()
    assert bundle['locale'] == 'en' and not bundle['clinical_use']
    for resistance in ('low', 'high'):
        for cutoff in (0, 6, 12):
            entry = bundle['templates'][resistance][str(cutoff)]
            expected = result(ScenarioRequest(resistance=resistance), cutoff)
            assert entry['canonical_result'] == canonical(expected)
            assert entry['sha256'] == digest(expected)
            assert json.loads(entry['canonical_result']) == expected
    assert not re.search(r'[\u3400-\u9fff]', json.dumps(bundle, ensure_ascii=False))


def test_new_patient_source_is_english():
    root = Path(__file__).resolve().parents[1]
    for name in ['src/vbt/patient_lab.py', 'src/vbt/patient_lab_view.py', 'docs/PATIENT_LAB.md',
                 'web/patient-lab/index.html', 'web/patient-lab/app.js']:
        assert not re.search(r'[\u3400-\u9fff]', (root / name).read_text()), name
