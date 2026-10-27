"""Serve the synthetic showcase locally without starting collection workers."""
import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18766)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    print('Patient Lab initializing local workbench...', flush=True)
    from vbt.workbench import Workbench, handler
    app = Workbench(args.workspace, args.state_dir)
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler(app)) as server:
        print(f'Patient Lab ready: http://127.0.0.1:{server.server_port}/patient-lab', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
