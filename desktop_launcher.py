"""Launch Mirgam as a local desktop application in the default browser."""

import atexit
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser
import argparse


_local_api_cleanup = None


def _attach_kill_on_close_job(process):
    """Tie a packaged API process to this client process on Windows."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_longlong),
            ('PerJobUserTimeLimit', ctypes.c_longlong),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount',
        )]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', BasicLimitInformation),
            ('IoInfo', IoCounters),
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(job, process._handle)
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return job, kernel32


def _close_job(job):
    if job:
        handle, kernel32 = job
        kernel32.CloseHandle(handle)


def health_response_ready(response):
    """Accept the central API envelope returned by /health."""
    try:
        if not response.ok:
            return False
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get('data'), dict):
            payload = payload['data']
        return isinstance(payload, dict) and payload.get('ready') is True
    except (ValueError, AttributeError, TypeError):
        return False


def start_bundled_local_api():
    """Reuse a healthy local API, or start the packaged one when configured."""
    global _local_api_cleanup
    if getattr(sys, 'frozen', False):
        server_exe = Path(sys.executable).resolve().parent.parent / 'MirgamServer' / 'MirgamServer.exe'
        command = [str(server_exe)]
        data_dir = server_exe.parent / 'data'
    else:
        root = Path(__file__).resolve().parent
        server_exe = root / 'server_launcher.py'
        command = [sys.executable, str(server_exe)]
        data_dir = root / 'var' / 'server'
    if not server_exe.is_file():
        print('Local API executable is not present in this package.')
        return None
    url = 'http://127.0.0.1:5100/health'
    try:
        import requests
        response = requests.get(url, timeout=(0.5, 1))
        if health_response_ready(response):
            print('Local API is already running at http://127.0.0.1:5100')
            return None
    except (requests.RequestException, ValueError, AttributeError):
        pass
    if not os.environ.get('MIRGAM_DATABASE_URL', '').strip():
        print('Local API not started: configure MIRGAM_DATABASE_URL on this PC to use the same cloud database.')
        return None
    command.extend(['--host', '127.0.0.1', '--port', '5100', '--data-dir', str(data_dir)])
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    log_root = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Mirgam' / 'logs'
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / 'local-api.log'
    with log_path.open('ab') as log_file:
        process = subprocess.Popen(command, cwd=str(server_exe.parent), creationflags=flags,
                                   stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT)
    job = _attach_kill_on_close_job(process)
    if os.name == 'nt' and job is None:
        print('Warning: Windows could not attach the local API to the client shutdown group.')
    stopped = False

    def stop_local_api():
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            _close_job(job)

    _local_api_cleanup = stop_local_api
    atexit.register(stop_local_api)
    try:
        import requests
        startup_timeout = max(15, min(180, int(os.environ.get('MIRGAM_LOCAL_API_STARTUP_TIMEOUT', '90'))))
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print(f'Local API stopped during startup. Check the local log: {log_path}')
                stop_local_api()
                return None
            try:
                response = requests.get(url, timeout=(0.5, 1))
                if health_response_ready(response):
                    print('Local API started at http://127.0.0.1:5100')
                    return process
            except (requests.RequestException, ValueError, AttributeError):
                pass
            time.sleep(0.2)
    except ImportError:
        pass
    stop_local_api()
    print(f'Local API startup timed out. Check the local log: {log_path}. The Render server remains available.')
    return None


def stop_bundled_local_api():
    """Stop only the API child launched by this client, not a reused server."""
    global _local_api_cleanup
    cleanup = _local_api_cleanup
    _local_api_cleanup = None
    if cleanup is not None:
        cleanup()
        atexit.unregister(cleanup)

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
    from apps.api_client import apply_client_runtime_settings
    apply_client_runtime_settings()
    start_bundled_local_api()
    from apps.app import create_app
    from waitress import create_server
    app = create_app(args.api_url)

    port = args.port or available_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"Mirgam is starting at {url}")
    print(f"Central API: {app.extensions['api_client'].base_url}")
    print("Keep this window open while using Mirgam. Close it to stop the local site.")
    http_server = None
    try:
        # Bind before opening the browser. Running on the main thread ensures
        # Ctrl+C enters this cleanup path instead of leaving a server thread alive.
        http_server = create_server(app, host="127.0.0.1", port=port, threads=4)
        webbrowser.open_new_tab(url)
        http_server.run()
    except KeyboardInterrupt:
        print("Stopping Mirgam and its local API...")
    finally:
        try:
            if http_server is not None:
                http_server.close()
        finally:
            stop_bundled_local_api()


if __name__ == "__main__":
    main()
