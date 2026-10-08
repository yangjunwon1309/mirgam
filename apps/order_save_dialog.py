"""Show the desktop's Save As dialog in its own process and main thread."""
import json
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def choose_order_export_path(directory, filename):
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--choose-order-export', str(directory), filename]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), str(directory), filename]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main():
    directory, filename = sys.argv[-2:]
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        destination = filedialog.asksaveasfilename(
            parent=root, title='주문 CSV 저장 위치와 이름 선택',
            initialdir=directory, initialfile=filename,
            defaultextension='.csv', filetypes=[('CSV 파일', '*.csv')],
            confirmoverwrite=True,
        )
    finally:
        root.destroy()
    print(json.dumps(destination, ensure_ascii=True))


if __name__ == '__main__':
    main()
