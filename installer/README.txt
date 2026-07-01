Audora Installer
================

This package bundles the application source code together with helper scripts
that automate the installation process on both Windows and Unix-like systems.

Building the installer
----------------------
1. Ensure you have Python 3.8 or newer available.
2. Recommended for current yt-dlp YouTube reliability: install one supported
   JavaScript runtime on PATH, such as Deno 2.3+, Node.js 22+, Bun 1.2.11+,
   or QuickJS.
3. (Windows only) Install PyInstaller so the standalone executable can be
   produced:
       pip install pyinstaller
4. From the project root, run:
       python installer/build.py
5. The archive "dist/audora_installer.zip" will be created. On Windows the
   build also produces "dist/AudoraSetup.exe" and bundles `audora.exe` inside
   the `app/` directory of the archive.

Note: the build script must be executed on Windows to produce the standalone
executable. Running it on other operating systems will stop with an error that
explains the requirement.

Using the installer
-------------------
1. Distribute the generated `AudoraSetup.exe` on Windows, or extract the ZIP
   file on the target machine.
2. On Windows, the simplest path is to run `AudoraSetup.exe`. It opens a
   graphical setup window, extracts the payload, and runs the installer for you.
3. If using the ZIP directly, run the installer script appropriate for the
   platform:
   * Windows:  Right-click `install.ps1` and choose "Run with PowerShell". The
     script can install Python automatically if it is not already available.
   * Linux/macOS:  Execute `bash install.sh` from a terminal. The script will
     attempt to install Python using `apt` or Homebrew if those package
     managers are detected.
3. After the installer finishes, use the generated `run_app` helper script to
   launch the program:
   * Windows:  `./run_app.ps1`
   * Linux/macOS:  `./run_app.sh`

Both installers create an isolated virtual environment to avoid interfering
with any existing Python installations on the system. The Windows installer
also provisions local Audora runtimes for VLC playback and FFmpeg/ffprobe, and
installs Microsoft Edge WebView2 when it is missing.
