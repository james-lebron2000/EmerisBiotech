"""Run the public source contract without access to frozen evidence files."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIRS = ("predome_data", "dashboard_data", "trial_data", "public_data")


def main():
    present = [name for name in EVIDENCE_DIRS if (ROOT / name).exists()]
    if present:
        raise SystemExit("Source-only CI requires no evidence directories; use a clean checkout: " + ", ".join(present))
    selected = [line.strip() for line in (ROOT / "ci-tests.txt").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if not selected or any(not item.startswith("tests/test_") or not item.endswith(".py")
                           or ".." in Path(item).parts or not (ROOT / item).is_file()
                           for item in selected):
        raise SystemExit("Invalid or empty source test selection")
    # These tests use synthetic fixtures and HTTP stubs. Never enable paid
    # providers or background collection in a public pull-request check.
    env = os.environ.copy()
    env.update(EMERIS_MODE="local", EMERIS_ENABLE_USER_LLM="false", EMERIS_ENABLE_ARENA_LLM="false",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    for key in tuple(env):
        if key.endswith(("API_KEY", "APIKEY", "API_TOKEN", "API_SECRET")) or key.startswith(("DEEPSEEK_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "ENSUREOK_")):
            env.pop(key)
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *selected], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
