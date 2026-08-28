from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
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
    def test_module_help_lists_public_commands(self) -> None:
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

        for command in (
            "sync",
            "rebuild",
            "search",
            "context",
            "outline",
            "read",
            "related",
            "stats",
            "status",
            "doctor",
            "traces",
            "evidence",
            "propose",
            "proposals",
            "accept",
            "reject",
            "defer",
            "dispute",
            "supersede",
            "delete",
            "history",
            "recover",
            "root-id",
            "proposal-submit",
            "remember-submit",
            "proposal-preview",
        ):
            self.assertIn(command, completed.stdout)
        self.assertIn("ids", completed.stdout)

    def test_package_exposes_pre_release_version(self) -> None:
        self.assertEqual("0.7.0.dev0", elm_memory.__version__)

    def test_json_output_is_utf8_under_a_legacy_process_encoding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-unicode-") as temporary:
            root = Path(temporary) / "memory"
            shutil.copytree(REPOSITORY_ROOT / "tests" / "fixtures" / "sample_elm", root)
            target = root / "20_projects" / "orion" / "ACTIVE_CONTEXT.md"
            target.write_text(
                target.read_text(encoding="utf-8")
                + "\n# Unicode probe\n\nПамять работает.\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            environment["PYTHONIOENCODING"] = "cp1252"
            environment["PYTHONUTF8"] = "0"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "elm_memory",
                    "search",
                    "Unicode probe",
                    "--root",
                    str(root),
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8"))
        payload = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(1, payload["count"])
        self.assertIn("Память работает", payload["results"][0]["snippet"])

    def test_public_engine_has_no_machine_specific_default_root(self) -> None:
        source = (SOURCE_ROOT / "elm_memory" / "cli.py").read_text(encoding="utf-8")
        private_prefix = "C:" + "\\" + "Users" + "\\"

        self.assertNotIn(private_prefix, source)
        self.assertNotIn("local_" + "Info_codex", source)


if __name__ == "__main__":
    unittest.main()
