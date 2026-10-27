"""Import the existing sealed UC comparison. No download or approval."""
import argparse
from pathlib import Path
from vbt.programs import Programs
from vbt.scientific_review import import_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--program', default='osmr-uc-independent')
    parser.add_argument('--author', required=True, help='实际导入记录人；导入不代表科学审阅')
    args = parser.parse_args()
    store = Programs(args.state_dir)
    print(import_comparison(store, args.program, args.source, args.author, store.get(args.program)['revision']))


if __name__ == '__main__':
    main()
