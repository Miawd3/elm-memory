#!/usr/bin/env sh
set -eu

APP_NAME="elm-memory"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_HOME=${XDG_BIN_HOME:-"$HOME/.local/bin"}
INSTALL_ROOT="$DATA_HOME/$APP_NAME"
WITH_MCP=0
ACTION=install

usage() {
    cat <<'EOF'
Usage: ./install.sh [--with-mcp] [--check | --rollback | --uninstall]

  --with-mcp   Install the optional MCP adapter (downloads its Python dependencies).
  --check      Verify the active installation.
  --rollback   Switch back to the previously active ELM version.
  --uninstall  Remove the ELM runtime and command links. Memory roots are never removed.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-mcp) WITH_MCP=1 ;;
        --check) ACTION=check ;;
        --rollback) ACTION=rollback ;;
        --uninstall) ACTION=uninstall ;;
        --help|-h) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$INSTALL_ROOT" in
    ""|"/") printf 'Refusing unsafe install root: %s\n' "$INSTALL_ROOT" >&2; exit 1 ;;
esac

remove_command_link() {
    command_path="$1"
    if [ -L "$command_path" ]; then
        target=$(readlink "$command_path" || true)
        case "$target" in
            "$INSTALL_ROOT"/*) rm -f -- "$command_path" ;;
        esac
    fi
}

if [ "$ACTION" = uninstall ]; then
    remove_command_link "$BIN_HOME/elm"
    remove_command_link "$BIN_HOME/elm-mcp"
    if [ -d "$INSTALL_ROOT" ]; then
        rm -rf -- "$INSTALL_ROOT"
    fi
    printf 'ELM runtime removed. Your Markdown memory roots were not touched.\n'
    exit 0
fi

if [ "$ACTION" = rollback ]; then
    if [ ! -L "$INSTALL_ROOT/previous" ]; then
        printf 'No previous ELM installation is available for rollback.\n' >&2
        exit 1
    fi
    current_target=$(readlink "$INSTALL_ROOT/current")
    previous_target=$(readlink "$INSTALL_ROOT/previous")
    ln -sfn -- "$previous_target" "$INSTALL_ROOT/current.new"
    mv -Tf -- "$INSTALL_ROOT/current.new" "$INSTALL_ROOT/current"
    ln -sfn -- "$current_target" "$INSTALL_ROOT/previous"
    printf 'Rolled back to %s. Memory roots were not changed.\n' "$previous_target"
    exit 0
fi

if [ "$ACTION" = check ]; then
    if [ ! -x "$INSTALL_ROOT/current/bin/elm" ]; then
        printf 'ELM is not installed under %s.\n' "$INSTALL_ROOT" >&2
        exit 1
    fi
    "$INSTALL_ROOT/current/bin/python" -c \
        'from importlib.metadata import version; print("elm-memory " + version("elm-memory"))'
    "$INSTALL_ROOT/current/bin/elm" --help >/dev/null
    "$INSTALL_ROOT/current/bin/python" -c \
        'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("CREATE VIRTUAL TABLE probe USING fts5(body)"); print("SQLite FTS5: ok")'
    exit 0
fi

PYTHON_BIN=${PYTHON:-python3}
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf 'Python 3.11 or newer is required. Install python3 and python3-venv first.\n' >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf 'Python 3.11 or newer is required. Found: ' >&2
    "$PYTHON_BIN" --version >&2 || true
    exit 1
fi

if ! "$PYTHON_BIN" -c \
    'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")'; then
    printf 'This Python build does not provide SQLite FTS5. Install a standard CPython build with FTS5.\n' >&2
    exit 1
fi

set -- "$SCRIPT_DIR"/elm_memory-*.whl
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    printf 'Expected exactly one elm_memory wheel next to install.sh.\n' >&2
    exit 1
fi
WHEEL_PATH=$1
VERSION=$(
    "$PYTHON_BIN" - "$WHEEL_PATH" <<'PY'
import re
import sys

match = re.fullmatch(r"elm_memory-([A-Za-z0-9_.!+-]+)-py3-none-any\.whl", sys.argv[1].rsplit("/", 1)[-1])
if not match:
    raise SystemExit("Unsupported wheel filename")
print(match.group(1))
PY
)

mkdir -p -- "$INSTALL_ROOT/versions" "$BIN_HOME"
FINAL_DIR="$INSTALL_ROOT/versions/$VERSION"
COMPLETE_MARKER="$FINAL_DIR/.elm-install-complete"
if [ -e "$FINAL_DIR" ] && { [ ! -x "$FINAL_DIR/bin/elm" ] || [ ! -f "$COMPLETE_MARKER" ]; }; then
    printf 'An incomplete installation already exists at %s. Run --uninstall before retrying.\n' "$FINAL_DIR" >&2
    exit 1
fi
if [ ! -x "$FINAL_DIR/bin/elm" ]; then
    cleanup_install() { rm -rf -- "$FINAL_DIR"; }
    trap cleanup_install EXIT HUP INT TERM
    if ! "$PYTHON_BIN" -m venv "$FINAL_DIR"; then
        printf 'Could not create a virtual environment. On Debian/Ubuntu install python3-venv.\n' >&2
        exit 1
    fi
    if [ "$WITH_MCP" -eq 1 ]; then
        "$FINAL_DIR/bin/python" -m pip install --disable-pip-version-check "$WHEEL_PATH[mcp]"
    else
        "$FINAL_DIR/bin/python" -m pip install --no-index --no-deps --disable-pip-version-check "$WHEEL_PATH"
    fi
    installed_version=$("$FINAL_DIR/bin/python" -c \
        'from importlib.metadata import version; print(version("elm-memory"))')
    if [ "$installed_version" != "$VERSION" ]; then
        printf 'Installed version %s does not match bundled version %s.\n' \
            "$installed_version" "$VERSION" >&2
        exit 1
    fi
    "$FINAL_DIR/bin/elm" --help >/dev/null
    printf '%s\n' "$VERSION" > "$COMPLETE_MARKER"
    trap - EXIT HUP INT TERM
fi

if [ "$WITH_MCP" -eq 1 ] && [ ! -x "$FINAL_DIR/bin/elm-mcp" ]; then
    "$FINAL_DIR/bin/python" -m pip install --disable-pip-version-check "$WHEEL_PATH[mcp]"
    if [ ! -x "$FINAL_DIR/bin/elm-mcp" ]; then
        printf 'The MCP dependencies were installed but elm-mcp is unavailable.\n' >&2
        exit 1
    fi
fi

if [ -L "$INSTALL_ROOT/current" ]; then
    old_target=$(readlink "$INSTALL_ROOT/current")
    if [ "$old_target" != "$FINAL_DIR" ]; then
        ln -sfn -- "$old_target" "$INSTALL_ROOT/previous"
    fi
fi
ln -sfn -- "$FINAL_DIR" "$INSTALL_ROOT/current.new"
mv -Tf -- "$INSTALL_ROOT/current.new" "$INSTALL_ROOT/current"
ln -sfn -- "$INSTALL_ROOT/current/bin/elm" "$BIN_HOME/elm"
if [ -x "$INSTALL_ROOT/current/bin/elm-mcp" ]; then
    ln -sfn -- "$INSTALL_ROOT/current/bin/elm-mcp" "$BIN_HOME/elm-mcp"
else
    remove_command_link "$BIN_HOME/elm-mcp"
fi

printf 'Installed ELM %s.\n' "$VERSION"
case ":${PATH}:" in
    *":$BIN_HOME:"*) ;;
    *) printf 'Add %s to PATH to use the elm command from any directory.\n' "$BIN_HOME" ;;
esac
