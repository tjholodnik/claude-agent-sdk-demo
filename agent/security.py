"""
Security guards for the Claude Agent SDK demo.

Demonstrates how to detect and prevent:
- Excessive tool calls
- Introspection / information leakage prompts
- Injection payloads hidden in uploaded files
- Prompts requesting dangerous code evaluation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import HookMatcher


class SecurityViolation(Exception):
    """Raised when a security policy is violated."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SecurityConfig:
    """Defines security limits for a single agent session."""

    # Agent SDK–level controls
    max_turns: int = 15
    max_budget_usd: float = 0.50
    allowed_tools: list[str] = field(default_factory=list)  # empty = only MCP tools

    # Application-level controls
    max_tool_calls: int = 20          # hard cap across all tool invocations
    block_introspection: bool = True  # reject prompts that probe internals
    scan_files: bool = True           # inspect uploaded files for injection payloads
    validate_code: bool = True        # run AST analysis before executing generated code

    @classmethod
    def unlimited(cls) -> "SecurityConfig":
        """Return a config with all guards disabled (for hazard demonstrations)."""
        return cls(
            max_turns=100,
            max_budget_usd=50.0,
            max_tool_calls=10_000,
            block_introspection=False,
            scan_files=False,
            validate_code=False,
        )


# ---------------------------------------------------------------------------
# Introspection / prompt-injection detection
# ---------------------------------------------------------------------------

# Phrases that suggest the user is trying to extract internal information
_INTROSPECTION_PATTERNS = [
    r"system\s+prompt",
    r"what\s+(is|are)\s+(your|the)\s+(model|instructions|config)",
    r"api[\s_-]?key",
    r"ANTHROPIC_",
    r"show\s+me\s+(your|the)\s+(prompt|instructions|config|settings)",
    r"reveal\s+(your|the)\s+(prompt|instructions|internals)",
    r"what\s+model\s+are\s+you",
    r"ignore\s+(previous|prior|above)\s+instructions",
]

# Phrases that request dangerous code evaluation from a file
_EVAL_REQUEST_PATTERNS = [
    r"\beval\b",
    r"\bexec\b",
    r"run\s+the\s+(file|script|code|contents)",
    r"execute\s+the\s+(file|script|contents)",
    r"evaluate\s+the\s+(file|script|contents)",
    r"import\s+the\s+file",
    r"interpret\s+the\s+(file|script|contents)",
]

_COMPILED_INTROSPECTION = [re.compile(p, re.I) for p in _INTROSPECTION_PATTERNS]
_COMPILED_EVAL_REQUEST = [re.compile(p, re.I) for p in _EVAL_REQUEST_PATTERNS]


# ---------------------------------------------------------------------------
# File content scanning
# ---------------------------------------------------------------------------

