from __future__ import annotations

import unittest

from _bootstrap import FixtureCopy, run_cli, run_cli_process


class ReadPolicyTests(unittest.TestCase):
    def test_archive_section_cannot_be_read_by_guessed_numeric_or_stable_id(self) -> None:
        with FixtureCopy() as root:
            historical = run_cli(root, "search", "Mercury Borealis", "--include-archive")
            selected = historical["results"][0]
            numeric = run_cli_process(root, "read", str(selected["section_id"]))
            stable = run_cli_process(root, "read", selected["section_key"])
            allowed = run_cli(root, "read", selected["section_key"], "--include-archive")

        self.assertNotEqual(0, numeric.returncode)
        self.assertNotEqual(0, stable.returncode)
        self.assertNotIn("Traceback", numeric.stderr + stable.stderr)
        self.assertEqual(selected["section_key"], allowed["section_key"])

    def test_project_policy_applies_to_outline_read_and_related(self) -> None:
        with FixtureCopy() as root:
            selected = run_cli(root, "search", "Aurora gateway PostgreSQL")["results"][0]
            outline = run_cli_process(
                root, "outline", str(selected["document_id"]), "--project", "lighthouse", "--no-sync"
            )
            read = run_cli_process(
                root, "read", str(selected["section_id"]), "--project", "lighthouse"
            )
            related = run_cli_process(
                root, "related", str(selected["document_id"]), "--project", "lighthouse", "--no-sync"
            )

        self.assertTrue(all(item.returncode != 0 for item in (outline, read, related)))
        self.assertNotIn("Traceback", outline.stderr + read.stderr + related.stderr)

    def test_related_does_not_expose_archive_target_path_by_default(self) -> None:
        with FixtureCopy() as root:
            hub = root / "20_projects" / "orion" / "PROJECT_HUB.md"
            hub.write_text(
                hub.read_text(encoding="utf-8")
                + "\n[historical copy](backups/PROJECT_HUB.old.md)\n",
                encoding="utf-8",
            )
            ordinary = run_cli(root, "related", "20_projects/orion/PROJECT_HUB.md")
            historical = run_cli(
                root,
                "related",
                "20_projects/orion/PROJECT_HUB.md",
                "--include-archive",
                "--no-sync",
            )

        ordinary_paths = {item["target_path"] for item in ordinary["outgoing"]}
        historical_paths = {item["target_path"] for item in historical["outgoing"]}
        self.assertNotIn("20_projects/orion/backups/PROJECT_HUB.old.md", ordinary_paths)
        self.assertIn("20_projects/orion/backups/PROJECT_HUB.old.md", historical_paths)


if __name__ == "__main__":
    unittest.main()
