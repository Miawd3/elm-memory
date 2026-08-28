from __future__ import annotations

import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from _bootstrap import REPOSITORY_ROOT


MODULE_PATH = REPOSITORY_ROOT / "scripts" / "run_release_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_release_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules["run_release_acceptance"] = HARNESS
SPEC.loader.exec_module(HARNESS)


class WheelValidationTests(unittest.TestCase):
    def test_valid_wheel_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "elm_memory-0.10.0.dev0-py3-none-any.whl"
            wheel.write_bytes(b"dummy")
            path, info = HARNESS.validate_wheel_path(wheel)
            self.assertEqual(wheel.resolve(), path)
            self.assertEqual("elm_memory", info["distribution"])
            self.assertEqual("0.10.0.dev0", info["version"])
            self.assertEqual("py3", info["python_tag"])
            self.assertEqual("none", info["abi_tag"])
            self.assertEqual("any", info["platform_tag"])

    def test_wheel_with_hyphenated_distribution_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "elm-memory-0.10.0.dev0-py3-none-any.whl"
            wheel.write_bytes(b"dummy")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_wheel_path(wheel)
            self.assertIn("does not conform to PEP 427", str(ctx.exception))

    def test_missing_wheel_file_fails(self) -> None:
        missing = Path("nonexistent") / "elm_memory-0.10.0.dev0-py3-none-any.whl"
        with self.assertRaises(FileNotFoundError):
            HARNESS.validate_wheel_path(missing)

    def test_non_wheel_extension_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tarball = Path(temp_dir) / "elm_memory-0.10.0.dev0.tar.gz"
            tarball.write_bytes(b"dummy")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_wheel_path(tarball)
            self.assertIn("not a wheel archive", str(ctx.exception))

    def test_wrong_distribution_name_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wrong = Path(temp_dir) / "other_pkg-0.10.0.dev0-py3-none-any.whl"
            wrong.write_bytes(b"dummy")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_wheel_path(wrong)
            self.assertIn("PEP 427 requires normalized 'elm_memory'", str(ctx.exception))

    def test_unparseable_wheel_filename_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unparseable = Path(temp_dir) / "invalid_wheel_format.whl"
            unparseable.write_bytes(b"dummy")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_wheel_path(unparseable)
            self.assertIn("does not conform to PEP 427", str(ctx.exception))


class VirtualenvPyzValidationTests(unittest.TestCase):
    def test_valid_pyz_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pyz = Path(temp_dir) / "virtualenv-21.7.4.pyz"
            pyz.write_bytes(b"PK\x03\x04dummy_zipapp")
            validated = HARNESS.validate_virtualenv_pyz_path(pyz)
            self.assertEqual(pyz.resolve(), validated)

    def test_missing_pyz_fails(self) -> None:
        missing = Path("nonexistent") / "virtualenv-21.7.4.pyz"
        with self.assertRaises(FileNotFoundError):
            HARNESS.validate_virtualenv_pyz_path(missing)

    def test_wrong_extension_pyz_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wrong = Path(temp_dir) / "virtualenv-21.7.4.zip"
            wrong.write_bytes(b"dummy")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_virtualenv_pyz_path(wrong)
            self.assertIn("must have .pyz extension", str(ctx.exception))

    def test_symlink_pyz_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            real_file = Path(temp_dir) / "real.pyz"
            real_file.write_bytes(b"dummy")
            link_file = Path(temp_dir) / "link.pyz"
            try:
                link_file.symlink_to(real_file)
            except (OSError, NotImplementedError):
                self.skipTest("Filesystem does not support symlinks")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_virtualenv_pyz_path(link_file)
            self.assertIn("must not be a symlink", str(ctx.exception))