# Patterns in uploaded files that indicate an injection payload
_FILE_INJECTION_PATTERNS = [
    r"__import__\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"os\.system\s*\(",
    r"os\.popen\s*\(",
    r"subprocess\.",
    r"socket\.",
    r"urllib\.",
    r"requests\.",
    r"open\s*\(.*['\"]w",   # writing to files
]

_COMPILED_FILE_PATTERNS = [re.compile(p, re.I) for p in _FILE_INJECTION_PATTERNS]

# File extensions we accept; .py / .sh etc. are rejected outright
_ALLOWED_EXTENSIONS = {".csv", ".txt", ".json", ".pdf", ".xlsx", ".tsv", ".md"}


# ---------------------------------------------------------------------------
# SecurityMonitor
# ---------------------------------------------------------------------------

class SecurityMonitor:
    """
    Tracks security events during an agent session and provides hooks
    that can be wired into ClaudeAgentOptions.

    Hazard illustrated: without this monitor an agent can call tools
    hundreds of times, spin indefinitely, or leak internal information.
    """

    def __init__(self, config: SecurityConfig) -> None:
        self.config = config
        self.tool_call_count = 0
        self.log_lines: list[str] = []

    # ------------------------------------------------------------------
    # Pre-flight checks (called BEFORE starting the agent)
    # ------------------------------------------------------------------

    def check_prompt(self, prompt: str) -> None:
        """
        Block prompts that attempt to extract internal information or
        request dangerous code evaluation.

        Hazard: without this check, the agent may echo its system prompt,
        reveal the model name, or generate eval()-based code for a
        malicious "data" file.
        """
        for pattern in _COMPILED_INTROSPECTION:
            if pattern.search(prompt):
                raise SecurityViolation(
                    f"Prompt blocked: introspection pattern detected — '{pattern.pattern}'\n"
                    "Hazard: users can probe the agent for its system prompt, model ID,\n"
                    "or API keys if this check is omitted."
                )

        for pattern in _COMPILED_EVAL_REQUEST:
            if pattern.search(prompt):
                raise SecurityViolation(
                    f"Prompt blocked: dangerous evaluation request — '{pattern.pattern}'\n"
                    "Hazard: a malicious user could upload a .py file disguised as data\n"
                    "and instruct the agent to eval() or exec() its contents."
                )

    def scan_file(self, path: Path) -> None:
        """
        Inspect an uploaded file for:
          1. Disallowed file extensions (e.g. .py, .sh)
          2. Python injection payloads embedded in the content

        Hazard: without this, attackers embed os.system() or __import__()
        calls inside a file that looks like CSV data, then instruct the
        agent to 'run the file contents'.
        """
        if path.suffix.lower() not in _ALLOWED_EXTENSIONS:
            raise SecurityViolation(
                f"File rejected: '{path.name}' has disallowed extension '{path.suffix}'.\n"
                f"Allowed extensions: {sorted(_ALLOWED_EXTENSIONS)}\n"
                "Hazard: executable files (.py, .sh, .js …) uploaded as 'data' could\n"
                "be eval()-ed by the generated code, leading to arbitrary execution."
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return  # binary files are not scannable; executor limits protect us

        for pattern in _COMPILED_FILE_PATTERNS:
            if pattern.search(text):
                raise SecurityViolation(
                    f"File rejected: '{path.name}' contains a suspicious pattern — '{pattern.pattern}'\n"
                    "Hazard: injection payloads embedded in 'data' files can execute\n"
                    "arbitrary code if the generated script reads and eval()s the file."
                )

    # ------------------------------------------------------------------
    # Hook callbacks (called DURING agent execution)
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        print(line)

    async def _pre_tool_hook(self, input_data: dict, tool_use_id: str, context: dict) -> dict:
        """
        Called before each tool invocation.

        Hazard: without a per-session tool-call counter the agent can
        call tools thousands of times, burning tokens and time.
        """
        self.tool_call_count += 1
        tool_name = input_data.get("tool_name", input_data.get("tool_use_name", "unknown"))
        self._log(
            f"[TOOL ▶] {tool_name} "
            f"({self.tool_call_count}/{self.config.max_tool_calls})"
        )

        if self.tool_call_count > self.config.max_tool_calls:
            raise SecurityViolation(
                f"Tool-call limit exceeded: {self.tool_call_count} > "
                f"{self.config.max_tool_calls}.\n"
                "Hazard: an unbounded agent can loop through tool calls indefinitely."
            )

        return {}

    async def _post_tool_hook(self, input_data: dict, tool_use_id: str, context: dict) -> dict:
        """Called after each tool invocation — logs for auditing."""
        tool_name = input_data.get("tool_name", input_data.get("tool_use_name", "unknown"))
        self._log(f"[TOOL ✓] {tool_name} completed")
        return {}

    def as_hooks(self) -> dict:
        """Return a hooks dict suitable for ClaudeAgentOptions."""
        return {
            "PreToolUse": [HookMatcher(matcher=".*", hooks=[self._pre_tool_hook])],
            "PostToolUse": [HookMatcher(matcher=".*", hooks=[self._post_tool_hook])],
        }
