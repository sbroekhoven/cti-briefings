"""Local Claude Code CLI: equivalent of `cat prompt | claude -p --output-format json --tools "" --strict-mcp-config`.

Story text is untrusted internet content, so by default the call gets no tools (no file reads, no shell,
no web) and no MCP servers (e.g. claude.ai Gmail/Drive connectors), and runs in an empty temporary
directory instead of the project folder (which holds .env). Set allow_tools: true only if you know why.
"""
from __future__ import annotations

import json
import subprocess
import tempfile

from .base import LLMEngine, LLMError, register_engine


@register_engine("claude_code")
class ClaudeCodeEngine(LLMEngine):
    name = "claude_code"

    def complete(self, prompt: str) -> str:
        c = self.cfg
        cmd = [c.get("binary", "claude"), "-p", "--output-format", "json"]
        if not c.get("allow_tools", False):
            cmd += ["--tools", "", "--strict-mcp-config"]
        if c.get("model"):
            cmd += ["--model", str(c["model"])]
        cmd += [str(a) for a in c.get("extra_args", [])]
        try:
            with tempfile.TemporaryDirectory(prefix="cti-brief-llm-") as empty_dir:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      timeout=int(c.get("timeout_seconds", 300)), cwd=c.get("cwd") or empty_dir)
        except FileNotFoundError as exc:
            raise LLMError(f"Claude Code binary not found: {cmd[0]!r} (set llm.engines.claude_code.binary)") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude timed out after {c.get('timeout_seconds', 300)}s") from exc
        if proc.returncode != 0:
            raise LLMError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude did not return JSON envelope: {proc.stdout[:300]}") from exc
        if envelope.get("is_error"):
            raise LLMError(f"claude reported an error: {str(envelope.get('result'))[:500]}")
        return str(envelope.get("result", ""))
