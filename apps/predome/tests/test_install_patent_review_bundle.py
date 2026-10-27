"""Real temporary catalogs, archives and filesystem targets; no live services."""
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

import pytest


spec = importlib.util.spec_from_file_location('patent_review_bundle',
    Path(__file__).parents[1] / 'scripts/install_patent_review_bundle.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

ORIGINAL = 'receipts/review/new/who-original.pdf'
PREVIEW = 'receipts/review/new/who-p123.png'
OBSERVATION = 'receipts/review/new/ai-observations.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def catalog(files):
    return {'items': [{'publication_number': 'SYNTHETIC-NEW', 'research_review': {
        'drug_mapping': {'sources': [{'original_relative_path': ORIGINAL, 'raw_sha256': sha(files[ORIGINAL])}]},
        'page_checks': [{'preview_relative_path': PREVIEW, 'preview_sha256': sha(files[PREVIEW])}],
        'source_observation_record': {'path': OBSERVATION, 'sha256': sha(files[OBSERVATION])}}},
        # An old unrelated receipt must not expand the requested transport set.
        {'publication_number': 'SYNTHETIC-OLD', 'research_review': {
            'drug_mapping': {'sources': [{'raw_sha256': 'a' * 64}]},
            'page_checks': [{'preview_relative_path': '../old-unrelated-preview'}],
            'source_observation_record': {'path': '/unrelated/private/file'}}}]}


def archive(tmp_path, files, *, extras=(), format=tarfile.USTAR_FORMAT):
    path = tmp_path / 'originals.tar.gz'
    with tarfile.open(path, 'w:gz', format=format) as out:
        for name, raw in files.items():
            member = tarfile.TarInfo(name); member.size = len(raw)
            out.addfile(member, io.BytesIO(raw))
        for member, raw in extras:
            out.addfile(member, io.BytesIO(raw) if raw is not None else None)
    return path, sha(path.read_bytes())


@pytest.fixture
def release(tmp_path):
    root = tmp_path / 'release'; data = root / 'patent_data'; data.mkdir(parents=True)
    files = {ORIGINAL: b'%PDF-1.7\nSynthetic original bytes, not scientific evidence\n',
             PREVIEW: b'\x89PNG\r\nSynthetic source preview',
             OBSERVATION: b'{"scope":"synthetic AI observation, not human review"}\n'}
    metadata = catalog(files)
    (data / 'catalog.json').write_text(json.dumps(metadata))
    return root, files, metadata


def write_catalog(root, metadata):
    (root / 'patent_data/catalog.json').write_text(json.dumps(metadata))


def assert_not_staged(root, files):
    assert all(not (root / 'patent_data' / name).exists() for name in files)


def test_stage_exact_original_preview_and_observation_without_runtime(release, tmp_path, monkeypatch):
    root, files, _ = release
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    sealed = runtime / 'arena.sqlite3'; sealed.write_bytes(b'untouched runtime sentinel')
    monkeypatch.setenv('EMERIS_DATA_DIR', str(runtime))
    before = sealed.stat()
    path, expected = archive(tmp_path, files)
    records = installer.stage(root, path, expected)
    assert records == [{'path': 'patent_data/' + name, 'sha256': sha(files[name]), 'bytes': len(files[name])}
                       for name in sorted(files)]
    for name, raw in files.items():
        target = root / 'patent_data' / name
        assert target.read_bytes() == raw and stat.S_IMODE(target.stat().st_mode) == 0o644
    assert sealed.read_bytes() == b'untouched runtime sentinel'
    assert sealed.stat().st_ino == before.st_ino and sealed.stat().st_mtime_ns == before.st_mtime_ns


def test_no_new_original_reference_is_a_noop_and_ignores_old_receipt(release):
    root, files, metadata = release
    metadata['items'] = metadata['items'][1:]
    write_catalog(root, metadata)
    assert installer.stage(root) == []
    assert_not_staged(root, files)


@pytest.mark.parametrize('missing', ['archive', 'hash', 'invalid_hash'])
def test_new_references_require_archive_and_pinned_full_hash(release, tmp_path, missing):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    if missing == 'archive': path = None
    if missing == 'hash': expected = None
    if missing == 'invalid_hash': expected = 'not-a-sha256'
    with pytest.raises(ValueError): installer.stage(root, path, expected)
    assert_not_staged(root, files)


@pytest.mark.parametrize('fault', ['transport_hash', 'member_hash', 'missing', 'extra', 'duplicate'])
def test_all_transport_checks_finish_before_any_copy(release, tmp_path, fault):
    root, files, _ = release
    actual = dict(files); extras = []
    if fault == 'member_hash': actual[PREVIEW] += b'tampered'
    if fault == 'missing': actual.pop(OBSERVATION)
    if fault == 'extra': actual['receipts/review/new/unlisted.txt'] = b'not referenced'
    if fault == 'duplicate':
        member = tarfile.TarInfo(ORIGINAL); member.size = len(files[ORIGINAL])
        extras.append((member, files[ORIGINAL]))
    path, expected = archive(tmp_path, actual, extras=extras)
    if fault == 'transport_hash': expected = '0' * 64
    with pytest.raises(ValueError): installer.stage(root, path, expected)
    assert_not_staged(root, files)
    assert not (root / 'patent_data/receipts').exists()


@pytest.mark.parametrize('name', ['/tmp/original.pdf', '../original.pdf', 'receipts/review/../original.pdf',
    'receipts/review/./original.pdf', 'receipts//review/original.pdf', 'receipts\\review\\original.pdf',
    'sources/original.pdf', 'receipts/review', 'receipts/review/bad\x00name'])
def test_catalog_paths_are_canonical_and_confined_before_transport(release, name):
    root, files, metadata = release
    metadata['items'][0]['research_review']['drug_mapping']['sources'][0]['original_relative_path'] = name
    write_catalog(root, metadata)
    with pytest.raises(ValueError): installer.stage(root)
    assert_not_staged(root, files)


@pytest.mark.parametrize('field', ['original', 'preview', 'observation', 'duplicate_reference'])
def test_metadata_hashes_and_duplicate_references_are_checked(release, field):
    root, files, metadata = release
    review = metadata['items'][0]['research_review']
    if field == 'original': review['drug_mapping']['sources'][0]['raw_sha256'] = 'unknown'
    if field == 'preview': review['page_checks'][0].pop('preview_sha256')
    if field == 'observation': review['source_observation_record']['sha256'] = 'A' * 64
    if field == 'duplicate_reference': review['page_checks'].append(deepcopy(review['page_checks'][0]))
    write_catalog(root, metadata)
    with pytest.raises(ValueError): installer.stage(root)
    assert_not_staged(root, files)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'fifo', 'pax', 'empty_pax', 'sparse', 'longname'])