class VirtualenvCreationTests(unittest.TestCase):
    @patch("run_release_acceptance.locate_venv_executables")
    @patch("run_release_acceptance.run_command")
    def test_create_venv_standard_invocation(
        self,
        mock_run: MagicMock,
        mock_locate: MagicMock,
    ) -> None:
        mock_run.return_value = (0, "", "")
        mock_python = MagicMock()
        mock_python.is_file.return_value = True
        mock_locate.return_value = {"python": mock_python}

        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            executables = HARNESS.create_isolated_venv(venv_dir, virtualenv_pyz=None)
            self.assertIn("python", executables)
            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            self.assertEqual([sys.executable, "-m", "venv", str(venv_dir)], called_cmd)

    @patch("run_release_acceptance.locate_venv_executables")
    @patch("run_release_acceptance.run_command")
    def test_create_venv_zipapp_invocation_with_no_download(
        self,
        mock_run: MagicMock,
        mock_locate: MagicMock,
    ) -> None:
        mock_run.return_value = (0, "", "")
        mock_python = MagicMock()
        mock_python.is_file.return_value = True
        mock_locate.return_value = {"python": mock_python}

        with tempfile.TemporaryDirectory() as temp_dir:
            pyz = Path(temp_dir) / "virtualenv-21.7.4.pyz"
            pyz.write_bytes(b"dummy")
            venv_dir = Path(temp_dir) / "venv"

            executables = HARNESS.create_isolated_venv(venv_dir, virtualenv_pyz=pyz)
            self.assertIn("python", executables)
            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            expected_cmd = [sys.executable, str(pyz.resolve()), "--no-download", str(venv_dir)]
            self.assertEqual(expected_cmd, called_cmd)


