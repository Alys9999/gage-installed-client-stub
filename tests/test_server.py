from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from stub_installed_client_service.server import _authorize, _build_codex_cli_result


class AuthorizeTests(unittest.TestCase):
    def test_authorize_accepts_standard_installed_client_token_envs(self) -> None:
        with patch.dict(os.environ, {"CODEX_CLIENT_TOKEN": "shared-secret"}, clear=True):
            self.assertTrue(_authorize("Bearer shared-secret"))
            self.assertFalse(_authorize("Bearer wrong-secret"))


class BuildCodexCliResultTests(unittest.TestCase):
    def test_submission_contract_patch_is_returned_before_git_diff_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            patch_text = "diff --git a/file.txt b/file.txt\n+patched\n"
            with open(f"{temp_dir}/submission.patch", "w", encoding="utf-8") as handle:
                handle.write(patch_text)

            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="stdout ignored when last message exists",
                stderr="",
            )
            def read_optional_text(path: str) -> str:
                if path.endswith("last_message.txt"):
                    return "final answer"
                with open(path, encoding="utf-8") as handle:
                    return handle.read()

            with patch("stub_installed_client_service.server.subprocess.run", return_value=completed):
                with patch(
                    "stub_installed_client_service.server._read_optional_text",
                    side_effect=read_optional_text,
                ):
                    with patch(
                        "stub_installed_client_service.server._collect_patch",
                        side_effect=AssertionError("git diff fallback should not run"),
                    ):
                        result = _build_codex_cli_result(
                            request={
                                "instruction": "fix it",
                                "cwd": temp_dir,
                                "metadata": {"submission_contract": "submission.patch"},
                            },
                            environment={},
                            codex_executable="codex",
                        )

        self.assertEqual(result["patch_content"], patch_text)
        self.assertEqual(result["answer"], "final answer")

    def test_empty_submission_contract_patch_still_blocks_git_diff_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(f"{temp_dir}/submission.patch", "w", encoding="utf-8") as handle:
                handle.write("")

            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="diff --git a/ignored b/ignored\n+ignored\n",
                stderr="",
            )

            with patch("stub_installed_client_service.server.subprocess.run", return_value=completed):
                with patch(
                    "stub_installed_client_service.server._read_optional_text",
                    side_effect=lambda path: "" if path.endswith("submission.patch") else "final answer",
                ):
                    with patch(
                        "stub_installed_client_service.server._collect_patch",
                        side_effect=AssertionError("git diff fallback should not run"),
                    ):
                        result = _build_codex_cli_result(
                            request={
                                "instruction": "fix it",
                                "cwd": temp_dir,
                                "metadata": {"submission_contract": "submission.patch"},
                            },
                            environment={},
                            codex_executable="codex",
                        )

        self.assertEqual(result["patch_content"], "")
        self.assertEqual(result["answer"], "final answer")

    def test_payload_submission_contract_is_supported_for_gage_workflow_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            patch_text = "diff --git a/fix.go b/fix.go\n+fixed\n"
            with open(f"{temp_dir}/submission.patch", "w", encoding="utf-8") as handle:
                handle.write(patch_text)

            completed = subprocess.CompletedProcess(
                args=["codex", "exec"],
                returncode=0,
                stdout="stdout ignored when last message exists",
                stderr="",
            )

            def read_optional_text(path: str) -> str:
                if path.endswith("last_message.txt"):
                    return "final answer"
                with open(path, encoding="utf-8") as handle:
                    return handle.read()

            with patch("stub_installed_client_service.server.subprocess.run", return_value=completed):
                with patch(
                    "stub_installed_client_service.server._read_optional_text",
                    side_effect=read_optional_text,
                ):
                    with patch(
                        "stub_installed_client_service.server._collect_patch",
                        side_effect=AssertionError("git diff fallback should not run"),
                    ):
                        result = _build_codex_cli_result(
                            request={
                                "instruction": "fix it",
                                "cwd": temp_dir,
                                "metadata": {"benchmark_kit_id": "swebench"},
                                "payload": {"submission_contract": "submission.patch"},
                            },
                            environment={},
                            codex_executable="codex",
                        )

        self.assertEqual(result["patch_content"], patch_text)
        self.assertEqual(result["answer"], "final answer")


if __name__ == "__main__":
    unittest.main()
