from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _bootstrap import REPOSITORY_ROOT, SOURCE_ROOT
import elm_memory
from elm_memory import cli


class RootResolutionTests(unittest.TestCase):
    def test_explicit_root_has_highest_priority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-root-") as temporary:
            expected = Path(temporary).resolve()
            with patch.dict(os.environ, {"ELM_ROOT": str(REPOSITORY_ROOT)}):
                resolved = cli.resolve_root(str(expected))

        self.assertEqual(expected, resolved)

    def test_environment_root_is_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-root-") as temporary:
            expected = Path(temporary).resolve()
            with patch.dict(os.environ, {"ELM_ROOT": str(expected)}):
                resolved = cli.resolve_root(None)

        self.assertEqual(expected, resolved)

    def test_config_pointer_supports_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-config-") as temporary:
            config_home = Path(temporary)
            memory = config_home / "memory"
            memory.mkdir()
            pointer = config_home / "root"
            pointer.write_text("memory\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(cli, "CONFIG_POINTER", pointer):
                resolved = cli.resolve_root(None)

        self.assertEqual(memory.resolve(), resolved)


class PublicCliContractTests(unittest.TestCase):
    def test_module_help_lists_v0_commands(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-m", "elm_memory", "--help"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        for command in ("sync", "rebuild", "search", "outline", "read", "related", "stats", "doctor"):
            self.assertIn(command, completed.stdout)
        self.assertIn("ids", completed.stdout)

    def test_package_exposes_pre_release_version(self) -> None:
        self.assertEqual("0.2.0.dev0", elm_memory.__version__)

    def test_public_engine_has_no_machine_specific_default_root(self) -> None:
        source = (SOURCE_ROOT / "elm_memory" / "cli.py").read_text(encoding="utf-8")
        private_prefix = "C:" + "\\" + "Users" + "\\"

        self.assertNotIn(private_prefix, source)
        self.assertNotIn("local_" + "Info_codex", source)


if __name__ == "__main__":
    unittest.main()