def test_tar_special_files_and_extensions_are_rejected(release, tmp_path, kind):
    root, files, _ = release
    member = tarfile.TarInfo('receipts/review/new/special')
    member.type = {'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE, 'directory': tarfile.DIRTYPE,
                   'fifo': tarfile.FIFOTYPE, 'pax': tarfile.XHDTYPE, 'empty_pax': tarfile.XHDTYPE,
                   'sparse': tarfile.GNUTYPE_SPARSE, 'longname': tarfile.GNUTYPE_LONGNAME}[kind]
    member.linkname = ORIGINAL
    raw = b'18 comment=bad\n' if kind == 'pax' else b''
    member.size = len(raw)
    path, expected = archive(tmp_path, files, extras=[(member, raw)])
    with pytest.raises(ValueError): installer.stage(root, path, expected)
    assert_not_staged(root, files)


def test_real_pax_headers_are_rejected_before_tarfile_hides_them(release, tmp_path):
    root, files, _ = release
    path = tmp_path / 'pax.tar.gz'
    with tarfile.open(path, 'w:gz', format=tarfile.PAX_FORMAT, pax_headers={'comment': 'global header'}) as out:
        for name, raw in files.items():
            member = tarfile.TarInfo(name); member.size = len(raw); member.pax_headers = {'comment': 'per-file'}
            out.addfile(member, io.BytesIO(raw))
    with pytest.raises(ValueError): installer.stage(root, path, sha(path.read_bytes()))
    assert_not_staged(root, files)


@pytest.mark.parametrize('kind', ['compressed', 'expanded', 'member', 'total', 'trailing'])
def test_archive_byte_limits_and_hidden_trailing_data(release, tmp_path, monkeypatch, kind):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    if kind == 'compressed': monkeypatch.setattr(installer, 'MAX_ARCHIVE_BYTES', path.stat().st_size - 1)
    if kind == 'expanded':
        monkeypatch.setattr(installer, 'MAX_EXPANDED_TAR_BYTES', 1024)
        path.write_bytes(gzip.compress(b'\0' * 1025)); expected = sha(path.read_bytes())
        monkeypatch.setattr(installer.tarfile, 'open', lambda *a, **kw: pytest.fail('Expansion must be bounded before tar parsing'))
    if kind == 'member': monkeypatch.setattr(installer, 'MAX_MEMBER_BYTES', len(files[ORIGINAL]) - 1)
    if kind == 'total': monkeypatch.setattr(installer, 'MAX_TOTAL_BYTES', sum(map(len, files.values())) - 1)
    if kind == 'trailing':
        path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()) + b'hidden file bytes'))
        expected = sha(path.read_bytes())
    with pytest.raises(ValueError): installer.stage(root, path, expected)
    assert_not_staged(root, files)


