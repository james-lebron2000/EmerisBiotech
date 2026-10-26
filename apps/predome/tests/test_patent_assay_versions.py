"""Exercise versioned assay metadata with the existing research gates; no originals required.

The published source fixtures bind AI observations, not human scientific approval.
Actual PDF/page hashes are checked separately in the authorized local audit.
"""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from emeris_live import patent_evidence as patents


ROOT = Path(__file__).resolve().parents[1] / 'patent_data'
VERSION = ROOT / 'versions' / 'assays-20261027-v2'
NUMBERS = ('WO2019125938A1', 'WO2020240467A1')
DEFAULT_CATALOG_SHA256 = '77e20d98e8464f77cdc34e01bad72f0b8db726796cd5041476970b2ae8e19c04'


def raw_catalog(root=VERSION):
    return json.loads((root / 'catalog.json').read_text())


def raw_item(number, root=VERSION):
    return next(item for item in raw_catalog(root)['items'] if item['publication_number'] == number)


def after_review(number):
    return (datetime.fromisoformat(raw_item(number)['research_review']['reviewed_at'])
            + timedelta(seconds=1)).isoformat()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Versioned assay source tests must never acquire sources')
    monkeypatch.setattr(patents, '_http', forbidden)


@pytest.fixture
def metadata_only(tmp_path):
    """Use the shipped source contract without private PDFs, OCR or receipts."""
    shutil.copytree(VERSION / 'sources', tmp_path / 'sources')
    shutil.copyfile(VERSION / 'catalog.json', tmp_path / 'catalog.json')
    assert not any((tmp_path / name).exists() for name in ('raw', 'ocr', 'receipts'))
    return tmp_path


@pytest.mark.parametrize('number', NUMBERS)
def test_actual_versioned_metadata_passes_existing_gates_without_private_originals(number, metadata_only):
    item = raw_item(number, metadata_only)
    cutoff = after_review(number)
    assert patents.research_review_errors(item, cutoff) == []
    eligibility = patents.evidence_eligibility(item, cutoff)
    assert eligibility['eligible_for_forecast'] is True
    assert eligibility['human_review_count'] == 0
    assert eligibility['clinical_prediction_validated'] is False
    packet = patents.freeze_evidence_pack(item['drug_mapping']['candidate_name'], cutoff, metadata_only)
    assert packet['status'] == 'ready' and len(packet['items']) == 1
    assert len(packet['items'][0]['experiment_cards']) == 2
    assert all(card['eligible_for_forecast'] for card in packet['items'][0]['experiment_cards'])
    assert packet['items'][0]['research_review']['same_base_model_correlation_disclosed'] is True
    assert packet['packet_sha256'] == patents.canonical_sha256({
        key: value for key, value in packet.items() if key != 'packet_sha256'})


@pytest.mark.parametrize('number', NUMBERS)
def test_new_assays_cannot_enter_a_cutoff_before_the_new_source_review(number, metadata_only):
    item = raw_item(number, metadata_only)
    cutoff = (datetime.fromisoformat(item['research_review']['reviewed_at'])
              - timedelta(microseconds=1)).isoformat()
    assert 'ai_review_after_cutoff' in patents.research_review_errors(item, cutoff)
    packet = patents.freeze_evidence_pack(item['drug_mapping']['candidate_name'], cutoff, metadata_only)
    assert packet['status'] == 'blocked_no_eligible_patent_evidence' and packet['items'] == []
    assert 'ai_review_after_cutoff' in packet['excluded'][0]['reasons']
    # An old review remains usable only with its original one-card catalogue.
    old = patents.freeze_evidence_pack(item['drug_mapping']['candidate_name'], cutoff, ROOT)
    assert old['status'] == 'ready' and len(old['items'][0]['experiment_cards']) == 1


