"""Utility script to build a distributable installer package for the project."""
from __future__ import annotations

import platform
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
ARCHIVE_NAME = "audora_installer.zip"
EXECUTABLE_STEM = "audora"
EXECUTABLE_NAME = f"{EXECUTABLE_STEM}.exe"
SETUP_STEM = "AudoraSetup"
SETUP_EXE_NAME = f"{SETUP_STEM}.exe"
ICON_PATH = ROOT / "ui" / "web" / "assets" / "app-icon.ico"
WEB_ASSETS_PATH = ROOT / "ui" / "web"

APP_ITEMS = [
    "core",
    "ui",
    "main.py",
    "requirements.txt",
    "README.md",
]

INSTALLER_FILES = [
    Path(__file__).with_name("install.ps1"),
    Path(__file__).with_name("install.sh"),
    Path(__file__).with_name("README.txt"),
]


def copy_app_payload(destination: Path) -> None:
    """Copy the application source into the installer payload directory."""
    app_dir = destination / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    for item in APP_ITEMS:
        src = ROOT / item
        dst = app_dir / Path(item).name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)
        else:
            raise FileNotFoundError(f"Required item '{item}' was not found at {src}")


def build_windows_executable(destination: Path) -> None:
    """Build the Windows executable and copy it into the app payload directory.

    Building a Windows executable requires running this build script on a
    Windows host with PyInstaller installed. A helpful error message is raised
    if those requirements are not met.
    """

    if platform.system() != "Windows":
        raise RuntimeError(
            "Building the Windows executable requires running on Windows. "
            "Re-run this script on a Windows machine with PyInstaller installed."
        )

    try:
        import PyInstaller  # noqa: F401  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "PyInstaller is required to build the Windows executable. "
            "Install it with 'pip install pyinstaller'."
        ) from exc

    with tempfile.TemporaryDirectory() as build_dir:
        build_path = Path(build_dir)
        dist_path = build_path / "dist"
        work_path = build_path / "build"
        spec_path = build_path / "spec"

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--name",
            EXECUTABLE_STEM,
            "--icon",
            str(ICON_PATH),
            "--add-data",
            f"{WEB_ASSETS_PATH}{os.pathsep}ui/web",
            "--hidden-import",
            "vlc",
            str(ROOT / "main.py"),
            "--distpath",
            str(dist_path),
            "--workpath",
            str(work_path),
            "--specpath",
            str(spec_path),
        ]

        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError as exc:  # pragma: no cover - subprocess guard
            raise RuntimeError(
                "Failed to execute PyInstaller. Ensure it is installed and available "
                "on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("PyInstaller failed to build the executable.") from exc

        built_exe = dist_path / EXECUTABLE_NAME
        if not built_exe.exists():
            raise RuntimeError(
                "The expected executable was not produced by PyInstaller: "
                f"{built_exe}"
            )

        shutil.copy2(built_exe, destination / EXECUTABLE_NAME)


def _write_setup_bootstrap(runner_path: Path, archive_name: str) -> None:
    source = textwrap.dedent(
        """
        from __future__ import annotations

        import os
        import queue
        import shutil
        import subprocess
        import sys
        import threading
        import zipfile
        from pathlib import Path
        import tkinter as tk
        from tkinter import messagebox, ttk


        ARCHIVE_NAME = "__ARCHIVE_NAME__"


        def bundled_path(name: str) -> Path:
            base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            return base_dir / name


        def install_root() -> Path:
            base_dir = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return Path(base_dir) / "Audora" / "installer"


        def hidden_startupinfo():
            if not sys.platform.startswith("win"):
                return None
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return startupinfo


        def create_no_window_flag() -> int:
            return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


        class AudoraInstaller(tk.Tk):
            def __init__(self) -> None:
                super().__init__()
                self.title("Audora Setup")
                self.geometry("560x420")
                self.minsize(520, 380)
                self.resizable(True, True)
                self._messages: queue.Queue[tuple[str, str]] = queue.Queue()
                self._return_code: int | None = None
                self._package_root: Path | None = None

                icon_path = bundled_path("app-icon.ico")
                if icon_path.exists():
                    try:
                        self.iconbitmap(str(icon_path))
                    except tk.TclError:
                        pass

                self._build_ui()
                self.after(100, self._drain_messages)
                threading.Thread(target=self._install, name="AudoraSetupInstall", daemon=True).start()

            def _build_ui(self) -> None:
                self.columnconfigure(0, weight=1)
                self.rowconfigure(0, weight=1)

                frame = ttk.Frame(self, padding=24)
                frame.grid(row=0, column=0, sticky="nsew")
                frame.columnconfigure(0, weight=1)
                frame.rowconfigure(4, weight=1)

                title = ttk.Label(frame, text="Installing Audora", font=("Segoe UI", 18, "bold"))
                title.grid(row=0, column=0, sticky="w")

                self.status = ttk.Label(frame, text="Preparing installer...", font=("Segoe UI", 10))
                self.status.grid(row=1, column=0, sticky="w", pady=(8, 14))

                self.progress = ttk.Progressbar(frame, mode="indeterminate")
                self.progress.grid(row=2, column=0, sticky="ew")
                self.progress.start(12)

                log_label = ttk.Label(frame, text="Details")
                log_label.grid(row=3, column=0, sticky="w", pady=(18, 6))

                log_frame = ttk.Frame(frame)
                log_frame.grid(row=4, column=0, sticky="nsew")
                log_frame.columnconfigure(0, weight=1)
                log_frame.rowconfigure(0, weight=1)

                self.log = tk.Text(log_frame, height=9, wrap="word", state="disabled", font=("Consolas", 9))
                self.log.grid(row=0, column=0, sticky="nsew")
                scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
                scrollbar.grid(row=0, column=1, sticky="ns")
                self.log.configure(yscrollcommand=scrollbar.set)

                buttons = ttk.Frame(frame)
                buttons.grid(row=5, column=0, sticky="e", pady=(18, 0))
                self.launch_button = ttk.Button(buttons, text="Launch Audora", command=self._launch, state="disabled")
                self.launch_button.grid(row=0, column=0, padx=(0, 8))
                self.finish_button = ttk.Button(buttons, text="Close", command=self.destroy, state="disabled")
                self.finish_button.grid(row=0, column=1)

            def _post(self, kind: str, message: str) -> None:
                self._messages.put((kind, message))

            def _log(self, message: str) -> None:
                self._post("log", message)

            def _set_status(self, message: str) -> None:
                self._post("status", message)

            def _drain_messages(self) -> None:
                while True:
                    try:
                        kind, message = self._messages.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "status":
                        self.status.configure(text=message)
                    elif kind == "log":
                        self.log.configure(state="normal")
                        self.log.insert("end", message.rstrip() + "\\n")
                        self.log.see("end")
                        self.log.configure(state="disabled")
                    elif kind == "done":
                        self._finish(int(message))
                self.after(100, self._drain_messages)

            def _install(self) -> None:
                try:
                    archive_path = bundled_path(ARCHIVE_NAME)
                    target_root = install_root()
                    if not archive_path.exists():
                        raise RuntimeError(f"Installer payload missing: {archive_path}")

                    self._set_status("Extracting Audora...")
                    self._log("Extracting installer payload.")
                    if target_root.exists():
                        shutil.rmtree(target_root)
                    target_root.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(target_root)

                    self._package_root = target_root / "audora_installer"
                    install_script = self._package_root / "install.ps1"
                    if not install_script.exists():
                        raise RuntimeError(f"Install script missing: {install_script}")

                    self._set_status("Installing Audora dependencies...")
                    command = [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(install_script),
                    ]
                    process = subprocess.Popen(
                        command,
                        cwd=self._package_root,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        startupinfo=hidden_startupinfo(),
                        creationflags=create_no_window_flag(),
                    )
                    assert process.stdout is not None
                    for line in process.stdout:
                        if line.strip():
                            self._log(line.rstrip())
                    self._return_code = process.wait()
                    self._post("done", str(self._return_code))
                except Exception as exc:  # pylint: disable=broad-except
                    self._log(str(exc))
                    self._return_code = 1
                    self._post("done", "1")

            def _finish(self, return_code: int) -> None:
                self.progress.stop()
                self.finish_button.configure(state="normal")
                if return_code == 0:
                    self.status.configure(text="Audora was installed successfully.")
                    self.launch_button.configure(state="normal")
                    messagebox.showinfo("Audora Setup", "Audora was installed successfully.")
                else:
                    self.status.configure(text="Audora installation failed.")
                    messagebox.showerror("Audora Setup", "Audora installation failed. Check the details in the installer window.")

            def _launch(self) -> None:
                if self._package_root is None:
                    return
                launcher = self._package_root / "run_app.ps1"
                if not launcher.exists():
                    messagebox.showerror("Audora Setup", "The Audora launcher was not found.")
                    return
                subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(launcher),
                    ],
                    cwd=self._package_root,
                    startupinfo=hidden_startupinfo(),
                    creationflags=create_no_window_flag(),
                )
                self.destroy()


        def main() -> int:
            app = AudoraInstaller()
            app.mainloop()
            return app._return_code or 0


        if __name__ == "__main__":
            raise SystemExit(main())
        """
    ).lstrip().replace("__ARCHIVE_NAME__", archive_name)
    runner_path.write_text(source, encoding="utf-8")


def build_windows_setup_executable(archive_path: Path) -> Path:
    """Wrap the installer archive in a single Windows setup executable."""

    if platform.system() != "Windows":
        raise RuntimeError(
            "Building the Windows setup executable requires running on Windows."
        )

    with tempfile.TemporaryDirectory() as build_dir:
        build_path = Path(build_dir)
        runner_path = build_path / "audora_setup_bootstrap.py"
        dist_path = build_path / "dist"
        work_path = build_path / "build"
        spec_path = build_path / "spec"
        _write_setup_bootstrap(runner_path, archive_path.name)

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--windowed",
            "--name",
            SETUP_STEM,
            "--icon",
            str(ICON_PATH),
            "--add-data",
            f"{archive_path}{os.pathsep}.",
            "--add-data",
            f"{ICON_PATH}{os.pathsep}.",
            str(runner_path),
            "--distpath",
            str(dist_path),
            "--workpath",
            str(work_path),
            "--specpath",
            str(spec_path),
        ]

        subprocess.run(cmd, check=True)
        built_setup = dist_path / SETUP_EXE_NAME
        if not built_setup.exists():
            raise RuntimeError(f"The expected setup executable was not produced: {built_setup}")

        output_path = DIST_DIR / SETUP_EXE_NAME
        shutil.copy2(built_setup, output_path)
        return output_path


