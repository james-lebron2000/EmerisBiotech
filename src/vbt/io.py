import hashlib, json, os, tempfile
from pathlib import Path
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024): h.update(block)
    return h.hexdigest()

def load(path):
    return json.loads(Path(path).read_text())

def ensure_storage(path):
    """Refuse to recreate a missing removable-volume tree on the system disk."""
    path = Path(path).resolve()
    if len(path.parts) >= 3 and path.parts[1] == "Volumes":
        volume = Path(*path.parts[:3])
        if not volume.is_mount():
            raise OSError(f"External volume is not mounted: {volume}")
    return path

def write(path, obj):
    path = ensure_storage(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False); f.write("\n")
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def within(path, root):
    p, r = Path(path).resolve(), Path(root).resolve()
    if not p.is_relative_to(r): raise ValueError(f"Path escapes allowed root: {p}")
    return p
