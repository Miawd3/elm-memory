# Installation

ELM v1.0 supports Windows x64 and Linux. The core Python package is platform-independent; the release artifacts differ because Windows gets a bundled runtime while Linux uses the system Python.

## Verify a download

Every release includes `SHA256SUMS.txt`.

PowerShell:

```powershell
Get-FileHash .\ELM-Memory-1.0.0-windows-x64-setup.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

Linux:

```bash
sha256sum -c SHA256SUMS.txt --ignore-missing
```

The checksum protects integrity only when you obtained `SHA256SUMS.txt` from the expected GitHub release page. v1.0 binaries are not code-signed.

## Windows installer

Download and run:

```text
ELM-Memory-1.0.0-windows-x64-setup.exe
```

The setup program:

- supports 64-bit Windows 10 and 11;
- installs for the current user under `%LOCALAPPDATA%\Programs\ELM Memory`;
- requires no administrator access;
- bundles Python, SQLite FTS5, the ELM CLI, and the MCP adapter;
- optionally adds the install directory to the user `PATH`;
- registers a standard uninstaller;
- never creates, changes, or removes a Markdown memory root.

Open a new terminal after installation and verify:

```powershell
elm --version
elm-mcp --help
```

The release uses a single Inno Setup EXE instead of MSI. MSI would add enterprise deployment machinery without removing any runtime dependency; the EXE already provides per-user installation, silent mode, upgrades, and clean uninstall.

### Portable Windows build

`elm-memory-1.0.0-windows-x64-portable.zip` contains the same bundled runtime without an installer. Extract the entire directory and run `elm.exe` or `elm-mcp.exe`; do not move either executable away from its `_internal` directory.

### Update and rollback on Windows

Run the newer setup EXE. It reuses the same application identity and install directory. Canonical Markdown and `.elm` indexes live outside the application directory and are not part of the upgrade.

To roll back, run an older trusted setup EXE and rebuild the disposable index if its schema differs:

```powershell
elm rebuild --root C:\path\to\memory --json
```

Keep a filesystem backup of canonical Markdown before any application downgrade. ELM refuses unsupported newer canonical formats rather than rewriting them silently.

### Uninstall on Windows

Use **Installed apps → ELM Memory → Uninstall**, or run the uninstaller in the application directory. Uninstall removes the runtime and its `PATH` entry. It does not remove memory roots.

## Linux bundle

Requirements:

- Python 3.11 or newer;
- the standard `venv` module;
- SQLite with FTS5 enabled;
- a POSIX shell and normal core utilities.

Install the core offline from the bundled wheel:

```bash
tar -xzf elm-memory-1.0.0-linux-any.tar.gz
cd elm-memory-1.0.0-linux-any
./install.sh
```

Install the optional MCP adapter as well:

```bash
./install.sh --with-mcp
```

The core install is offline. `--with-mcp` uses pip to download the MCP SDK and its dependencies.

The installer uses:

```text
runtime:  ${XDG_DATA_HOME:-$HOME/.local/share}/elm-memory
commands: ${XDG_BIN_HOME:-$HOME/.local/bin}/elm
          ${XDG_BIN_HOME:-$HOME/.local/bin}/elm-mcp
```

If `~/.local/bin` is not on `PATH`, the installer prints a reminder.

Check the active installation:

```bash
./install.sh --check
```

Install a newer bundle by running its `install.sh`. The previous version remains available for one-step rollback:

```bash
./install.sh --rollback
```

Remove the runtime and command links:

```bash
./install.sh --uninstall
```

Rollback and uninstall never remove Markdown memory roots.

On Debian or Ubuntu, a missing `venv` module is usually fixed with:

```bash
sudo apt install python3-venv
```

## Install from a wheel

The CLI core has no third-party runtime dependencies:

```bash
python -m venv .venv
.venv/bin/python -m pip install elm_memory-1.0.0-py3-none-any.whl
```

On Windows, use `.venv\Scripts\python.exe` instead.

For MCP:

```bash
python -m pip install "./elm_memory-1.0.0-py3-none-any.whl[mcp]"
```

The project name is `elm-memory`; the import package is `elm_memory`.

## Create or select a memory root

Create a fresh root:

```bash
elm init --root /absolute/path/to/memory --project my-project --set-default
```

Existing roots are never overwritten by `init`.

ELM resolves a root in this order:

1. `--root PATH`;
2. `ELM_ROOT`;
3. the path stored in `~/.elm-system/root`;
4. the current directory when it contains `00_registry`.

Use `elm status --json` and `elm doctor --json` after moving or restoring a root.

## macOS

There is no tested macOS installer in v1.0. The pure-Python wheel may work with Python 3.11+ and SQLite FTS5, but that path is currently unverified and unsupported. A native macOS artifact should be built and tested on macOS; PyInstaller is not a cross-compiler.

## Build release assets locally

```bash
python -m venv .release-venv
python -m pip install -e ".[mcp,release]"
python scripts/build_release.py
```

On Windows, install Inno Setup 6 and add `--windows`:

```powershell
.\.release-venv\Scripts\python.exe scripts\build_release.py --windows
```

The builder produces the wheel, source distribution, Linux bundle, skill ZIP, Windows portable ZIP and setup EXE, then writes `SHA256SUMS.txt`. Windows builds also smoke-test the frozen commands, a synthetic memory root, silent install, and uninstall.