@pytest.mark.parametrize('kind', ['root', 'root_ancestor', 'patent_data', 'catalog', 'parent', 'file', 'archive', 'archive_ancestor'])
def test_filesystem_symlinks_are_not_followed(release, tmp_path, kind):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    outside = tmp_path / 'outside'; outside.mkdir()
    sentinel = outside / 'original'; sentinel.write_bytes(files[ORIGINAL])
    if kind == 'root':
        link = tmp_path / 'release-link'; link.symlink_to(root, target_is_directory=True); root = link
    elif kind == 'root_ancestor':
        link = tmp_path / 'ancestor-link'; link.symlink_to(tmp_path, target_is_directory=True); root = link / root.name
    elif kind == 'patent_data':
        old = root / 'patent_data'; moved = tmp_path / 'moved-patent-data'; old.rename(moved)
        old.symlink_to(moved, target_is_directory=True)
    elif kind == 'catalog':
        old = root / 'patent_data/catalog.json'; moved = outside / 'catalog.json'; old.rename(moved); old.symlink_to(moved)
    elif kind == 'parent': (root / 'patent_data/receipts').symlink_to(outside, target_is_directory=True)
    elif kind == 'file':
        target = root / 'patent_data' / ORIGINAL; target.parent.mkdir(parents=True); target.symlink_to(sentinel)
    elif kind == 'archive':
        link = tmp_path / 'archive-link'; link.symlink_to(path); path = link
    elif kind == 'archive_ancestor':
        link = tmp_path / 'archive-ancestor'; link.symlink_to(tmp_path, target_is_directory=True); path = link / path.name
    with pytest.raises((OSError, ValueError)): installer.stage(root, path, expected)
    assert sentinel.read_bytes() == files[ORIGINAL]
    assert not (outside / 'review').exists()
    assert not (root / 'patent_data' / PREVIEW).exists()


def test_matching_existing_file_is_unchanged_and_missing_files_are_copied(release, tmp_path):
    root, files, _ = release
    target = root / 'patent_data' / ORIGINAL; target.parent.mkdir(parents=True)
    target.write_bytes(files[ORIGINAL]); target.chmod(0o640); os.utime(target, ns=(10**15, 10**15))
    before = target.stat()
    path, expected = archive(tmp_path, files)
    first = installer.stage(root, path, expected)
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns, stat.S_IMODE(after.st_mode)) == (
        before.st_ino, before.st_mtime_ns, stat.S_IMODE(before.st_mode))
    assert target.read_bytes() == files[ORIGINAL]
    assert installer.stage(root, path, expected) == first


def test_conflicting_existing_bytes_prevent_all_new_writes(release, tmp_path):
    root, files, _ = release
    target = root / 'patent_data' / ORIGINAL; target.parent.mkdir(parents=True); target.write_bytes(b'existing different bytes')
    path, expected = archive(tmp_path, files)
    with pytest.raises(ValueError, match='different bytes'): installer.stage(root, path, expected)
    assert target.read_bytes() == b'existing different bytes'
    assert not (root / 'patent_data' / PREVIEW).exists()
    assert not (root / 'patent_data' / OBSERVATION).exists()


def test_cli_stages_only_offline_originals_and_reports_no_scientific_acceptance(release, tmp_path):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    result = subprocess.run([sys.executable, '-B', installer.__file__, '--release-root', str(root),
        '--archive', str(path), '--sha256', expected], capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output['offline'] is True and output['scientific_acceptance_granted'] is False
    assert len(output['files']) == 3


def test_explicit_version_stages_its_catalog_without_changing_default_files(release, tmp_path):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    installer.stage(root, path, expected)
    default_paths = [root / 'patent_data/catalog.json', *(root / 'patent_data' / name for name in files)]
    before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in default_paths}
    directory = 'patent_data/versions/assays-20261027-v2'
    selected = root / directory; selected.mkdir(parents=True)
    version_files = {name: raw + b'\nSYNTHETIC version-two bytes' for name, raw in files.items()}
    (selected / 'catalog.json').write_text(json.dumps(catalog(version_files)))
    path, expected = archive(tmp_path, version_files)
    records = installer.stage(root, path, expected, patent_directory=directory)
    assert records == [{'path': directory + '/' + name, 'sha256': sha(version_files[name]),
                        'bytes': len(version_files[name])} for name in sorted(version_files)]
    assert all((selected / name).read_bytes() == raw for name, raw in version_files.items())
    assert all((p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) == before[p] for p in default_paths)
    assert installer.stage(root, path, expected, patent_directory=directory) == records


