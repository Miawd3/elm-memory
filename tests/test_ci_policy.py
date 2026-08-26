from __future__ import annotations

import re
import unittest

from _bootstrap import REPOSITORY_ROOT


WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


class GithubActionsPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_only_unprivileged_triggers(self) -> None:
        for trigger in ("pull_request_target", "workflow_run", "issue_comment", "issues:"):
            self.assertNotIn(trigger, self.workflow)
        self.assertIn("pull_request:", self.workflow)

    def test_token_is_read_only(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("write-all", self.workflow)
        self.assertNotRegex(self.workflow, r"(?m)^\s+[a-z-]+:\s+write\s*$")

    def test_every_action_is_pinned_to_a_full_commit_sha(self) -> None:
        references = re.findall(r"(?m)^\s*uses:\s*[^@\s]+@([^\s#]+)", self.workflow)

        self.assertTrue(references)
        for reference in references:
            self.assertRegex(reference, r"^[0-9a-f]{40}$")

    def test_checkout_does_not_persist_credentials(self) -> None:
        checkout_count = self.workflow.count("uses: actions/checkout@")

        self.assertEqual(checkout_count, self.workflow.count("persist-credentials: false"))

    def test_no_secrets_or_self_hosted_runners(self) -> None:
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("self-hosted", self.workflow)

    def test_shell_commands_do_not_interpolate_workflow_expressions(self) -> None:
        run_lines = [line for line in self.workflow.splitlines() if re.match(r"^\s+run:\s", line)]

        self.assertTrue(run_lines)
        self.assertTrue(all("${{" not in line for line in run_lines))


if __name__ == "__main__":
    unittest.main()
