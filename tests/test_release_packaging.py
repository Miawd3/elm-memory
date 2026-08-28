from __future__ import annotations

import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from _bootstrap import REPOSITORY_ROOT
import elm_memory


SPEC = importlib.util.spec_from_file_location(
    "build_release",
    REPOSITORY_ROOT / "scripts" / "build_release.py",
)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class ReleasePackagingTests(unittest.TestCase):
    def test_release_version_is_consistent(self) -> None:
        self.assertEqual(elm_memory.__version__, BUILDER.project_version())

    def test_skill_zip_is_deterministic_and_has_one_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-skill-zip-") as temporary:
            temporary_path = Path(temporary)
            first = temporary_path / "first.zip"
            second = temporary_path / "second.zip"
            source = REPOSITORY_ROOT / "skills" / "elm-memory-operator"
            BUILDER.write_zip(first, source, "elm-memory-operator")
            BUILDER.write_zip(second, source, "elm-memory-operator")
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(names)
            self.assertTrue(all(name.startswith("elm-memory-operator/") for name in names))
            self.assertIn("elm-memory-operator/SKILL.md", names)

    def test_linux_archive_preserves_executable_installer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="elm-linux-archive-") as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "source"
            source.mkdir()
            installer = source / "install.sh"
            installer.write_text("#!/bin/sh\n", encoding="utf-8")
            archive = temporary_path / "bundle.tar.gz"
            BUILDER.write_tar_gz(archive, source, "bundle")
            with tarfile.open(archive, "r:gz") as package:
                member = package.getmember("bundle/install.sh")

            self.assertEqual(0o755, member.mode)
            self.assertEqual(0, member.mtime)

    def test_installers_do_not_own_memory_roots(self) -> None:
        linux = (REPOSITORY_ROOT / "packaging" / "linux" / "install.sh").read_text(encoding="utf-8")
        windows = (REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")

        self.assertIn("Memory roots are never removed", linux)
        self.assertNotIn(".elm-system", linux)
        self.assertIn('venv "$FINAL_DIR"', linux)
        self.assertIn("COMPLETE_MARKER", linux)
        self.assertNotIn("STAGE_DIR", linux)
        self.assertIn("PrivilegesRequired=lowest", windows)
        self.assertNotIn("runascurrentuser", windows.casefold())


if __name__ == "__main__":
    unittest.main()
