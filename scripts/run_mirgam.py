"""Compatibility entry point: validate isolated SQLite fixtures or run the client.

The API must run separately. Verification never modifies the farm source CSVs.
"""
import argparse
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.verify_only or not args.skip_verify:
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_central_api")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            return 1
    if args.verify_only:
        return 0
    from apps.app import create_app
    from waitress import serve
    app = create_app(args.api_url)
    print(f"Mirgam: http://127.0.0.1:{args.port}", flush=True)
    serve(app, host=args.host, port=args.port, threads=4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