def copy_installer_scripts(destination: Path) -> None:
    """Copy helper scripts for installing the application."""
    for file_path in INSTALLER_FILES:
        if not file_path.exists():
            raise FileNotFoundError(f"Installer script missing: {file_path}")
        shutil.copy2(file_path, destination / file_path.name)


def build_archive() -> Path:
    """Build a zip archive containing the app payload and installer scripts."""
    DIST_DIR.mkdir(exist_ok=True)
    archive_path = DIST_DIR / ARCHIVE_NAME

    with tempfile.TemporaryDirectory() as tmp_dir:
        build_root = Path(tmp_dir) / "audora_installer"
        build_root.mkdir()

        copy_app_payload(build_root)
        app_destination = build_root / "app"
        try:
            build_windows_executable(app_destination)
        except RuntimeError as exc:
            raise RuntimeError(
                "Unable to build the Windows executable: "
                f"{exc}"
            ) from exc

        copy_installer_scripts(build_root)

        shutil.make_archive(
            base_name=str(archive_path.with_suffix("")),
            format="zip",
            root_dir=build_root.parent,
            base_dir=build_root.name,
        )

    return archive_path


def main() -> None:
    archive_path = build_archive()
    print(f"Installer package created at: {archive_path}")
    if platform.system() == "Windows":
        setup_path = build_windows_setup_executable(archive_path)
        print(f"Windows setup executable created at: {setup_path}")


if __name__ == "__main__":
    main()
