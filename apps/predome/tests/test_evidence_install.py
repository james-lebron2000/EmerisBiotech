import hashlib
import io
import tarfile
import pytest
from scripts.install_evidence import install

def bundle(tmp_path, contents):
    archive=tmp_path/'bundle.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name,raw in contents.items():
            m=tarfile.TarInfo(name);m.size=len(raw);t.addfile(m,io.BytesIO(raw))
    data=contents['dashboard_data/fixture.json']
    return archive,{'release_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':[{'path':'dashboard_data/fixture.json','sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}]}

def test_restore_hash_pinned_allowlist_is_idempotent(tmp_path):
    archive,m=bundle(tmp_path,{'dashboard_data/fixture.json':b'{}','run.py':b'never imported'})
    root=tmp_path/'app';root.mkdir()
    assert install(archive,root,m)['files']==1
    assert install(archive,root,m)['status']=='verified'
    assert not (root/'run.py').exists()

def test_wrong_archive_has_no_writes(tmp_path):
    archive,m=bundle(tmp_path,{'dashboard_data/fixture.json':b'{}'})
    m['release_archive_sha256']='0'*64
    with pytest.raises(ValueError,match='archive SHA'):install(archive,tmp_path/'app',m)
    assert not (tmp_path/'app').exists()

def test_traversal_rejected_even_when_not_requested(tmp_path):
    archive,m=bundle(tmp_path,{'dashboard_data/fixture.json':b'{}','../escape':b'bad'})
    with pytest.raises(ValueError,match='Unsafe'):install(archive,tmp_path/'app',m)
    assert not (tmp_path/'escape').exists()

def test_existing_evidence_never_overwritten(tmp_path):
    archive,m=bundle(tmp_path,{'dashboard_data/fixture.json':b'{}'})
    root=tmp_path/'app';(root/'dashboard_data').mkdir(parents=True)
    target=root/'dashboard_data/fixture.json';target.write_bytes(b'original')
    with pytest.raises(ValueError,match='overwrite'):install(archive,root,m)
    assert target.read_bytes()==b'original'

def test_symlink_destination_rejected(tmp_path):
    archive,m=bundle(tmp_path,{'dashboard_data/fixture.json':b'{}'})
    root=tmp_path/'app';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (root/'dashboard_data').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='escapes'):install(archive,root,m)
    assert list(outside.iterdir())==[]
