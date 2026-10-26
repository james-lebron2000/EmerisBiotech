"""Canonical Predome checks; no installs, model calls, source acquisition or deploys."""
import argparse
import ast
from pathlib import Path
import shutil
import subprocess
import sys

from test_source import ROOT, source_tests, test_environment

BROWSER_TESTS = (
    'ui_predict_context.cjs', 'ui_prospective_coverage.cjs', 'ui_arena.cjs',
    'ui_arena_home.cjs', 'ui_arena_public.cjs', 'ui_arena_lab.cjs', 'ui_trials_readonly.cjs',
    'ui_arena_review.cjs',
)


def check_runtime(root=ROOT):
    """Check syntax and direct static imports, without importing application code.

    CLI modules may depend on emeris_live; the reverse creates an execution-layer
    dependency. Dynamic imports and transitive dependencies are outside this check.
    """
    runtime = root / 'emeris_live'
    if not runtime.is_dir():
        raise ValueError('Missing emeris_live package')
    commands = {p.stem for p in (root / 'scripts').glob('*.py')}
    files = (sorted(runtime.rglob('*.py')) + sorted((root / 'scripts').glob('*.py'))
             + sorted((root / 'deploy').rglob('*.py')))
    if not files:
        raise ValueError('No Python source files')
    for path in files:
        tree = ast.parse(path.read_bytes(), filename=str(path.relative_to(root)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                raise ValueError(f'Production assert disappears under Python -O: {path.relative_to(root)}:{node.lineno}; use an explicit exception')
        if not path.is_relative_to(runtime):
            continue
        for node in ast.walk(tree):
            names = []
            absolute = True
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    # Relative imports within emeris_live (including subpackages)
                    # cannot resolve to the sibling CLI directory. Only a climb
                    # to the application package can reach that directory.
                    if node.level - 1 != len(path.relative_to(runtime).parts):
                        continue
                    absolute = False
                names = [node.module] if node.module else []
                if not node.module:
                    names.extend(item.name for item in node.names)
                if not node.level and node.module == 'apps.predome':
                    names.extend('apps.predome.' + item.name for item in node.names)
            if any(name == 'scripts' or name.startswith('scripts.')
                   or name == 'apps.predome.scripts' or name.startswith('apps.predome.scripts.')
                   or absolute and name.split('.')[0] in commands for name in names):
                raise ValueError(f'Runtime imports CLI: {path.relative_to(root)}:{node.lineno}')
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('static', 'source', 'full'), default='source',
                        help='full requires an authorized, separately verified local evidence bundle')
    parser.add_argument('--browser', action='store_true', help='also run the intercepted browser fixture suites')
    args = parser.parse_args(argv)
    try:
        selected = source_tests() if args.suite == 'source' else []
        count = check_runtime()
    except (ValueError, OSError, SyntaxError) as exc:
        # Syntax exceptions can contain source text; do not echo arbitrary contents.
        if isinstance(exc, SyntaxError):
            parser.exit(1, f'Python syntax check failed: {exc.filename}:{exc.lineno}\n')
        parser.exit(1, str(exc) + '\n')
    print(f'[static] {count} Python files; production asserts and direct runtime-to-CLI imports rejected', flush=True)
    node = shutil.which('node')
    if not node:
        parser.exit(1, 'Node.js is required for JavaScript syntax checks; install the documented runtime first.\n')
    env = test_environment(source_only=args.suite == 'source', browser=args.browser)
    for path in sorted((ROOT / 'static').glob('*.js')):
        result = subprocess.run([node, '--check', str(path)], cwd=ROOT, env=env)
        if result.returncode:
            return result.returncode
    print('[static] JavaScript syntax checked without executing pages', flush=True)
    if args.suite != 'static':
        print(f'[{args.suite}] actual pytest implementations; external service fixtures only', flush=True)
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', *(selected or ['tests'])], cwd=ROOT, env=env)
        if result.returncode:
            return result.returncode
    if args.browser:
        for name in BROWSER_TESTS:
            print(f'[browser fixture] {name}', flush=True)
            result = subprocess.run([node, str(ROOT / 'tests' / name)], cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    print('Engineering checks passed; no clinical validation or production deployment was performed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