def test_observed_human_pk_retains_units_unknown_denominators_and_simulation_boundary():
    item = raw_item('WO2020240467A1')
    card = item['experiment_cards'][1]
    assert card['evidence_id'] == 'WO2020240467A1-example7-observed-human-pk-v1'
    assert card['section_type'] == 'clinical_study'
    assert card['observation_status'] == 'reported_observed_pk_measurements'
    assert card['sample_size']['overall_pk_analysis_subjects'] == 61
    assert card['sample_size']['overall_pk_dose_range_mg_kg'] == [.27, 10]
    assert card['sample_size']['repeated_8_mg_kg_pk_subjects'] is None
    assert card['sample_size']['n_per_reported_pk_parameter'] is None
    assert card['dose']['amount'] == 8 and card['dose']['unit'] == 'mg/kg'
    assert card['dose']['route'] == 'intravenous' and card['dose']['exposure'] == 'repeated dosing'
    assert card['dose']['quantitative_pk_cycle'] is None
    assert card['dose']['quantitative_pk_dosing_interval_days'] is None
    assert card['data_cutoff'] is None and card['comparator'] is None
    assert [(p['name'], p['value'], p['unit']) for p in card['result']['parameters']] == [
        ('AUClast', 914, 'ug*day/mL'), ('Cmax', 196, 'ug/mL'), ('terminal_half_life', 5.45, 'day')]
    assert 'not separately label' in card['result']['analyte_assignment_limit']
    assert card['pdf_page_1_based'] == 94 and card['printed_page'] == 92
    assert 129 in card['supporting_pdf_pages']
    assert card['source_discrepancies'][0]['archived_ocr'] == '196 g/mL'
    assert card['source_discrepancies'][0]['original_page_visual'] == '196 μg/mL'
    assert any('modeling' in record['reason'] and 'not the observed' in record['reason']
               for record in card['excluded_materials'])
    assert 'AVANZAR' in card['interpretation_limit']


def test_casein_assay_keeps_its_own_conditions_and_does_not_inherit_table3_parameters():
    item = raw_item('WO2019125938A1')
    original, card = item['experiment_cards']
    assert card['evidence_id'] == 'WO2019125938A1-example12-table4-casein-camp-v1'
    assert card['example_id'] == '12' and card['sequence_id'] == 'SEQ ID NO:17'
    assert card['assay_conditions']['casein_percent'] == .1
    assert card['assay_conditions']['FBS_percent'] is None
    assert original['assay_conditions']['FBS_percent'] == .25
    assert [(r['receptor'], r['mean_ec50_nM'], r['sem_nM'], r['reported_n'])
            for r in card['result']['groups']] == [
        ('GcgR', .122, .00931, 15), ('GIPR', .0529, .00394, 18), ('GLP-1R', .162, .0100, 18)]
    for key in ('concentration_response_points', 'dilution_fold', 'example_concentration_range',
                'control_concentration_range', 'peptide_exposure_minutes', 'temperature', 'assay_volume_uL'):
        assert card['dose'][key] is None
    assert card['sample_size']['independent_biological_units'] is None
    assert card['pdf_page_1_based'] == 31 and 30 in card['supporting_pdf_pages']
    assert card['result']['control_rows_as_printed'][2]['ligand_label_as_printed'] == 'gGLP-1 amide'
    assert card['result']['control_rows_as_printed'][2]['identity_status'] == 'source_label_ambiguity_not_normalized'
    assert 'not a new independent drug project' in card['interpretation_limit']


