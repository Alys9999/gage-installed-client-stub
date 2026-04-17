from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4


def main() -> None:
    """Runs the local Codex proxy service."""

    parser = argparse.ArgumentParser(description="Run the GAGE local Codex proxy service.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8787, help="Bind port.")
    parser.add_argument(
        "--codex-executable",
        default=os.getenv("CODEX_EXECUTABLE", "codex"),
        help="Local codex executable.",
    )
    args = parser.parse_args()

    app_state = _AppState(codex_executable=args.codex_executable)
    if not app_state.codex_available:
        raise SystemExit(
            f"codex executable not found: {args.codex_executable}. "
            "Set CODEX_EXECUTABLE or install codex first."
        )
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(app_state))
    print(
        (
            f"local codex proxy listening on http://{args.host}:{args.port} "
            f"(codex_executable={app_state.codex_executable})"
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _AppState:
    def __init__(self, *, codex_executable: str) -> None:
        self.codex_executable = codex_executable
        self.codex_available = shutil.which(codex_executable) is not None


def _build_handler(app_state: _AppState):
    class StubHandler(BaseHTTPRequestHandler):
        server_version = "GAGEInstalledClientStub/0.2"

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/healthz":
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "codex_available": app_state.codex_available,
                        "codex_executable": app_state.codex_executable,
                    },
                )
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/run":
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not _authorize(self.headers.get("Authorization")):
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": f"invalid_json:{exc}"})
                return
            response = _build_run_response(payload, app_state=app_state)
            request_id = f"stub-{uuid4().hex[:12]}"
            self._write_json(HTTPStatus.OK, response, extra_headers={"X-Request-Id": request_id})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("root payload must be an object")
            return payload

        def _write_json(
            self,
            status: HTTPStatus,
            payload: dict[str, Any],
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return StubHandler


def _authorize(header_value: str | None) -> bool:
    expected = _get_expected_bearer_token()
    if not expected:
        return True
    if not header_value:
        return False
    return header_value.strip() == f"Bearer {expected}"


def _build_run_response(payload: dict[str, Any], *, app_state: _AppState) -> dict[str, Any]:
    request = payload.get("request")
    environment = payload.get("environment")
    if not isinstance(request, dict):
        request = {}
    if not isinstance(environment, dict):
        environment = {}

    result = _build_codex_cli_result(
        request=request,
        environment=environment,
        codex_executable=app_state.codex_executable,
    )
    return {"result": result}


def _build_codex_cli_result(
    *,
    request: dict[str, Any],
    environment: dict[str, Any],
    codex_executable: str,
) -> dict[str, Any]:
    instruction = str(request.get("instruction") or "")
    metadata = request.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    payload = request.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    request_env = request.get("env")
    if not isinstance(request_env, dict):
        request_env = {}

    prompt = _build_prompt_text(instruction=instruction, payload=payload)
    timeout_sec = _coerce_timeout(metadata.get("timeout_sec"), default=1800)
    cwd = str(request.get("cwd") or "").strip()
    host_cwd = cwd if cwd and Path(cwd).is_dir() else None
    cwd_unavailable = cwd if cwd and host_cwd is None else None

    with tempfile.TemporaryDirectory(prefix="gage-codex-proxy-") as temp_dir:
        output_path = str(Path(temp_dir) / "last_message.txt")
        prompt_path = Path(temp_dir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        command = [
            codex_executable,
            "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--output-last-message",
            output_path,
        ]
        env = dict(os.environ)
        env.update({str(key): str(value) for key, value in request_env.items() if key})
        with prompt_path.open(encoding="utf-8") as prompt_file:
            completed = subprocess.run(
                command,
                stdin=prompt_file,
                cwd=host_cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        stdout = _read_optional_text(output_path) or (completed.stdout or "")
        stderr = completed.stderr or ""
        exit_code = int(completed.returncode)
        submission_patch_path = _resolve_submission_contract_patch_path(
            metadata=metadata,
            payload=payload,
            cwd=host_cwd,
        )
        patch_content: str | None
        has_submission_contract = submission_patch_path is not None
        if has_submission_contract and submission_patch_path.exists():
            patch_content = _read_optional_text(str(submission_patch_path))
            if patch_content is None:
                patch_content = ""
        elif not has_submission_contract:
            patch_content = _collect_patch(host_cwd) if host_cwd else None
        else:
            patch_content = None
        if patch_content is None:
            patch_content = _extract_last_diff_block(stdout) or _extract_last_diff_block(stderr)

    command_str = " ".join(_shell_quote(part) for part in command) + " < <prompt_file>"
    answer = stdout.strip()
    trace = [
        {
            "step": 1,
            "kind": "memory",
            "summary": "Loaded installed-client request and runtime context.",
            "benchmark_kit_id": environment.get("benchmark_kit_id"),
        },
        {
            "step": 2,
            "kind": "tool_call",
            "tool_name": "codex_exec",
            "input_summary": {
                "instruction": instruction,
                "cwd": host_cwd,
            },
            "output_summary": {
                "status": "ok" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "answer_preview": answer[:200],
            },
        },
    ]
    trajectory_text = json.dumps(
        {
            "command": command_str,
            "cwd": host_cwd,
            "cwd_unavailable": cwd_unavailable,
            "stdout": stdout,
            "stderr": stderr,
            "trace": trace,
        },
        ensure_ascii=False,
        indent=2,
    )
    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "answer": answer,
        "status": "completed" if exit_code == 0 else "failed",
        "patch_content": patch_content,
        "agent_trace": trace,
        "usage": _extract_usage_payload(stdout=stdout, stderr=stderr),
        "metadata": {
            "provider_name": "local-codex-proxy",
            "benchmark_kit_id": environment.get("benchmark_kit_id"),
            "command": command_str,
            "cwd": host_cwd,
            "cwd_unavailable": cwd_unavailable,
        },
        "trajectory_text": trajectory_text,
    }

def _build_prompt_text(*, instruction: str, payload: dict[str, Any]) -> str:
    sections: list[str] = []
    if instruction.strip():
        sections.append(instruction.strip())
    whitelisted_keys = (
        "messages",
        "tools_schema",
        "allowed_apps",
        "policy",
        "domain",
        "repo",
        "base_commit",
        "test_command",
        "ground_truth_mode",
        "mcp_endpoint",
        "env_endpoint",
    )
    extras = {
        key: payload.get(key)
        for key in whitelisted_keys
        if payload.get(key) not in (None, "", [], {})
    }
    if extras:
        sections.append(
            "Runtime context:\n"
            + json.dumps(extras, ensure_ascii=False, indent=2, default=str)
        )
    if not sections:
        sections.append(json.dumps(payload, ensure_ascii=False, default=str))
    return "\n\n".join(section.strip() for section in sections if section.strip())


def _get_expected_bearer_token() -> str:
    for env_name in (
        "STUB_CLIENT_TOKEN",
        "GAGE_CODEX_CLIENT_TOKEN",
        "CODEX_CLIENT_TOKEN",
        "GAGE_INSTALLED_CLIENT_TOKEN",
    ):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def _coerce_timeout(value: Any, *, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _read_optional_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return None


def _resolve_submission_contract_patch_path(
    *,
    metadata: dict[str, Any],
    payload: dict[str, Any],
    cwd: str | None,
) -> Path | None:
    if not cwd:
        return None
    submission_contract = str(
        metadata.get("submission_contract") or payload.get("submission_contract") or ""
    ).strip()
    if not submission_contract:
        return None

    workspace_root = Path(cwd).resolve()
    patch_path = (workspace_root / submission_contract).resolve(strict=False)
    if not patch_path.is_relative_to(workspace_root):
        return None
    return patch_path


def _collect_patch(cwd: str | None) -> str | None:
    if not cwd:
        return None
    tracked = _run_capture(["git", "diff", "--binary", "--", "."], cwd=cwd)
    if tracked:
        return tracked
    untracked = _run_capture(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "."],
        cwd=cwd,
    )
    if not untracked:
        return None
    chunks: list[str] = []
    for file_path in (line.strip() for line in untracked.splitlines()):
        if not file_path:
            continue
        diff = _run_capture(
            ["git", "diff", "--no-index", "--binary", "/dev/null", file_path],
            cwd=cwd,
            allow_exit_codes={0, 1},
        )
        if diff:
            chunks.append(diff)
    return "".join(chunks) or None


def _run_capture(argv: list[str], *, cwd: str, allow_exit_codes: set[int] | None = None) -> str | None:
    allow_exit_codes = allow_exit_codes or {0}
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode not in allow_exit_codes:
        return None
    return completed.stdout or None


def _extract_last_diff_block(text: str) -> str | None:
    marker = "diff --git "
    if marker not in text:
        return None
    return marker + text.rsplit(marker, 1)[-1]


def _extract_usage_payload(*, stdout: str, stderr: str) -> dict[str, Any]:
    for text in (stdout, stderr):
        payload = _parse_total_tokens(text)
        if payload:
            return payload
    return {}


def _parse_total_tokens(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if line.lower() != "tokens used":
            continue
        if index + 1 >= len(lines):
            continue
        raw_value = lines[index + 1].replace(",", "").strip()
        try:
            return {"total_tokens": int(raw_value)}
        except ValueError:
            continue
    return {}


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "-._/:=" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    main()
