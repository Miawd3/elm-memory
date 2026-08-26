from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


class FixtureCopy:
    def __enter__(self) -> Path:
        self._temp = tempfile.TemporaryDirectory(prefix="elm-test-")
        self.root = Path(self._temp.name) / "memory"
        shutil.copytree(FIXTURE_ROOT, self.root)
        return self.root

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._temp.cleanup()


def run_cli(root: Path, *arguments: str) -> dict:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else os.pathsep.join((str(SOURCE_ROOT), existing))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "elm_memory.cli", *arguments, "--root", str(root), "--json"],
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)
