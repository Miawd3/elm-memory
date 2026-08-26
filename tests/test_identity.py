from __future__ import annotations

import unittest
import uuid

from _bootstrap import SOURCE_ROOT  # noqa: F401
from elm_memory.identity import derive_section_key, validate_document_uid


class StableIdentityTests(unittest.TestCase):
    def test_document_bound_section_key_survives_path_movement(self) -> None:
        document_uid = "doc_" + str(uuid.uuid4())
        before = derive_section_key(document_uid, "old/PROJECT.md", "Decision", 0)
        after = derive_section_key(document_uid, "new/PROJECT.md", "Decision", 0)

        self.assertEqual(before, after)
        self.assertEqual("document_uid", before[1])

    def test_path_bound_section_key_changes_when_document_moves(self) -> None:
        before = derive_section_key(None, "old/PROJECT.md", "Decision", 0)
        after = derive_section_key(None, "new/PROJECT.md", "Decision", 0)

        self.assertNotEqual(before[0], after[0])
        self.assertEqual("path", before[1])

    def test_repeated_heading_occurrences_receive_distinct_keys(self) -> None:
        first = derive_section_key(None, "PROJECT.md", "Decision", 0)
        second = derive_section_key(None, "PROJECT.md", "Decision", 1)

        self.assertNotEqual(first[0], second[0])

    def test_document_uid_requires_prefixed_uuid4(self) -> None:
        valid = "doc_" + str(uuid.uuid4())
        self.assertEqual(valid, validate_document_uid(valid.upper()))
        with self.assertRaises(ValueError):
            validate_document_uid("doc_" + str(uuid.uuid5(uuid.NAMESPACE_URL, "elm")))
        with self.assertRaises(ValueError):
            validate_document_uid(str(uuid.uuid4()))


if __name__ == "__main__":
    unittest.main()
