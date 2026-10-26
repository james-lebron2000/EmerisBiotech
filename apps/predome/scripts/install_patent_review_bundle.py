"""Stage pinned patent-review originals into a new release, entirely offline.

The commit-pinned catalog identifies required bytes. Installing those bytes is
an integrity check, not scientific acceptance, and never reads runtime state.
Tar members use catalog-relative ``receipts/review/...`` names; returned manifest
paths are relative to release_root and include the ``patent_data/`` prefix.
"""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import zlib


MAX_ARCHIVE_BYTES = 12 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_TAR_BYTES = MAX_TOTAL_BYTES + 1024 * 1024
MAX_CATALOG_BYTES = 8 * 1024 * 1024
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('A complete lowercase SHA-256 is required')
    return value


def _path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 1000 or '\\' in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('Invalid patent review original path')
    parsed = PurePosixPath(value)
    if (parsed.is_absolute() or str(parsed) != value or '..' in parsed.parts
            or len(parsed.parts) < 3 or parsed.parts[:2] != ('receipts', 'review')):
        raise ValueError('Patent review originals must stay under receipts/review')
    return value


def _json(raw):
    def pairs(entries):
        value = {}
        for key, item in entries:
            if key in value:
                raise ValueError('Duplicate catalog field')
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError('Non-finite catalog value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def _references(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get('items'), list):
        raise ValueError('A patent catalog with an items list is required')
    result = {}
    def add(path, expected):
        path, expected = _path(path), _hash(expected)
        if path in result:
            raise ValueError('Duplicate patent review original reference')
        result[path] = expected
    for item in catalog['items']:
        if not isinstance(item, dict):
            raise ValueError('Invalid patent catalog item')
        receipt = item.get('research_review')
        if receipt is None:
            continue
        if not isinstance(receipt, dict):
            raise ValueError('Invalid patent research-review metadata')
        mapping = receipt.get('drug_mapping') or {}
        if not isinstance(mapping, dict) or not isinstance(mapping.get('sources', []), list):
            raise ValueError('Invalid patent source-reference metadata')
        sources = mapping.get('sources', [])
        if any(not isinstance(source, dict) for source in sources):
            raise ValueError('Invalid patent source reference')
        originals = [source for source in sources if 'original_relative_path' in source]
        if not originals:
            # Old receipts can have unrelated page previews; they are not part
            # of this separately pinned original-source transport.
            continue
        for source in originals:
            add(source['original_relative_path'], source.get('raw_sha256'))
        checks = receipt.get('page_checks', [])
        if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
            raise ValueError('Invalid patent page-check references')
        for check in checks:
            add(check.get('preview_relative_path'), check.get('preview_sha256'))
        observation = receipt.get('source_observation_record')
        if observation is not None:
            if not isinstance(observation, dict):
                raise ValueError('Invalid patent observation reference')
            add(observation.get('path'), observation.get('sha256'))
    return result


@contextmanager
def _directory(parent_fd, parts, *, create=False):
    descriptor = os.dup(parent_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(part, DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if create:
                    raise ValueError('Patent review destination directory changed') from None
                yield None
                return
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _parent(path):
    """Open each explicit filesystem ancestor without following aliases."""
    path = Path(path).absolute()
    if len(path.parts) < 2 or '..' in path.parts:
        raise ValueError('An explicit file or release path without traversal is required')
    anchor = os.open(path.anchor, DIR_FLAGS)
    try:
        with _directory(anchor, path.parts[1:-1]) as directory:
            if directory is None:
                raise FileNotFoundError('Patent review path parent is missing')
            yield directory, path.name
    finally:
        os.close(anchor)


def _read(path, maximum, *, directory=None):
    if directory is None:
        with _parent(path) as (parent, name):
            return _read(name, maximum, directory=parent)
    descriptor = os.open(path, READ_FLAGS, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= maximum:
            raise ValueError('Expected a bounded single regular file')
        raw = stream.read(maximum + 1)
        if len(raw) != info.st_size or len(raw) > maximum:
            raise ValueError('File changed or exceeded its byte limit')
    return raw


class _RegularTarInfo(tarfile.TarInfo):
    @classmethod
    def frombuf(cls, buf, encoding, errors):
        item = super().frombuf(buf, encoding, errors)
        # Reject extension headers before tarfile can consume or hide them.
        # This includes empty/global PAX, GNU long names and sparse records.
        if item.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
            raise ValueError('Only regular original files are allowed; no PAX, links or sparse records')
        return item


def _archive(path, expected_sha256, references):
    raw = _read(path, MAX_ARCHIVE_BYTES)
    if digest(raw) != _hash(expected_sha256):
        raise ValueError('Patent review archive SHA-256 differs')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded = compressed.read(MAX_EXPANDED_TAR_BYTES + 1)
        if len(expanded) > MAX_EXPANDED_TAR_BYTES:
            raise ValueError('Expanded patent review archive exceeds its byte limit')
        files = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:', tarinfo=_RegularTarInfo) as archive:
            for member in archive:
                name = _path(member.name)
                total += member.size
                if (name not in references or name in files or not member.isfile()
                        or not 0 <= member.size <= MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES
                        or member.pax_headers or member.sparse is not None):
                    raise ValueError('Unexpected, duplicate or oversized patent review archive member')
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('Missing original file bytes')
                with stream:
                    content = stream.read(MAX_MEMBER_BYTES + 1)
                if len(content) != member.size or digest(content) != references[name]:
                    raise ValueError('Patent review original bytes do not match catalog SHA-256')
                files[name] = content
            tail = expanded[archive.offset:]
        if len(expanded) % 512 or len(tail) < 1024 or any(tail):
            raise ValueError('Invalid or trailing patent review archive content')
        if set(files) != set(references):
            raise ValueError('Patent review archive must contain the exact referenced file set')
        return files
    except (OSError, EOFError, tarfile.TarError, zlib.error) as error:
        raise ValueError('Invalid bounded patent review archive') from error


def _existing(parent_fd, name, expected):
    parts = PurePosixPath(name).parts
    with _directory(parent_fd, parts[:-1]) as directory:
        if directory is None:
            return False
        try:
            raw = _read(parts[-1], MAX_MEMBER_BYTES, directory=directory)
        except FileNotFoundError:
            return False
        if digest(raw) != expected:
            raise ValueError('Existing patent review original has different bytes')
        return True


def stage(release_root, archive_path=None, expected_sha256=None):
    """Verify all references and existing targets before exclusively copying.

    The caller supplies a new release directory, never a runtime data directory.
    There is no network, model or database access, or existing-file overwrite.
    """
    with _parent(release_root) as (parent, name):
        root = os.open(name, DIR_FLAGS, dir_fd=parent)
    try:
        with _directory(root, ('patent_data',)) as patent_data:
            if patent_data is None:
                raise ValueError('Release patent_data directory is missing')
            references = _references(_json(_read('catalog.json', MAX_CATALOG_BYTES, directory=patent_data)))
            if not references:
                return []
            if archive_path is None or expected_sha256 is None:
                raise ValueError('Referenced patent review originals require an offline archive and SHA-256')
            _hash(expected_sha256)
            files = _archive(archive_path, expected_sha256, references)
            existing = {name: _existing(patent_data, name, references[name]) for name in sorted(files)}
            # All byte, set, type, path and existing-file checks precede writes.
            for name in sorted(files):
                if existing[name]:
                    continue
                parts = PurePosixPath(name).parts
                with _directory(patent_data, parts[:-1], create=True) as directory:
                    try:
                        descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                             0o644, dir_fd=directory)
                    except FileExistsError:
                        if _existing(patent_data, name, references[name]):
                            continue
                        raise
                    with os.fdopen(descriptor, 'wb') as stream:
                        os.fchmod(stream.fileno(), 0o644)
                        stream.write(files[name])
                        stream.flush()
                        os.fsync(stream.fileno())
            return [{'path': 'patent_data/' + name, 'sha256': references[name], 'bytes': len(files[name])}
                    for name in sorted(files)]
    finally:
        os.close(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    try:
        files = stage(args.release_root, args.archive, args.sha256)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, 'Patent review originals were not staged: ' + str(error) + '\n')
    print(json.dumps({'files': files, 'offline': True, 'scientific_acceptance_granted': False}, indent=2))


if __name__ == '__main__':
    main()
