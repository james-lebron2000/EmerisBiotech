"""Restore only allowlisted evidence from an operator-supplied, hash-pinned bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = {'predome_data', 'dashboard_data', 'trial_data', 'public_data'}

def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def install(archive, root, manifest):
    root = Path(root).resolve()
    if sha_file(archive) != manifest['release_archive_sha256']:
        raise ValueError('Evidence bundle archive SHA-256 mismatch')
    expected = {}
    for item in manifest['files']:
        rel = PurePosixPath(item['path'])
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in DATA_DIRS or str(rel) in expected:
            raise ValueError('Unsafe or duplicate evidence manifest path')
        destination = root / str(rel)
        if not destination.resolve().is_relative_to(root):
            raise ValueError('Evidence destination escapes app directory')
        expected[str(rel)] = item
    with tarfile.open(archive, 'r:gz') as tar:
        members = {}
        for member in tar.getmembers():
            rel = PurePosixPath(member.name)
            if not member.isfile() or rel.is_absolute() or '..' in rel.parts or member.name in members:
                raise ValueError('Unsafe or duplicate archive member')
            members[member.name] = member
        # Check every selected byte before changing any destination.
        for name, item in expected.items():
            member = members.get(name)
            if member is None or member.size != item['bytes']:
                raise ValueError('Evidence member missing or length mismatch: ' + name)
            with tar.extractfile(member) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                    raise ValueError('Evidence member SHA-256 mismatch: ' + name)
            dest = root / name
            if dest.exists() and sha_file(dest) != item['sha256']:
                raise ValueError('Refusing to overwrite different existing evidence: ' + name)
        for name, item in expected.items():
            dest = root / name
            if dest.exists(): continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.parent.resolve().is_relative_to(root): raise ValueError('Unsafe evidence parent')
            temp = dest.with_name(dest.name + '.installing')
            try:
                with temp.open('xb') as out, tar.extractfile(members[name]) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''): out.write(block)
                if sha_file(temp) != item['sha256']: raise ValueError('Copied evidence SHA-256 mismatch')
                os.replace(temp, dest)
            finally:
                if temp.exists(): temp.unlink()
    return {'status': 'verified', 'files': len(expected), 'archive_sha256': manifest['release_archive_sha256']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.archive, ROOT, json.loads((ROOT / 'evidence-files-1.1.8.json').read_text()))))