@pytest.mark.parametrize('number', NUMBERS)
def test_new_receipts_disclose_carried_identity_and_old_cards_without_claiming_new_human_review(number):
    old = raw_item(number, ROOT)
    item = raw_item(number)
    receipt = item['research_review']
    assert receipt['prior_review']['receipt_sha256'] == old['research_review']['receipt_sha256']
    assert receipt['drug_mapping']['reviewed_at'] == old['drug_mapping']['reviewed_at']
    assert receipt['drug_mapping']['reviewer'] == old['drug_mapping']['reviewer']
    assert receipt['human_review_count'] == 0 and receipt['clinical_prediction_validated'] is False
    assert all(actor['actor_type'] == 'ai' for actor in receipt['reviewers'])
    assert all('additional_experiment' in actor['role'] for actor in receipt['reviewers'])
    assert patents.experiment_content_sha256(old['experiment_cards'][0]) == patents.experiment_content_sha256(item['experiment_cards'][0])
    checks = {c['evidence_id']: c for c in receipt['experiment_card_checks']}
    assert set(checks) == {c['evidence_id'] for c in item['experiment_cards']}
    for card in item['experiment_cards']:
        assert checks[card['evidence_id']]['content_sha256'] == patents.experiment_content_sha256(card)
        assert card['source_sha256'] == item['raw_sha256']
    source_receipt = json.loads((VERSION / 'sources' / (number + '.identity-review.json')).read_text())
    assert receipt == source_receipt
    if 'experiment_cards_source' in receipt:
        selected = VERSION / 'sources' / receipt['experiment_cards_source']['filename']
        assert hashlib.sha256(selected.read_bytes()).hexdigest() == receipt['experiment_cards_source']['sha256']


@pytest.mark.parametrize('number,field', [
    ('WO2020240467A1', 'pk-number'), ('WO2020240467A1', 'pk-unit'), ('WO2020240467A1', 'pk-denominator'),
    ('WO2019125938A1', 'casein-number'), ('WO2019125938A1', 'casein-count'), ('WO2019125938A1', 'casein-condition'),
])
def test_tampered_new_assay_fails_generic_review_and_freeze_even_with_rehashed_outer_catalog(number, field, metadata_only):
    document = raw_catalog(metadata_only)
    item = next(i for i in document['items'] if i['publication_number'] == number)
    card = item['experiment_cards'][1]
    if field == 'pk-number': card['result']['parameters'][0]['value'] = 9140
    elif field == 'pk-unit': card['result']['parameters'][1]['unit'] = 'g/mL'
    elif field == 'pk-denominator': card['sample_size']['repeated_8_mg_kg_pk_subjects'] = 34
    elif field == 'casein-number': card['result']['groups'][0]['mean_ec50_nM'] = 1.22
    elif field == 'casein-count': card['result']['groups'][2]['reported_n'] = 6
    else: card['assay_conditions']['casein_percent'] = 10
    cutoff = after_review(number)
    assert 'experiment_content_not_bound_to_ai_review' in patents.research_review_errors(item, cutoff)
    assert patents.evidence_eligibility(item, cutoff)['eligible_for_forecast'] is False
    # Give the transport a valid outer digest; it still cannot grant a source check.
    document['catalog_sha256'] = patents.canonical_sha256({
        key: value for key, value in document.items() if key != 'catalog_sha256'})
    (metadata_only / 'catalog.json').write_text(json.dumps(document, ensure_ascii=False))
    packet = patents.freeze_evidence_pack(item['drug_mapping']['candidate_name'], cutoff, metadata_only)
    assert packet['status'] == 'blocked_no_eligible_patent_evidence' and packet['items'] == []
    assert 'experiment_content_not_bound_to_ai_review' in packet['excluded'][0]['reasons']


def test_versioned_reads_and_freezes_leave_the_default_catalogue_bytes_and_single_cards_unchanged():
    before = (ROOT / 'catalog.json').read_bytes()
    assert hashlib.sha256(before).hexdigest() == DEFAULT_CATALOG_SHA256
    for number in NUMBERS:
        old = raw_item(number, ROOT)
        assert len(old['experiment_cards']) == 1
        drug = old['drug_mapping']['candidate_name']
        old_packet = patents.freeze_evidence_pack(drug, after_review(number), ROOT)
        new_packet = patents.freeze_evidence_pack(drug, after_review(number), VERSION)
        assert len(old_packet['items'][0]['experiment_cards']) == 1
        assert len(new_packet['items'][0]['experiment_cards']) == 2
        assert old_packet['packet_sha256'] != new_packet['packet_sha256']
    assert (ROOT / 'catalog.json').read_bytes() == before
