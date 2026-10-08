"""Run one local UI, pointing to a configurable remote or local central API."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default=None)
    parser.add_argument('--port', type=int, default=5000)
    args = parser.parse_args()
    app = create_app(args.api_url)
    from waitress import serve
    print(f'Mirgam client: http://127.0.0.1:{args.port}', flush=True)
    print(f"Central API: {app.extensions['api_client'].base_url}", flush=True)
    serve(app, host='127.0.0.1', port=args.port, threads=4)


if __name__ == '__main__':
    main()
