"""Actual comparison/build/freeze implementations; no acquisition or model calls."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from emeris_live import patent_evidence as patents
from emeris_live.patent_identity import compare_peptides

ROOT = Path(__file__).parents[1]
NUMBER = 'WO2019125938A1'
SOURCE = ROOT / 'patent_data/sources'


def review():
    return json.loads((SOURCE / (NUMBER + '.identity-review.json')).read_text())


def test_actual_modified_structure_matches_and_near_neighbors_do_not():
    analysis = review()['identity_analysis']
    assert compare_peptides(analysis['reference'], analysis['candidate'])['matches'] is True
    example11 = deepcopy(analysis['candidate'])
    example11['conjugations'][0]['linker_from_peptide'].insert(0, 'AEEA')
    assert compare_peptides(analysis['reference'], example11)['matches'] is False
    example1 = deepcopy(example11)
    for position, token in ((20, '2-methyl-L-Lys'), (28, 'L-Ala'), (29, 'Aib')):
        example1['residues'][position - 1] = token
    result = compare_peptides(analysis['reference'], example1)
    assert result['differing_fields'] == ['residues', 'conjugations']


@pytest.mark.parametrize('change', ['missing_residue', 'stereo', 'c_terminus', 'n_terminus', 'lipid_length'])
def test_backbone_similarity_cannot_hide_changed_chemistry(change):
    reference = review()['identity_analysis']['reference']
    candidate = deepcopy(reference)
    if change == 'missing_residue':candidate['residues'].pop()
    elif change == 'stereo':candidate['residues'][16] = 'D-Lys'
    elif change == 'c_terminus':candidate['c_terminus'] = 'carboxyl'
    elif change == 'n_terminus':candidate['n_terminus'] = 'acetyl'
    elif change == 'lipid_length':candidate['conjugations'][0]['lipid_carbons'] = 18
    assert compare_peptides(reference, candidate)['matches'] is False


@pytest.mark.parametrize('fault', ['omitted', 'unknown_residue', 'unknown_terminus', 'unknown_connection',
    'bool_position', 'float_lipid', 'missing_free_carboxyl', 'extra', 'duplicate_conjugation'])
def test_unknown_incomplete_or_unsupported_chemistry_cannot_match(fault):
    reference = review()['identity_analysis']['reference']
    candidate = deepcopy(reference)
    if fault == 'omitted':candidate.pop('conjugations')
    elif fault == 'unknown_residue':candidate['residues'][0] = 'unknown'
    elif fault == 'unknown_terminus':candidate['c_terminus'] = None
    elif fault == 'unknown_connection':candidate['conjugations'][0]['linker_from_peptide'] = ['Glu']
    elif fault == 'bool_position':candidate['conjugations'][0]['position'] = True
    elif fault == 'float_lipid':candidate['conjugations'][0]['lipid_carbons'] = 20.0
    elif fault == 'missing_free_carboxyl':candidate['conjugations'][0].pop('glutamate_alpha_carboxyl')
    elif fault == 'extra':candidate['identity_verified'] = True
    elif fault == 'duplicate_conjugation':candidate['conjugations'].append(deepcopy(candidate['conjugations'][0]))
    with pytest.raises(ValueError):compare_peptides(reference, candidate)


def test_actual_new_card_freezes_only_after_review_and_excludes_old_mouse_card():
    receipt = review()
    old = patents.freeze_evidence_pack('retatrutide', '2026-10-23')
    assert old['status'] == 'blocked_no_eligible_patent_evidence'
    assert 'ai_review_after_cutoff' in old['excluded'][0]['reasons']
    cutoff = (datetime.fromisoformat(receipt['reviewed_at']) + timedelta(seconds=1)).isoformat()
    packet = patents.freeze_evidence_pack('retatrutide', cutoff)
    assert packet['status'] == 'ready' and len(packet['items']) == 1
    item = packet['items'][0]
    assert item['eligibility']['human_review_count'] == 0
    assert item['eligibility']['clinical_prediction_validated'] is False
    assert item['research_review']['same_base_model_correlation_disclosed'] is True
    assert [c['example_id'] for c in item['experiment_cards']] == ['12']
    assert 'excluded_experiment_cards' not in item and 'candidate_experiment_cards' not in item
    original = patents.get_patent(NUMBER, cutoff)
    assert original['excluded_experiment_cards'][0]['evidence_id'] == NUMBER + '-table7-mouse-weight'
    card = item['experiment_cards'][0]
    assert card['result']['groups'][2]['sem_nM'] == 1.41
    assert card['dose']['example_range']['minimum_unit'] == 'fM'
    assert card['source_discrepancies'][1]['status'] == 'unresolved_source_relationship'
    assert packet['packet_sha256'] == patents.canonical_sha256({k: v for k, v in packet.items() if k != 'packet_sha256'})


@pytest.mark.parametrize('fault', ['sequence', 'linker', 'wrong_example', 'wrong_sequence_id',
    'source', 'missing_reference', 'decision', 'numeric', 'single_ai', 'human_claim',
    'downgrade_labels', 'downgrade_schema_and_remove_structure'])
def test_structure_review_cannot_authorize_a_different_or_unreviewed_card(fault):
    item = patents.get_patent(NUMBER, '2026-10-28')
    receipt = item['research_review']
    if fault == 'sequence':receipt['identity_analysis']['candidate']['residues'][19] = '2-methyl-L-Lys'
    elif fault == 'linker':receipt['identity_analysis']['candidate']['conjugations'][0]['linker_from_peptide'].insert(0, 'AEEA')
    elif fault == 'wrong_example':item['experiment_cards'][0]['example_id'] = '1'
    elif fault == 'wrong_sequence_id':item['experiment_cards'][0]['sequence_id'] = 'SEQ ID NO:7'
    elif fault == 'source':receipt['identity_analysis']['reference_source_sha256'] = 'a' * 64
    elif fault == 'missing_reference':receipt['identity_analysis'].pop('reference')
    elif fault == 'decision':receipt['identity_decision'] = 'supported_by_explicit_development_code_and_trial_id'
    elif fault == 'numeric':item['experiment_cards'][0]['result']['groups'][2]['sem_nM'] = 141
    elif fault == 'single_ai':receipt['reviewers'].pop()
    elif fault == 'human_claim':receipt['human_review_count'] = 2
    elif fault in ('downgrade_labels', 'downgrade_schema_and_remove_structure'):
        receipt['identity_analysis']['candidate']['residues'][19] = '2-methyl-L-Lys'
        item['drug_mapping']['status'] = receipt['drug_mapping']['status'] = 'ai_verified_explicit_code_and_trial_identity'
        receipt['identity_decision'] = 'supported_by_explicit_development_code_and_trial_id'
        if fault == 'downgrade_schema_and_remove_structure':
            receipt['schema_version'] = 'predome-patent-ai-source-review-1.0'
            receipt.pop('identity_analysis');receipt.pop('experiment_cards_source')
    # Rehash the modified envelope: a matching JSON digest alone is insufficient.
    receipt['receipt_sha256'] = patents.canonical_sha256({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
    assert patents.evidence_eligibility(item, '2026-10-28')['eligible_for_forecast'] is False


@pytest.fixture
def build_root(tmp_path):
    """Synthetic OCR inventory exercises the real builder; not scientific OCR QA."""
    (tmp_path / 'sources').mkdir()
    for name in ('pilot_manifest.json', 'mapping_public_dates.json', NUMBER + '.curated.json',
                 NUMBER + '.identity-review.json', NUMBER + '.retatrutide-ex12-v1.json'):
        shutil.copyfile(SOURCE / name, tmp_path / 'sources' / name)
    seed = json.loads((tmp_path / 'sources/pilot_manifest.json').read_text())
    seed['items'] = [i for i in seed['items'] if i['publication_number'] == NUMBER]
    (tmp_path / 'sources/pilot_manifest.json').write_text(json.dumps(seed))
    directory = tmp_path / 'ocr' / NUMBER;directory.mkdir(parents=True)
    for page in range(1, 47):
        data = {'pdf_page_1_based': page, 'source_sha256': seed['items'][0]['raw_sha256'],
                'text': 'Synthetic inventory page; no experimental claim.', 'characters': 48}
        data['text_sha256'] = patents.canonical_sha256(data['text'])
        (directory / f'page-{page:04}.json').write_text(json.dumps(data))
    spec = importlib.util.spec_from_file_location('modified_peptide_ingest', ROOT / 'scripts/ingest_patents.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return tmp_path, module


def test_actual_builder_selects_hash_bound_version_without_overwriting_prior_card(build_root):
    root, module = build_root
    before = (root / 'sources' / (NUMBER + '.curated.json')).read_bytes()
    catalog = module.build_catalog(root)
    assert catalog['items'][0]['experiment_cards'][0]['example_id'] == '12'
    assert catalog['items'][0]['excluded_experiment_cards'][0]['example_id'] == 'Table 7 / Example 1 analog'
    assert (root / 'sources' / (NUMBER + '.curated.json')).read_bytes() == before


@pytest.mark.parametrize('fault', ['changed_bytes', 'traversal', 'unknown_metadata', 'symlink', 'wrong_page'])
def test_builder_rejects_unbound_or_unsafe_selected_sources(build_root,fault):
    root, module = build_root
    path = root / 'sources' / (NUMBER + '.identity-review.json')
    receipt = json.loads(path.read_text());selection = receipt['experiment_cards_source']
    selected = root / 'sources' / selection['filename']
    if fault == 'changed_bytes':selected.write_bytes(selected.read_bytes() + b' ')
    elif fault == 'traversal':selection['filename'] = '../secret.json'
    elif fault == 'unknown_metadata':selection['trusted'] = True
    elif fault == 'symlink':
        raw = selected.read_bytes();selected.unlink();other = root / 'elsewhere.json';other.write_bytes(raw);selected.symlink_to(other)
    elif fault == 'wrong_page':
        value = json.loads(selected.read_text());value['experiment_cards'][0]['pdf_page_1_based'] = 99
        selected.write_text(json.dumps(value));selection['sha256'] = patents.file_sha256(selected)
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):module.build_catalog(root)
