"""
Tray-based launcher for the Bot and Backend, replacing the old start.bat/
stop.bat setup (two separate minimized console windows, killed by matching
their window titles) with a single small status window that can be
minimized to the Windows system tray instead.

Runs both processes as subprocesses of this one, using the project's own
.venv interpreter. Normally started for you by start.vbs (which does the
.venv/.env existence checks first, then launches this file with
pythonw.exe so no console window appears at all - this window IS the UI).

Usage (normally done for you by start.vbs):
    .venv\\Scripts\\pythonw.exe launcher.py
"""

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

if sys.platform == "win32":
    import ctypes

import tkinter as tk
from tkinter import messagebox

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    # tkinter itself is always available (stdlib), so we can show a proper
    # error dialog here even though there's no console window (this file is
    # normally launched via pythonw.exe) instead of failing completely
    # silently.
    _root = tk.Tk()
    _root.withdraw()
    messagebox.showerror(
        "PoGo Stats - Missing dependencies",
        "pystray and/or Pillow are not installed.\n\n"
        "Open a terminal in this folder with the venv activated and run:\n"
        "  pip install -r requirements.txt",
    )
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

DASHBOARD_URL = "http://localhost:8000"

# Keeps the Bot/Backend subprocesses from ever popping up their own console
# window, even though they're started via python.exe (not pythonw.exe) -
# this way their stdout/stderr can still be redirected to the log files
# below. Hardcoded instead of referencing subprocess.CREATE_NO_WINDOW
# directly since that attribute doesn't exist on non-Windows platforms.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ManagedProcess:
    """Wraps a single subprocess (Bot or Backend) plus its log file, and
    tracks whether it's supposed to be running vs. having crashed/exited."""

    def __init__(self, name, args, log_name):
        self.name = name
        self.args = args
        self.log_path = LOGS_DIR / log_name
        self.process = None
        self._log_file = None

    def start(self):
        if self.is_running():
            return
        self._log_file = open(self.log_path, "a", encoding="utf-8")
        self.process = subprocess.Popen(
            self.args,
            cwd=BASE_DIR,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def is_running(self):
        return self.process is not None and self.process.poll() is None


bot_process = ManagedProcess("Bot", [str(VENV_PYTHON), "-m", "bot.bot"], "bot.log")
backend_process = ManagedProcess(
    "Backend",
    [str(VENV_PYTHON), "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"],
    "backend.log",
)
ALL_PROCESSES = [bot_process, backend_process]


def ensure_app_identity():
    """On Windows, tells the OS this process is its own distinct
    application rather than lumping it in with every other Python-based
    program. Without this, the taskbar icon keeps showing the generic
    Python logo (pulled from pythonw.exe itself) even after iconbitmap()/
    iconphoto() are set - those only reliably change the titlebar icon on
    their own. Must be called before the Tk window is created."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PoGoStats.Launcher")
    except Exception:
        pass


def make_tray_icon_image():
    """Draws a small pokeball-style icon in code (red top half, white
    bottom half, black band) instead of shipping a separate .ico/.png
    asset - matches backend/static/favicon.svg's color scheme."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, size - 2, size - 2), fill="#f0f0f0", outline="#1a1a1a", width=4)
    draw.pieslice((2, 2, size - 2, size - 2), 180, 360, fill="#e94747", outline="#1a1a1a", width=4)
    draw.rectangle((2, size // 2 - 3, size - 2, size // 2 + 3), fill="#1a1a1a")
    draw.ellipse(
        (size // 2 - 9, size // 2 - 9, size // 2 + 9, size // 2 + 9),
        fill="#f0f0f0", outline="#1a1a1a", width=4,
    )
    return img


class ControlWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PoGo Stats")
        self.root.resizable(False, False)
        self.root.geometry("280x200")

        # Use the same pokeball icon as the tray icon for the window's own
        # titlebar/taskbar icon, instead of Tkinter's default feather icon
        # (or, on Windows, the taskbar's generic Python logo). Two calls
        # are needed: iconbitmap() with a real .ico file is what actually
        # controls the Windows taskbar icon, while iconphoto() covers the
        # titlebar and other platforms. The .ico is regenerated on every
        # launch next to launcher.py so it always matches the current
        # code-drawn design rather than being a separate shipped asset.
        icon_image = make_tray_icon_image()
        ico_path = BASE_DIR / "launcher_icon.ico"
        try:
            icon_image.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            self.root.iconbitmap(default=str(ico_path))
        except Exception:
            pass
        # The PhotoImage reference is kept on self - Tkinter doesn't hold
        # its own reference, so without this the icon would get garbage
        # collected and silently disappear.
        self._window_icon = ImageTk.PhotoImage(icon_image)
        self.root.iconphoto(True, self._window_icon)

        # Clicking the window's own close button minimizes to the tray
        # instead of quitting - matches the "runs quietly in the
        # background" behavior the old minimized console windows had.
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.status_labels = {}
        for proc in ALL_PROCESSES:
            row = tk.Frame(self.root)
            row.pack(fill="x", padx=16, pady=(12, 0))
            dot = tk.Label(row, text="●", font=("Segoe UI", 14), fg="#888888")
            dot.pack(side="left")
            name_label = tk.Label(row, text=proc.name, font=("Segoe UI", 11))
            name_label.pack(side="left", padx=(6, 0))
            self.status_labels[proc.name] = dot

        button_frame = tk.Frame(self.root)
        button_frame.pack(fill="x", padx=16, pady=16)
        tk.Button(button_frame, text="Open Dashboard", command=self.open_dashboard).pack(fill="x", pady=2)
        tk.Button(button_frame, text="Restart", command=self.restart_all).pack(fill="x", pady=2)
        tk.Button(button_frame, text="Quit", command=self.quit_app).pack(fill="x", pady=2)

        self.tray_icon = None
        self._build_tray_icon(icon_image)

        self._poll_status()

    def _build_tray_icon(self, icon_image):
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda icon, item: self.root.after(0, self.show_window), default=True),
            pystray.MenuItem("Quit", lambda icon, item: self.root.after(0, self.quit_app)),
        )
        self.tray_icon = pystray.Icon("pogostats", icon_image, "PoGo Stats", menu)
        # pystray's own event loop blocks, so it needs its own thread -
        # tkinter's mainloop stays on the main thread, which it requires.
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def open_dashboard(self):
        webbrowser.open(DASHBOARD_URL)

    def restart_all(self):
        for proc in ALL_PROCESSES:
            proc.stop()
        for proc in ALL_PROCESSES:
            proc.start()

    def quit_app(self):
        for proc in ALL_PROCESSES:
            proc.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def _poll_status(self):
        for proc in ALL_PROCESSES:
            running = proc.is_running()
            color = "#2ecc71" if running else "#e74c3c"
            self.status_labels[proc.name].config(fg=color)
        # Check again in 2 seconds - catches a crash shortly after it
        # happens instead of only when you happen to look at the window.
        self.root.after(2000, self._poll_status)

    def run(self):
        self.root.mainloop()


def main():
    ensure_app_identity()
    for proc in ALL_PROCESSES:
        proc.start()
    window = ControlWindow()
    window.run()


if __name__ == "__main__":
    main()
