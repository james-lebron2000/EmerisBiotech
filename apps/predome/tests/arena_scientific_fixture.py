"""Synthetic operator documents for tests; no real people or scientific claims."""
from copy import deepcopy
from datetime import timezone
import hashlib
from pathlib import Path
import uuid

from emeris_live.arena_result_assurance import accept_scientific_adjudication, prepare_acceptance
from emeris_live.arena_review_auth import enroll_reviewer
from emeris_live.arena_store import canonical, instant, result_target_binding
from emeris_live.store import Store


CONFIRMATIONS = ('qualification_and_identity_checked', 'target_scope_checked', 'no_material_conflict',
                 'independent_of_model_execution', 'original_source_and_target_checked',
                 'individual_review_confirmed')


def _authenticated_adjudication(ledger, target):
    target_id = target['id'] if isinstance(target, dict) else target
    current = next(t for t in ledger.snapshot()['targets'] if t['id'] == target_id)
    if current.get('status') != 'adjudicated':
        raise ValueError('The synthetic acceptance fixture requires an existing adjudication')
    events = ledger.events()
    adjudication = next(e for e in reversed(events)
                        if e['kind'] == 'result_adjudicated' and e['entity_id'] == target_id)
    result = deepcopy(adjudication['payload']['result'])
    result['target_binding'] = result_target_binding(current)
    source = ('SYNTHETIC ORIGINAL OUTCOME SOURCE FOR ISOLATED TESTS\n' + canonical({
        'target_binding': result['target_binding'], 'label': result['label'],
        'first_public_at': result['first_public_at']})).encode('utf-8')
    source_hash = hashlib.sha256(source).hexdigest()
    if result.get('source_sha256') == source_hash:
        document = prepare_acceptance(ledger, target_id)
        reviews = document.get('reviews', [])
        if len(reviews) == 2 and all(r.get('review_event_sha256') and r.get('vote_sha256') for r in reviews):
            return target_id, source

    # A legacy declaration is retained exactly. Append real temporary accounts,
    # enrollments and two authenticated source reviews for a new outcome round.
    store = Store(ledger.path.parent)
    nonce = uuid.uuid4().hex
    accounts = ['sci-fixture-' + nonce + '-' + str(i) for i in range(2)]
    with store.connect() as db:
        db.executemany('INSERT INTO users VALUES(?,?,?)',
                       [(account, account, 'synthetic-unusable-password-hash') for account in accounts])
    result['source_sha256'] = source_hash
    context = {'candidate_id': 'synthetic-scientific-fixture-' + nonce,
               'source_sha256': result['source_sha256'],
               'excerpt': 'Synthetic source inspection for the exact registered trial ' + current['nct_id'],
               'source_publication_date': instant(result['first_public_at']).astimezone(timezone.utc).date().isoformat(),
               'publication_date_basis': 'reviewer_provided_not_independently_verified',
               'confirmations': {key: True for key in ('exact_trial', 'primary_endpoint',
                   'prespecified_analysis', 'first_public_release', 'source_inspected')}}
    for index, account in enumerate(accounts):
        grant = enroll_reviewer(ledger, account, 'sci-principal-' + nonce + '-' + str(index), [target_id],
                                'a' * 64, 'Synthetic account linkage for an isolated scientific acceptance test')
        status = ledger.record_authenticated_result(target_id, deepcopy(result), account_id=account,
            enrollment_sha256=grant['event_hash'],
            review_note='Synthetic individual source inspection by fixture account ' + str(index),
            review_context=deepcopy(context), supersedes=adjudication['payload']['result_sha256'])
    if status != 'adjudicated':
        raise ValueError('The synthetic authenticated reviews did not form a new adjudication')
    return target_id, source


def scientific_acceptance_document(ledger, target, tmp_path=None):
    """Prepare a complete document backed by distinct, explicitly synthetic files."""
    target_id, source = _authenticated_adjudication(ledger, target)
    parent = Path(tmp_path) if tmp_path is not None else ledger.path.parent / 'scientific-fixture-inputs'
    directory = parent / ('synthetic-scientific-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    document = prepare_acceptance(ledger, target_id)
    original = directory / 'original-source.txt'
    original.write_bytes(source)
    original.chmod(0o600)
    document['source_evidence_file'] = str(original)
    document['acceptor'] = 'Synthetic responsible operator for isolated tests'
    document['note'] = 'Synthetic documentary acceptance; no real qualifications or clinical validity established'
    for index, review in enumerate(document['reviews']):
        qualification = directory / ('qualification-' + str(index) + '.txt')
        independence = directory / ('independence-' + str(index) + '.txt')
        qualification.write_text('PRIVATE SYNTHETIC QUALIFICATION DOCUMENT ' + str(index) + '\n', encoding='utf-8')
        independence.write_text('PRIVATE SYNTHETIC INDEPENDENCE STATEMENT ' + str(index) + '\n', encoding='utf-8')
        qualification.chmod(0o600)
        independence.chmod(0o600)
        review.update(scientific_role='Synthetic scientific reviewer for the registered target',
                      qualification_check_method='manual_original_documents',
                      qualification_check_note='Synthetic original qualification and identity documents checked',
                      qualification_evidence_file=str(qualification),
                      independence_conclusion='independent_for_registered_target',
                      independence_check_note='Synthetic target-specific independence statement checked',
                      independence_statement_file=str(independence),
                      confirmations={key: True for key in CONFIRMATIONS})
    return document


def scientifically_accept(ledger, target, tmp_path=None):
    """Append genuine test acceptance over auth reviews; preserve all old events."""
    document = scientific_acceptance_document(ledger, target, tmp_path)
    prior = next((e for e in reversed(ledger.events()) if e['entity_id'] == document['target_id']
                  and e['kind'] in ('scientific_adjudication_acceptance', 'scientific_adjudication_revocation')), None)
    return accept_scientific_adjudication(ledger, document,
                                          supersedes=prior['event_hash'] if prior else None)
