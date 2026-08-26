from __future__ import annotations

import re
from pathlib import Path
import tomllib
import unittest

from _bootstrap import REPOSITORY_ROOT


EXCLUDED_PARTS = {".git", ".venv", ".elm", "dist", "build", "__pycache__"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".yml", ".yaml", ".txt"}


def public_text_files() -> list[Path]:
    return [
        path
        for path in REPOSITORY_ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and path.name != "benchmark-report.json"
        and not (set(path.relative_to(REPOSITORY_ROOT).parts) & EXCLUDED_PARTS)
        and not any(part.endswith(".egg-info") for part in path.relative_to(REPOSITORY_ROOT).parts)
    ]


class RepositoryHygieneTests(unittest.TestCase):
    def test_public_text_is_valid_utf8(self) -> None:
        for path in public_text_files():
            with self.subTest(path=path.relative_to(REPOSITORY_ROOT)):
                path.read_text(encoding="utf-8")

    def test_no_generated_sqlite_is_part_of_the_source_tree(self) -> None:
        generated = [
            path
            for path in REPOSITORY_ROOT.rglob("*.sqlite*")
            if not (set(path.relative_to(REPOSITORY_ROOT).parts) & EXCLUDED_PARTS)
        ]

        self.assertEqual([], generated)

    def test_no_private_machine_paths_or_common_secret_shapes(self) -> None:
        private_windows_prefix = "C:" + "\\" + "Users" + "\\"
        private_memory_name = "local_" + "Info_codex"
        private_skill_path = ".co" + "dex"
        secret_patterns = (
            re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
            re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
            re.compile(r"AKIA[0-9A-Z]{16}"),
            re.compile(r"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
        )
        failures: list[str] = []
        for path in public_text_files():
            text = path.read_text(encoding="utf-8")
            if private_windows_prefix in text or private_memory_name in text or private_skill_path in text:
                failures.append(str(path.relative_to(REPOSITORY_ROOT)))
                continue
            if any(pattern.search(text) for pattern in secret_patterns):
                failures.append(str(path.relative_to(REPOSITORY_ROOT)))

        self.assertEqual([], failures)

    def test_readme_relative_markdown_links_resolve(self) -> None:
        readme = REPOSITORY_ROOT / "README.md"
        links = re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", readme.read_text(encoding="utf-8"))
        missing = []
        for target in links:
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = target.split("#", 1)[0]
            if path_part and not (readme.parent / path_part).is_file():
                missing.append(target)

        self.assertEqual([], missing)

    def test_apache_license_is_declared_and_packaged(self) -> None:
        configuration = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")
        notice_text = (REPOSITORY_ROOT / "NOTICE").read_text(encoding="utf-8")

        self.assertEqual("Apache-2.0", configuration["project"]["license"])
        self.assertEqual(["LICENSE", "NOTICE"], configuration["project"]["license-files"])
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("ELM contributors", notice_text)


if __name__ == "__main__":
    unittest.main()
