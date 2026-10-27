"""Exact comparison of explicitly transcribed modified-peptide features.

This does not read chemical drawings or authenticate a review. Original-document
inspection supplies the features; unknown or omitted chemistry never matches.
"""
import re

VERSION = 'predome-modified-peptide-comparison-1'
AMINO_ACIDS = {'Ala', 'Arg', 'Asn', 'Asp', 'Cys', 'Gln', 'Glu', 'His', 'Ile',
               'Leu', 'Lys', 'Met', 'Phe', 'Pro', 'Ser', 'Thr', 'Trp', 'Tyr', 'Val'}
RESIDUES = {'Gly', 'Aib', '2-methyl-L-Leu', '2-methyl-L-Lys'} | {
    stereo + '-' + name for stereo in ('L', 'D') for name in AMINO_ACIDS}
FIELDS = {'residues', 'n_terminus', 'c_terminus', 'conjugations'}
CONJUGATION = {'position', 'attachment_atom', 'linker_from_peptide',
               'glutamate_alpha_carboxyl', 'lipid_carbons', 'lipid_distal_carboxyl'}


def checked_peptide(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Complete modified-peptide features are required')
    residues = value['residues']
    if (not isinstance(residues, list) or not 1 <= len(residues) <= 200
            or any(not isinstance(r, str) or r not in RESIDUES for r in residues)):
        raise ValueError('Explicit stereochemical residue tokens are required')
    if value['n_terminus'] not in ('free_amine', 'acetyl') or value['c_terminus'] not in ('amide', 'carboxyl'):
        raise ValueError('Explicit supported terminal groups are required')
    conjugations = value['conjugations']
    if not isinstance(conjugations, list) or len(conjugations) > 8:
        raise ValueError('A bounded list of explicit conjugations is required')
    positions = set()
    for entry in conjugations:
        if not isinstance(entry, dict) or set(entry) != CONJUGATION:
            raise ValueError('Complete conjugation chemistry is required')
        position = entry['position']
        if (type(position) is not int or not 1 <= position <= len(residues)
                or position in positions or residues[position - 1] not in ('L-Lys', 'D-Lys')
                or entry['attachment_atom'] != 'N6'):
            raise ValueError('A unique lysine N6 conjugation is required')
        positions.add(position)
        linker = entry['linker_from_peptide']
        # This vocabulary describes the pilot family only. L-gamma-Glu means
        # its gamma carboxyl links toward the peptide and alpha amine to lipid.
        if (not isinstance(linker, list) or not 1 <= len(linker) <= 6
                or any(unit not in ('AEEA', 'L-gamma-Glu') for unit in linker)
                or linker[-1] != 'L-gamma-Glu'
                or entry['glutamate_alpha_carboxyl'] != 'free'
                or type(entry['lipid_carbons']) is not int or not 16 <= entry['lipid_carbons'] <= 22
                or entry['lipid_distal_carboxyl'] != 'free'):
            raise ValueError('Explicit supported linker and diacid chemistry are required')
    return value


def compare_peptides(reference, candidate):
    reference, candidate = checked_peptide(reference), checked_peptide(candidate)
    differences = []
    if reference['residues'] != candidate['residues']:
        differences.append('residues')
    differences.extend(key for key in ('n_terminus', 'c_terminus', 'conjugations')
                       if reference[key] != candidate[key])
    return {'schema_version': VERSION, 'matches': not differences, 'differing_fields': differences,
            'method': 'exact_transcribed_features_not_automated_structure_recognition',
            'human_scientific_review': False, 'clinical_prediction_validated': False}


def structure_review_errors(item, receipt):
    """Check a content-bound AI structure review, separately from its authority."""
    analysis = receipt.get('identity_analysis')
    try:
        if (receipt.get('schema_version') != 'predome-patent-ai-source-review-1.1'
                or not isinstance(analysis, dict) or analysis.get('schema_version') != VERSION
                or not re.fullmatch(r'[a-f0-9]{64}', str(analysis.get('reference_source_sha256', '')))
                or analysis.get('candidate_source_sha256') != item.get('raw_sha256')
                or type(analysis.get('reference_pdf_page_1_based')) is not int
                or analysis['reference_pdf_page_1_based'] < 1):
            raise ValueError('Missing structure review sources')
        sources = receipt.get('drug_mapping', {}).get('sources', [])
        if not any(s.get('raw_sha256') == analysis['reference_source_sha256']
                   and s.get('role') == 'official_inn_modified_peptide_identity' for s in sources):
            raise ValueError('Missing official reference binding')
        if not compare_peptides(analysis['reference'], analysis['candidate'])['matches']:
            raise ValueError('Modified peptide features differ')
        identity = analysis.get('candidate_identity', {})
        if (not isinstance(identity, dict)
                or not isinstance(identity.get('example_id'), str)
                or not re.fullmatch(r'\d{1,3}', identity['example_id'])
                or not isinstance(identity.get('sequence_id'), str)
                or not re.fullmatch(r'SEQ ID NO:\d{1,4}', identity['sequence_id'])
                or not item.get('experiment_cards')
                or any(not isinstance(card, dict) or card.get('example_id') != identity['example_id']
                       or card.get('sequence_id') != identity['sequence_id']
                       or card.get('source_sha256') != item.get('raw_sha256')
                       for card in item['experiment_cards'])):
            raise ValueError('Experiment card is for a different compound')
    except (ValueError, KeyError, TypeError):
        return ['modified_peptide_identity_check_incomplete_or_mismatched']
    return []
