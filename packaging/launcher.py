"""Shared frozen entry point for the elm and elm-mcp executables."""
from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    executable_name = Path(sys.executable).stem.casefold()
    if executable_name == "elm-mcp":
        from elm_memory.mcp_server import main as mcp_main

        return mcp_main()

    from elm_memory.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
