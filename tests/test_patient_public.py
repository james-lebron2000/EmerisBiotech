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


def test_review_entry_is_english_and_does_not_present_legacy_resistance(tmp_path):
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'scripts/build_patient_site.py'),'--out',str(tmp_path)],check=True)
    html=(tmp_path/'index.html').read_text()
    assert 'Research Preview' in html and 'MNC customer value' in html
    assert 'id="resistance"' not in html and 'style="' not in html
    assert 'scenario-archive/' in html and 'cohort-lab/' in html and 'molecule-lab/' in html
    assert (tmp_path/'scenario-archive/scenarios.json').exists()
    for cutoff in (3,5,7):assert (tmp_path/f'cohort-lab/trajectories-cutoff-{cutoff}.csv').exists()
    for path in [root/'README.md',root/'web/product-review/index.html',root/'docs/PRODUCT_REVIEW_2026-10-27.md']:
        assert not re.search(r'[\u3400-\u9fff]',path.read_text())
