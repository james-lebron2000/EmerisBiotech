"""Run the public source contract without access to frozen evidence files."""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIRS = ("predome_data", "dashboard_data", "trial_data", "public_data",
                 "arena_data/source_snapshots", "patent_data/raw", "patent_data/ocr", "patent_data/receipts")


def source_tests(root=ROOT):
    """Public config/catalogue files are allowed; local frozen originals are not."""
    present = [name for name in EVIDENCE_DIRS if (root / name).exists() or (root / name).is_symlink()]
    if present:
        raise SystemExit("Source-only CI requires no evidence directories; use a clean checkout: " + ", ".join(present))
    selected = [line.strip() for line in (root / "ci-tests.txt").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if (not selected or len(set(selected)) != len(selected) or (root / 'tests').is_symlink()
            or any(not re.fullmatch(r'tests/test_[A-Za-z0-9_]+\.py', item)
                   or (root / item).is_symlink() or not (root / item).is_file()
                   or not (root / item).resolve().is_relative_to(root.resolve()) for item in selected)):
        raise SystemExit("Invalid or empty source test selection")
    return selected


def test_environment(*, source_only=False, browser=False):
    """Do not inherit provider config, live-test opt-ins or runner injection flags.

    This controls child configuration, not filesystem or network sandboxing.
    External HTTP is intercepted by the selected tests themselves.
    """
    names = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'SYSTEMROOT', 'WINDIR',
             'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    if browser:
        names.update({'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'})
    env = {key: value for key, value in os.environ.items() if key in names}
    env.update(EMERIS_MODE='local', EMERIS_ENABLE_USER_LLM='false', EMERIS_ENABLE_ARENA_LLM='false',
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    if source_only:
        env['EMERIS_SOURCE_ONLY_CI'] = 'true'
    return env


def main():
    selected = source_tests()
    return subprocess.call([sys.executable, '-m', 'pytest', '-q', *selected], cwd=ROOT,
                           env=test_environment(source_only=True))


if __name__ == "__main__":
    raise SystemExit(main())
