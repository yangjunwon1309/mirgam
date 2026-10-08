"""Start the separate API and local UI together for development; Ctrl+C stops both."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser

import requests

ROOT = Path(__file__).resolve().parents[1]


def ready(url, child):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('A local server failed to start; check its output.')
        try:
            if requests.get(url, timeout=1).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError('Local server startup timed out.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(ROOT / 'var/server'))
    parser.add_argument('--api-port', default=5100, type=int)
    parser.add_argument('--client-port', default=5000, type=int)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    cloud_url = os.environ.get('MIRGAM_DATABASE_URL', '').strip()
    if not cloud_url and not (Path(args.data_dir) / 'mirgam.sqlite3').is_file():
        parser.error('Migrate first: python scripts/migrate_csv_to_sqlite.py --source apps/static --apply')
    for port in (args.api_port, args.client_port):
        if not 1 <= port <= 65535:
            parser.error('Ports must be between 1 and 65535')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                parser.error(f'Port {port} is occupied. Stop the existing process or select another port.')
    children = []
    try:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        api_url = f'http://127.0.0.1:{args.api_port}'
        client_url = f'http://127.0.0.1:{args.client_port}'
        server = subprocess.Popen([sys.executable, '-u', str(ROOT / 'scripts/run_api_server.py'),
            '--data-dir', str(Path(args.data_dir).resolve()), '--port', str(args.api_port)], cwd=ROOT, creationflags=flags)
        children.append(server)
        ready(api_url + '/health', server)
        client = subprocess.Popen([sys.executable, '-u', str(ROOT / 'scripts/run_client.py'),
            '--api-url', api_url, '--port', str(args.client_port)], cwd=ROOT, creationflags=flags)
        children.append(client)
        ready(client_url + '/status', client)
        print(f'Open {client_url}. Keep this terminal open. Ctrl+C stops both processes.', flush=True)
        if not args.no_browser:
            webbrowser.open_new_tab(client_url)
        while all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    main()
