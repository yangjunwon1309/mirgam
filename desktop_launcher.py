"""Launch Mirgam as a local desktop application in the default browser."""

import socket
import sys
import threading
import time
import webbrowser
import argparse

def available_port():
    """Use the familiar port first; fall back if another local app uses it."""
    for port in (5000, 5001, 5002):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--choose-order-export':
        from apps.order_save_dialog import main as show_save_dialog
        show_save_dialog()
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default=None)
    parser.add_argument('--port', type=int, default=None)
    args = parser.parse_args()
    from apps.app import create_app
    from waitress import serve
    app = create_app(args.api_url)

    port = args.port or available_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"Mirgam is starting at {url}")
    print(f"Central API: {app.extensions['api_client'].base_url}")
    print("Keep this window open while using Mirgam. Close it to stop the local site.")
    server = threading.Thread(
        target=lambda: serve(app, host="127.0.0.1", port=port, threads=4),
        daemon=False,
    )
    server.start()

    # Do not let opening a browser delay the server's first successful bind.
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    webbrowser.open_new_tab(url)
    server.join()


if __name__ == "__main__":
    main()
