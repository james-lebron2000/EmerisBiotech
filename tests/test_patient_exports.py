import copy
import csv
import io
import json
import subprocess
import sys
from pathlib import Path
import pytest
from vbt.cohort_lab import cohort_result
from vbt.molecule_lab import simulate_molecule
from vbt.patient_lab import canonical, digest
from vbt.patient_exports import cohort_csv, strict_json, verify_export


def export(cohort=True):
    payload=cohort_result(3) if cohort else simulate_molecule('P001','fluorouracil',3)
    metadata={'run_id':'8c7e4366-e077-4bf5-8a31-8471241e5a90','created_at':'2026-10-27T05:00:00.000Z'}
    result={'canonical_template':canonical(payload),'template_sha256':digest(payload),'result':copy.deepcopy(payload),
            'checksum_scope':'canonical_template UTF-8 only; metadata is not signed'}
    if cohort:result.update(metadata,view={'assay_day':6,'population':'paired','highlight_patient':'P003'})
    else:result['record']={**metadata,'patient':'P001','molecule':'fluorouracil','cutoff':'3','template_sha256':digest(payload)}
    return result


@pytest.mark.parametrize('cohort',[True,False])
def test_valid_patient_exports_independently_reproduce(cohort):
    envelope=export(cohort)
    assert verify_export(envelope)==envelope['result']
    # JSON.stringify uses integers for integral floats; this is legitimate JSON.
    envelope['result']=strict_json(json.dumps(envelope['result']).replace('100.0','100'))
    assert verify_export(envelope)['clinical_use'] is False


@pytest.mark.parametrize('mutation',[
    lambda e:e.update(template_sha256='0'*64),
    lambda e:e['result'].update(clinical_use=True),
    lambda e:e['result'].update(clinical_use=0),
    lambda e:e['view'].update(assay_day=7),
    lambda e:e['view'].update(assay_day=True),
    lambda e:e['view'].update(population='randomized'),
    lambda e:e['view'].update(highlight_patient='P999'),
    lambda e:e.update(run_id='not-an-id'),
    lambda e:e.update(created_at='2026-10-27'),
])
def test_cohort_tampering_or_invalid_selection_rejected(mutation):
    envelope=export()
    mutation(envelope)
    with pytest.raises(ValueError):verify_export(envelope)


def test_rehashed_fabricated_data_is_not_approved_template():
    envelope=export()
    envelope['result']['patients'][0]['patient']['source_kind']='real'
    envelope['canonical_template']=canonical(envelope['result'])
    envelope['template_sha256']=digest(envelope['result'])
    with pytest.raises(ValueError,match='not an approved synthetic fixture'):verify_export(envelope)


def test_single_patient_saved_selection_bound_to_template():
    envelope=export(False)
    envelope['record']['patient']='P002'
    with pytest.raises(ValueError):verify_export(envelope)
    envelope=export(False)
    envelope['record']['template_sha256']='0'*64
    with pytest.raises(ValueError):verify_export(envelope)


@pytest.mark.parametrize('raw',['{"a":1,"a":2}','{"value":NaN}','{"value":Infinity}','not-json'])
def test_malformed_json_fails(raw):
    with pytest.raises(ValueError):strict_json(raw)


def test_csv_has_patient_time_molecule_units_and_blank_missing_values():
    payload=cohort_result(3)
    rows=list(csv.DictReader(io.StringIO(cohort_csv(payload))))
    assert len(rows)==24*2*7
    assert len({r['patient_id'] for r in rows})==24
    missing=[r for r in rows if r['status']=='not_estimable']
    assert len(missing)==2*7
    assert all(r['viability_percent']=='' and r['paired_summary_included']=='false' for r in missing)
    for row in rows:
        source=next(p for p in payload['patients'] if p['patient']['patient_id']==row['patient_id'])['responses'][row['molecule_id']]
        if source['status']=='estimable':
            point=source['trajectory'][int(row['assay_day'])]
            assert float(row['viability_percent'])==point['viability_percent']
            assert row['trajectory_kind']==point['kind']
    modified=copy.deepcopy(payload);modified['total_patients']=100
    with pytest.raises(ValueError):cohort_csv(modified)


def test_offline_cli_csv_and_failures(tmp_path):
    source=tmp_path/'export.json';source.write_text(json.dumps(export()))
    csv_path=tmp_path/'cohort.csv'
    command=[sys.executable,'-m','vbt.patient_exports',str(source)]
    valid=subprocess.run(command+['--csv',str(csv_path)],text=True,capture_output=True)
    assert valid.returncode==0
    receipt=json.loads(valid.stdout)
    assert receipt['record_integrity']=='verified' and receipt['scientific_review']=='not_performed'
    previous=csv_path.read_bytes()
    repeat=subprocess.run(command+['--csv',str(csv_path)],capture_output=True)
    assert repeat.returncode!=0 and csv_path.read_bytes()==previous
    source.write_text('{"a":NaN}')
    assert subprocess.run(command,capture_output=True).returncode!=0
