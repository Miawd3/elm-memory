from __future__ import annotations

import unittest

from _bootstrap import SOURCE_ROOT  # noqa: F401
from elm_memory.cli import (
    extract_links,
    is_archive_path,
    parse_metadata,
    parse_sections,
    safe_fts_query,
)


class MetadataTests(unittest.TestCase):
    def test_parses_supported_metadata_without_treating_body_as_metadata(self) -> None:
        lines = [
            "Title: Example\n",
            "Scope: Parser fixture\n",
            "Tags: alpha, beta\n",
            "Last updated: 2026-08-25\n",
            "Status: active\n",
            "Summary: Deterministic metadata.\n",
            "\n",
            "# Heading\n",
            "Body: this is section content.\n",
        ]
        metadata, end = parse_metadata(lines)

        self.assertEqual("Example", metadata["title"])
        self.assertEqual("alpha, beta", metadata["tags"])
        self.assertNotIn("body", metadata)
        self.assertEqual(7, end)

    def test_builds_preamble_and_nested_heading_paths(self) -> None:
        lines = [
            "Title: Example\n",
            "\n",
            "Preamble text.\n",
            "# Parent\n",
            "Parent body.\n",
            "## Child\n",
            "Child body.\n",
        ]
        sections = parse_sections(lines, metadata_end=2)

        self.assertEqual("[preamble]", sections[0]["heading"])
        self.assertEqual("Parent", sections[1]["heading_path"])
        self.assertEqual("Parent > Child", sections[2]["heading_path"])
        self.assertEqual(1, sections[2]["parent_ordinal"])


class RetrievalSafetyTests(unittest.TestCase):
    def test_fts_query_quotes_operator_shaped_input(self) -> None:
        self.assertEqual('"alpha" "OR" "beta"', safe_fts_query('alpha OR "beta"'))
        self.assertEqual('"alpha" OR "beta"', safe_fts_query("alpha beta", broad=True))

    def test_archive_classification_covers_directories_and_backup_names(self) -> None:
        self.assertTrue(is_archive_path("20_projects/demo/backups/STATE.md"))
        self.assertTrue(is_archive_path("99_archive/STATE.md"))
        self.assertTrue(is_archive_path("20_projects/demo/STATE.md.bak_20260825"))
        self.assertFalse(is_archive_path("20_projects/demo/STATE.md"))

    def test_external_links_are_not_indexed_as_local_edges(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory(prefix="elm-links-") as temporary:
            root = Path(temporary).resolve()
            source = root / "PROJECT.md"
            source.write_text("", encoding="utf-8")
            links = extract_links(
                root,
                source,
                "[web](https://example.com) [escape](../outside.md) [local](LOCAL.md)",
                {},
            )

        self.assertEqual([("LOCAL.md", "local", "markdown_link")], links)


if __name__ == "__main__":
    unittest.main()
