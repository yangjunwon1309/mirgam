"""Run the central API independently; never opens a browser or seeds accounts."""
import argparse
import os
from pathlib import Path
import threading
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=os.environ.get('MIRGAM_SERVER_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('MIRGAM_SERVER_PORT', '5100')))
    default_data = str(Path(sys.executable).parent / 'data') if getattr(sys, 'frozen', False) else 'var/server'
    parser.add_argument('--data-dir', default=os.environ.get('MIRGAM_SERVER_DATA_DIR', default_data))
    args = parser.parse_args(argv)
    from server.app import create_app
    from server.maintenance import maintenance
    from waitress import serve
    app = create_app(args.data_dir)
    database = app.extensions['database']
    stop = threading.Event()
    thread = threading.Thread(target=maintenance, args=(database, stop), daemon=True)
    thread.start()
    print(f'Mirgam central API: http://{args.host}:{args.port}', flush=True)
    database_mode = 'SQLite Cloud' if database.remote else 'local SQLite'
    print(f'Database: {database_mode}. Clients connect only to this API; credentials stay on the server.', flush=True)
    try:
        serve(app, host=args.host, port=args.port, threads=4, max_request_body_size=10 * 1024 * 1024)
    finally:
        stop.set()
        thread.join(timeout=6)
        database.engine.dispose()


if __name__ == '__main__':
    main()
