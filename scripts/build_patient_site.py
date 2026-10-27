"""Build the English public showcase from the authoritative synthetic engine."""
import argparse
import json
import shutil
from pathlib import Path
from vbt.cohort_lab import public_cohort_bundle, cohort_result
from vbt.patient_exports import cohort_csv
from vbt.molecule_lab import public_bundle
from vbt.patient_lab import PLANS, ScenarioRequest, canonical, digest, result


def bundle():
    templates = {}
    for resistance in ('low', 'high'):
        templates[resistance] = {}
        for cutoff in (0, 6, 12):
            payload = result(ScenarioRequest(resistance=resistance), cutoff)
            templates[resistance][str(cutoff)] = {'canonical_result': canonical(payload), 'sha256': digest(payload)}
    return {'schema': 'patient-public-bundle-v1', 'locale': 'en', 'plans': PLANS,
            'templates': templates, 'storage': 'visitor_browser_only', 'clinical_use': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'web' / 'patient-lab'
    args.out.mkdir(parents=True, exist_ok=True)
    archive_out = args.out / 'scenario-archive'
    archive_out.mkdir(exist_ok=True)
    for name in ('index.html', 'app.js', 'styles.css'):
        shutil.copyfile(source / name, archive_out / name)
    (archive_out / 'scenarios.json').write_text(json.dumps(bundle(), ensure_ascii=False, separators=(',', ':')) + '\n')
    shutil.copyfile(source.parent / 'product-review' / 'index.html', args.out / 'index.html')
    shutil.copyfile(source / 'styles.css', args.out / 'styles.css')
    shutil.copyfile(source.parent / 'product-review' / 'review.css', args.out / 'review.css')
    molecule_out = args.out / 'molecule-lab'
    molecule_out.mkdir(exist_ok=True)
    molecule_source = source.parent / 'molecule-lab'
    for name in ('index.html', 'app.js'):
        shutil.copyfile(molecule_source / name, molecule_out / name)
    shutil.copyfile(source / 'styles.css', molecule_out / 'styles.css')
    (molecule_out / 'scenarios.json').write_text(json.dumps(public_bundle(), ensure_ascii=False, separators=(',', ':')) + '\n')
    cohort_out = args.out / 'cohort-lab'
    cohort_out.mkdir(exist_ok=True)
    for name in ('index.html', 'app.js', 'cohort.css'):
        shutil.copyfile(source.parent / 'cohort-lab' / name, cohort_out / name)
    shutil.copyfile(source / 'styles.css', cohort_out / 'styles.css')
    (cohort_out / 'scenarios.json').write_text(json.dumps(public_cohort_bundle(), ensure_ascii=False, separators=(',', ':')) + '\n')
    for cutoff in (3, 5, 7):
        (cohort_out / f'trajectories-cutoff-{cutoff}.csv').write_text(cohort_csv(cohort_result(cutoff)), encoding='utf-8')
    print(f'Built English patient showcase: {args.out}')


if __name__ == '__main__':
    main()