@pytest.mark.parametrize('directory', [None, 'other_data', '/patent_data', 'patent_data/versions',
    'patent_data/versions/../escape', 'patent_data/versions/.hidden', 'patent_data/versions/v2/nested',
    'patent_data//versions/v2', 'patent_data/versions/' + 'x' * 101])
def test_invalid_patent_directory_rejected_before_release_writes(release, tmp_path, directory):
    root, files, _ = release
    path, expected = archive(tmp_path, files)
    original = (root / 'patent_data/catalog.json').read_bytes()
    with pytest.raises(ValueError, match='Patent directory must be'):
        installer.stage(root, path, expected, patent_directory=directory)
    assert_not_staged(root, files)
    assert (root / 'patent_data/catalog.json').read_bytes() == original
    assert not (root / 'patent_data/versions').exists()


def test_missing_version_neither_creates_directory_nor_uses_default_catalog(release):
    root, files, _ = release
    with pytest.raises(ValueError, match='directory is missing'):
        installer.stage(root, patent_directory='patent_data/versions/missing-v2')
    assert not (root / 'patent_data/versions').exists()
    assert_not_staged(root, files)


@pytest.mark.parametrize('kind', ['versions', 'version', 'catalog', 'receipts', 'file', 'conflict'])
def test_version_links_and_conflicts_prevent_all_new_writes(release, tmp_path, kind):
    root, files, metadata = release
    directory = 'patent_data/versions/assays-v2'
    selected = root / directory; selected.mkdir(parents=True)
    (selected / 'catalog.json').write_text(json.dumps(metadata))
    default_before = (root / 'patent_data/catalog.json').read_bytes()
    outside = tmp_path / 'outside'; outside.mkdir()
    sentinel = outside / 'original'; sentinel.write_bytes(b'untouched outside bytes')
    if kind in ['versions', 'version']:
        original = selected.parent if kind == 'versions' else selected
        moved = outside / 'moved'; original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    elif kind == 'catalog':
        original = selected / 'catalog.json'; moved = outside / 'catalog.json'
        original.rename(moved); original.symlink_to(moved)
    elif kind == 'receipts':
        (selected / 'receipts').symlink_to(outside, target_is_directory=True)
    else:
        original = selected / ORIGINAL; original.parent.mkdir(parents=True)
        if kind == 'file': original.symlink_to(sentinel)
        else: original.write_bytes(b'existing conflicting version-two bytes')
    path, expected = archive(tmp_path, files)
    with pytest.raises((ValueError, OSError)):
        installer.stage(root, path, expected, patent_directory=directory)
    assert not (selected / PREVIEW).exists()
    assert not (selected / OBSERVATION).exists()
    assert_not_staged(root, files)
    assert sentinel.read_bytes() == b'untouched outside bytes'
    assert (root / 'patent_data/catalog.json').read_bytes() == default_before
    if kind == 'conflict':
        assert (selected / ORIGINAL).read_bytes() == b'existing conflicting version-two bytes'


def test_cli_explicit_version_reports_selected_prefix_only(release, tmp_path):
    root, files, metadata = release
    directory = 'patent_data/versions/assays_20261027-v2'
    selected = root / directory; selected.mkdir(parents=True)
    (selected / 'catalog.json').write_text(json.dumps(metadata))
    path, expected = archive(tmp_path, files)
    result = subprocess.run([sys.executable, '-B', installer.__file__, '--release-root', str(root),
        '--archive', str(path), '--sha256', expected, '--patent-directory', directory],
        capture_output=True, text=True, check=True)
    output = json.loads(result.stdout)
    assert output['offline'] is True and output['scientific_acceptance_granted'] is False
    assert [item['path'] for item in output['files']] == [directory + '/' + name for name in sorted(files)]
    assert all((selected / name).read_bytes() == raw for name, raw in files.items())
    assert_not_staged(root, files)