class FixtureValidationTests(unittest.TestCase):
    def test_valid_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fixture"
            root.mkdir()
            (root / "README.md").write_text("# Test\n", encoding="utf-8")
            validated = HARNESS.validate_fixture_root(root)
            self.assertEqual(root, validated)

    def test_fixture_with_elm_runtime_state_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fixture"
            root.mkdir()
            (root / "README.md").write_text("# Test\n", encoding="utf-8")
            elm_dir = root / ".elm"
            elm_dir.mkdir()
            (elm_dir / "index.sqlite").write_bytes(b"fake sqlite")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_fixture_root(root)
            self.assertIn(".elm runtime state", str(ctx.exception))

    def test_fixture_without_markdown_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fixture"
            root.mkdir()
            (root / "notes.txt").write_text("plain text\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_fixture_root(root)
            self.assertIn("contains no Markdown files", str(ctx.exception))

    def test_fixture_missing_directory_rejected(self) -> None:
        missing = Path("nonexistent") / "fixture"
        with self.assertRaises(FileNotFoundError):
            HARNESS.validate_fixture_root(missing)

    def test_fixture_with_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fixture"
            root.mkdir()
            target = root / "target.md"
            target.write_text("# Target\n", encoding="utf-8")
            link = root / "link.md"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Filesystem does not support symlinks")
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_fixture_root(root)
            self.assertIn("forbidden symlink", str(ctx.exception))


class MarkdownDriftTests(unittest.TestCase):
    def test_canonical_markdown_hashing_and_drift_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "memory"
            root.mkdir()
            file1 = root / "DOC1.md"
            file2 = root / "sub" / "DOC2.md"
            file2.parent.mkdir()
            file1.write_text("# Doc 1\n", encoding="utf-8")
            file2.write_text("# Doc 2\n", encoding="utf-8")

            baseline = HARNESS.hash_canonical_markdown(root)
            self.assertIn("DOC1.md", baseline)
            self.assertIn("sub/DOC2.md", baseline)

            # Unchanged check passes
            HARNESS.assert_markdown_unchanged(root, baseline, "test_clean")

            # Modification detected
            file1.write_text("# Doc 1 Modified\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.assert_markdown_unchanged(root, baseline, "test_modified")
            self.assertIn("modified: ['DOC1.md']", str(ctx.exception))

            # Revert modification
            file1.write_text("# Doc 1\n", encoding="utf-8")
            HARNESS.assert_markdown_unchanged(root, baseline, "test_reverted")

            # Addition detected
            file3 = root / "DOC3.md"
            file3.write_text("# Doc 3\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.assert_markdown_unchanged(root, baseline, "test_added")
            self.assertIn("added: ['DOC3.md']", str(ctx.exception))

            # Removal detected
            file3.unlink()
            file1.unlink()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.assert_markdown_unchanged(root, baseline, "test_removed")
            self.assertIn("removed: ['DOC1.md']", str(ctx.exception))


class EntryPointContainmentTests(unittest.TestCase):
    def test_windows_entry_point_paths_are_stable_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            with patch.object(HARNESS.sys, "platform", "win32"):
                executables = HARNESS.locate_venv_executables(venv_dir)

            self.assertEqual(venv_dir / "Scripts" / "elm.exe", executables["elm"])
            self.assertEqual(
                venv_dir / "Scripts" / "elm-mcp.exe", executables["elm-mcp"]
            )

    def test_entry_point_inside_venv_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            elm_bin = bin_dir / "elm"
            elm_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            HARNESS.assert_entry_point_containment(elm_bin, venv_dir, "elm")

    def test_missing_entry_point_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            missing_bin = venv_dir / "bin" / "elm"
            with self.assertRaises(FileNotFoundError):
                HARNESS.assert_entry_point_containment(missing_bin, venv_dir, "elm")

    def test_escaped_entry_point_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            venv_dir = Path(temp_dir) / "venv"
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            external_dir = Path(temp_dir) / "external"
            external_dir.mkdir()
            outside_bin = external_dir / "elm"
            outside_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            symlink_bin = bin_dir / "elm"
            try:
                symlink_bin.symlink_to(outside_bin)
            except (OSError, NotImplementedError):
                self.skipTest("Filesystem does not support symlinks")

            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.assert_entry_point_containment(symlink_bin, venv_dir, "elm")
            self.assertIn("escaped the virtual environment boundary", str(ctx.exception))


class CommandAndJsonHandlingTests(unittest.TestCase):
    @patch("run_release_acceptance.run_command")
    def test_memory_validation_closes_sqlite_before_return(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.side_effect = [
            (0, json.dumps({"errors": []}), ""),
            (0, json.dumps({"healthy": True, "issue_count": 0}), ""),
            (0, json.dumps({"count": 1, "results": [{"path": "DOC.md"}]}), ""),
            (0, json.dumps({"estimated_tokens": 100}), ""),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "memory"
            db_file = root / ".elm" / "index.sqlite"
            db_file.parent.mkdir(parents=True)
            con = HARNESS.sqlite3.connect(str(db_file))
            try:
                con.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
                con.execute("CREATE TABLE sections (id INTEGER PRIMARY KEY)")
                con.execute("INSERT INTO documents DEFAULT VALUES")
                con.execute("INSERT INTO sections DEFAULT VALUES")
                con.commit()
            finally:
                con.close()

            counts = HARNESS.validate_memory_operations(Path("elm"), root)
            self.assertEqual({"documents": 1, "sections": 1}, counts)
            db_file.unlink()
            self.assertFalse(db_file.exists())

    @patch("run_release_acceptance.subprocess.run")
    def test_run_command_uses_synthetic_home_inside_cwd(
        self, mock_subprocess_run: MagicMock
    ) -> None:
        mock_subprocess_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            HARNESS.run_command([sys.executable, "--version"], cwd=cwd)

        child_env = mock_subprocess_run.call_args.kwargs["env"]
        self.assertEqual(str(cwd), child_env["HOME"])
        self.assertEqual(str(cwd), child_env["USERPROFILE"])
        self.assertNotEqual(str(Path.home()), child_env["HOME"])

    @patch("run_release_acceptance.run_command")
    def test_rebuild_nonzero_fails(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (1, "", "rebuild error")
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("elm rebuild failed (code 1)", str(ctx.exception))

    @patch("run_release_acceptance.run_command")
    def test_rebuild_malformed_json_fails(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (0, "not json content", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(ValueError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("malformed JSON", str(ctx.exception))

    @patch("run_release_acceptance.run_command")
    def test_rebuild_reported_errors_fails(self, mock_run: MagicMock) -> None:
        mock_run.return_value = (0, json.dumps({"errors": [{"path": "bad.md", "error": "fail"}]}), "")
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("index errors", str(ctx.exception))

    @patch("run_release_acceptance.run_command")
    def test_doctor_unhealthy_fails(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            (0, json.dumps({"errors": []}), ""),  # rebuild
            (0, json.dumps({"healthy": False, "issue_count": 2}), ""),  # doctor
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("unhealthy memory state", str(ctx.exception))

    @patch("run_release_acceptance.run_command")
    def test_search_zero_matches_fails(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            (0, json.dumps({"errors": []}), ""),  # rebuild
            (0, json.dumps({"healthy": True, "issue_count": 0}), ""),  # doctor
            (0, json.dumps({"count": 0, "results": []}), ""),  # search
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("failed to recover known synthetic PostgreSQL fact", str(ctx.exception))

    @patch("run_release_acceptance.run_command")
    def test_context_budget_violation_fails(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            (0, json.dumps({"errors": []}), ""),  # rebuild
            (0, json.dumps({"healthy": True, "issue_count": 0}), ""),  # doctor
            (0, json.dumps({"count": 1, "results": [{"path": "test.md"}]}), ""),  # search
            (0, json.dumps({"estimated_tokens": 850}), ""),  # context over 700 budget
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            elm_bin = Path(temp_dir) / "elm"
            root = Path(temp_dir) / "memory"
            root.mkdir()
            with self.assertRaises(RuntimeError) as ctx:
                HARNESS.validate_memory_operations(elm_bin, root)
            self.assertIn("violated budget limit (700)", str(ctx.exception))


class ReportSanitizationTests(unittest.TestCase):
    def test_sanitized_report_keys_and_leakage(self) -> None:
        report = HARNESS.build_sanitized_report(
            status="passed",
            mode="candidate_only",
            candidate_version="0.10.0.dev0",
            previous_version=None,
            markdown_documents=7,
            documents_indexed=7,
            sections_indexed=18,
            transitions_verified=["stage1", "stage2"],
            error_message=None,
        )
        serialized = json.dumps(report)
        expected_keys = {
            "status",
            "mode",
            "candidate_version",
            "previous_version",
            "markdown_documents",
            "documents_indexed",
            "sections_indexed",
            "transitions_verified",
        }
        self.assertEqual(expected_keys, set(report.keys()))
        self.assertEqual("passed", report["status"])
        self.assertNotIn("error", report)

        # Confirm no home path, env vars, or session tokens in serialized report
        self.assertNotIn(str(Path.home()), serialized)
        self.assertNotIn("session_id", serialized)
        self.assertNotIn("token", serialized)


class ErrorSanitizationTests(unittest.TestCase):
    def test_sanitize_error_redacts_posix_paths(self) -> None:
        exc = RuntimeError("Failure in /tmp/elm-release-acceptance-xyz/memory/index.sqlite")
        sanitized = HARNESS.sanitize_error_message(exc)
        self.assertNotIn("/tmp", sanitized)
        self.assertNotIn("elm-release-acceptance-xyz", sanitized)
        self.assertIn("<path>", sanitized)

    def test_sanitize_error_redacts_windows_paths(self) -> None:
        private_prefix = "C:" + "\\" + "Users" + "\\"
        exc = RuntimeError(
            "Failure in " + private_prefix + r"runner\AppData\Local\Temp\wheel.whl"
        )
        sanitized = HARNESS.sanitize_error_message(exc)
        self.assertNotIn("Users", sanitized)
        self.assertNotIn(private_prefix, sanitized)
        self.assertIn("<path>", sanitized)

    def test_sanitize_error_redacts_home_and_temp_dirs(self) -> None:
        home_path = str(Path.home())
        exc = RuntimeError(f"Error reading from {home_path}/secret_file.md")
        sanitized = HARNESS.sanitize_error_message(exc)
        self.assertNotIn(home_path, sanitized)

    def test_sanitize_error_redacts_wheel_names(self) -> None:
        exc = RuntimeError("pip install failed for wheel elm_memory-0.10.0.dev0-py3-none-any.whl (code 1)")
        sanitized = HARNESS.sanitize_error_message(exc)
        self.assertNotIn("elm_memory-0.10.0.dev0-py3-none-any.whl", sanitized)
        self.assertIn("<wheel>", sanitized)

    def test_sanitize_error_redacts_pyz_paths(self) -> None:
        exc = RuntimeError("Failure in /opt/seeds/virtualenv-21.7.4.pyz")
        sanitized = HARNESS.sanitize_error_message(exc)
        self.assertNotIn("/opt/seeds", sanitized)
        self.assertNotIn("virtualenv-21.7.4.pyz", sanitized)
        self.assertTrue("<path>" in sanitized or "<virtualenv_pyz>" in sanitized)

    def test_main_failure_output_has_no_paths_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_wheel = temp_path / "bad.whl"
            fake_wheel.write_bytes(b"bad")
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = HARNESS.main(["--candidate-wheel", str(fake_wheel), "--json"])
            self.assertEqual(1, exit_code)
            output = stdout_buf.getvalue()
            data = json.loads(output)
            self.assertEqual("failed", data["status"])
            self.assertIn("error", data)
            # Prove absolute temp path is absent from report
            self.assertNotIn(str(fake_wheel), output)
            self.assertNotIn(str(temp_path), output)

    def test_main_failure_output_has_no_paths_in_human_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_wheel = temp_path / "bad.whl"
            fake_wheel.write_bytes(b"bad")
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
                exit_code = HARNESS.main(["--candidate-wheel", str(fake_wheel)])
            self.assertEqual(1, exit_code)
            stderr_output = stderr_buf.getvalue()
            self.assertIn("FAIL:", stderr_output)
            # Prove absolute temp path is absent from human error message
            self.assertNotIn(str(fake_wheel), stderr_output)
            self.assertNotIn(str(temp_path), stderr_output)


class PipelineOrchestrationTests(unittest.TestCase):
    @patch("run_release_acceptance.create_isolated_venv")
    @patch("run_release_acceptance.pip_install_wheel")
    @patch("run_release_acceptance.verify_installed_distribution")
    @patch("run_release_acceptance.assert_entry_point_containment")
    @patch("run_release_acceptance.validate_memory_operations")
    @patch("run_release_acceptance.exercise_disposable_index")
    @patch("run_release_acceptance.pip_uninstall_package")
    @patch("run_release_acceptance.verify_import_fails")
    def test_candidate_only_pipeline_orchestration(
        self,
        mock_import_fails: MagicMock,
        mock_uninstall: MagicMock,
        mock_disposable: MagicMock,
        mock_validate: MagicMock,
        mock_containment: MagicMock,
        mock_version: MagicMock,
        mock_install: MagicMock,
        mock_venv: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wheel = temp_path / "elm_memory-0.10.0.dev0-py3-none-any.whl"
            wheel.write_bytes(b"dummy")

            fixture = temp_path / "fixture"
            fixture.mkdir()
            (fixture / "DOC.md").write_text("# Doc\n", encoding="utf-8")

            fake_bin = temp_path / "venv" / "bin"
            fake_bin.mkdir(parents=True)
            python_path = fake_bin / "python"
            python_path.write_bytes(b"")
            elm_path = fake_bin / "elm"
            elm_path.write_bytes(b"")
            elm_mcp_path = fake_bin / "elm-mcp"
            elm_mcp_path.write_bytes(b"")

            mock_venv.return_value = {
                "python": python_path,
                "pip": fake_bin / "pip",
                "elm": elm_path,
                "elm-mcp": elm_mcp_path,
            }
            mock_validate.return_value = {"documents": 5, "sections": 12}

            report = HARNESS.run_acceptance_pipeline(
                candidate_wheel=wheel,
                previous_wheel=None,
                fixture_root=fixture,
            )

            self.assertEqual("passed", report["status"])
            self.assertEqual("candidate_only", report["mode"])
            self.assertEqual("0.10.0.dev0", report["candidate_version"])
            self.assertIsNone(report["previous_version"])
            self.assertEqual(5, report["documents_indexed"])
            self.assertEqual(12, report["sections_indexed"])

            expected_transitions = [
                "fixture_validated",
                "synthetic_memory_initialized",
                "isolated_venv_created",
                "candidate_installed",
                "entry_points_verified",
                "memory_operations_validated",
                "disposable_index_rebuilt",
                "candidate_uninstalled",
            ]
            self.assertEqual(expected_transitions, report["transitions_verified"])

            mock_install.assert_called_once()
            mock_uninstall.assert_called_once()
            mock_import_fails.assert_called_once()

    @patch("run_release_acceptance.create_isolated_venv")
    @patch("run_release_acceptance.pip_install_wheel")
    @patch("run_release_acceptance.verify_installed_distribution")
    @patch("run_release_acceptance.assert_entry_point_containment")
    @patch("run_release_acceptance.validate_memory_operations")
    @patch("run_release_acceptance.exercise_disposable_index")
    def test_rollback_pipeline_orchestration(
        self,
        mock_disposable: MagicMock,
        mock_validate: MagicMock,
        mock_containment: MagicMock,
        mock_version: MagicMock,
        mock_install: MagicMock,
        mock_venv: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cand_wheel = temp_path / "elm_memory-0.10.0.dev0-py3-none-any.whl"
            cand_wheel.write_bytes(b"dummy")
            prev_wheel = temp_path / "elm_memory-0.9.0-py3-none-any.whl"
            prev_wheel.write_bytes(b"dummy")

            fixture = temp_path / "fixture"
            fixture.mkdir()
            (fixture / "DOC.md").write_text("# Doc\n", encoding="utf-8")

            fake_bin = temp_path / "venv" / "bin"
            fake_bin.mkdir(parents=True)
            python_path = fake_bin / "python"
            python_path.write_bytes(b"")
            elm_path = fake_bin / "elm"
            elm_path.write_bytes(b"")
            elm_mcp_path = fake_bin / "elm-mcp"
            elm_mcp_path.write_bytes(b"")

            mock_venv.return_value = {
                "python": python_path,
                "pip": fake_bin / "pip",
                "elm": elm_path,
                "elm-mcp": elm_mcp_path,
            }
            mock_validate.side_effect = [
                {"documents": 4, "sections": 10},  # previous install
                {"documents": 5, "sections": 12},  # candidate upgrade
            ]

            report = HARNESS.run_acceptance_pipeline(
                candidate_wheel=cand_wheel,
                previous_wheel=prev_wheel,
                fixture_root=fixture,
            )

            self.assertEqual("passed", report["status"])
            self.assertEqual("rollback", report["mode"])
            self.assertEqual("0.10.0.dev0", report["candidate_version"])
            self.assertEqual("0.9.0", report["previous_version"])
            self.assertEqual(5, report["documents_indexed"])
            self.assertEqual(12, report["sections_indexed"])

            expected_transitions = [
                "fixture_validated",
                "synthetic_memory_initialized",
                "isolated_venv_created",
                "previous_installed",
                "previous_entry_points_verified",
                "previous_memory_validated",
                "candidate_upgraded",
                "candidate_memory_validated",
                "previous_rolled_back",
                "rolled_back_index_rebuilt",
            ]
            self.assertEqual(expected_transitions, report["transitions_verified"])

            # Verify pip_install_wheel called 3 times: prev -> cand -> prev
            self.assertEqual(3, mock_install.call_count)

    @patch("run_release_acceptance.create_isolated_venv")
    @patch("run_release_acceptance.pip_install_wheel")
    @patch("run_release_acceptance.verify_installed_distribution")
    @patch("run_release_acceptance.assert_entry_point_containment")
    @patch("run_release_acceptance.validate_memory_operations")
    @patch("run_release_acceptance.exercise_disposable_index")
    @patch("run_release_acceptance.pip_uninstall_package")
    @patch("run_release_acceptance.verify_import_fails")
    def test_pipeline_forwards_virtualenv_pyz(
        self,
        mock_import_fails: MagicMock,
        mock_uninstall: MagicMock,
        mock_disposable: MagicMock,
        mock_validate: MagicMock,
        mock_containment: MagicMock,
        mock_version: MagicMock,
        mock_install: MagicMock,
        mock_venv: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wheel = temp_path / "elm_memory-0.10.0.dev0-py3-none-any.whl"
            wheel.write_bytes(b"dummy")
            pyz = temp_path / "virtualenv-21.7.4.pyz"
            pyz.write_bytes(b"dummy")

            fixture = temp_path / "fixture"
            fixture.mkdir()
            (fixture / "DOC.md").write_text("# Doc\n", encoding="utf-8")

            fake_bin = temp_path / "venv" / "bin"
            fake_bin.mkdir(parents=True)
            python_path = fake_bin / "python"
            python_path.write_bytes(b"")
            elm_path = fake_bin / "elm"
            elm_path.write_bytes(b"")
            elm_mcp_path = fake_bin / "elm-mcp"
            elm_mcp_path.write_bytes(b"")

            mock_venv.return_value = {
                "python": python_path,
                "pip": fake_bin / "pip",
                "elm": elm_path,
                "elm-mcp": elm_mcp_path,
            }
            mock_validate.return_value = {"documents": 5, "sections": 12}

            report = HARNESS.run_acceptance_pipeline(
                candidate_wheel=wheel,
                previous_wheel=None,
                fixture_root=fixture,
                virtualenv_pyz=pyz,
            )

            self.assertEqual("passed", report["status"])
            mock_venv.assert_called_once_with(
                mock_venv.call_args[0][0],
                virtualenv_pyz=pyz.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
