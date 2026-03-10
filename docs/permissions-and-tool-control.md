# Permissions and Tool Control in the Claude Agent SDK

This document covers the interactions between `permission_mode`, `allowed_tools`,
`disallowed_tools`, and the `PreToolUse` hook — including the side effects and failure
modes you must understand before deploying an agent that uses `bypassPermissions`.

---

## Table of Contents

1. [Built-in Claude Code Tools](#1-built-in-claude-code-tools)
2. [permission_mode — What Each Value Actually Does](#2-permission_mode--what-each-value-actually-does)
3. [Side Effects of bypassPermissions](#3-side-effects-of-bypasspermissions)
4. [allowed_tools=[] — The Empty-List Trap](#4-allowed_tools--the-empty-list-trap)
5. [disallowed_tools — Concrete Defence Examples](#5-disallowed_tools--concrete-defence-examples)
6. [Safe Usage of the PreToolUse Hook](#6-safe-usage-of-the-pretooluse-hook)
7. [ClaudeAgentOptions — Full Field Reference](#7-claudeagentoptions--full-field-reference)

---

## 1. Built-in Claude Code Tools

Claude Code ships with a fixed set of built-in tools that are available to every agent
session unless explicitly blocked. Understanding this list is a prerequisite for
reasoning about `allowed_tools` and `disallowed_tools`.

| Tool name | What it does | Risk level |
|-----------|--------------|------------|
| `Bash` | Execute arbitrary shell commands | **Critical** — full host access |
| `Read` | Read any file the process can access | High — can exfiltrate secrets |
| `Write` | Create or overwrite any file | High — can replace source code |
| `Edit` | Targeted string-replace within a file | High |
| `MultiEdit` | Multiple Edit operations in one call | High |
| `Glob` | Find files matching a pattern | Low |
| `Grep` | Search file contents by regex | Low |
| `LS` | List a directory | Low |
| `WebFetch` | HTTP GET to any URL | Medium — exfiltration, SSRF |
| `WebSearch` | Web search via Anthropic proxy | Low–Medium |
| `NotebookEdit` | Modify cells in a `.ipynb` file | Medium |
| `NotebookRead` | Read a Jupyter notebook | Low |
| `TodoWrite` | Write to Claude's internal task list | Low — but unexpected in production |
| `TodoRead` | Read the internal task list | Low |
| `Task` | Spawn a Claude sub-agent | High — escapes current security context |
| `exit_plan_mode` | Exit plan mode | Low |

> **Key point:** Unless you use `allowed_tools` or `disallowed_tools`, *all* of these
> are available to your agent. The three custom MCP tools in this project
> (`read_uploaded_file`, `write_generated_code`, `execute_generated_code`) are
> *additions* on top of this list, not replacements.

---

## 2. permission_mode — What Each Value Actually Does

`permission_mode` is passed to the Claude Code CLI as `--permission-mode`. It controls
how the CLI handles tool operations that would normally require interactive approval.

```python
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]
```

| Mode | Behaviour |
|------|-----------|
| `"default"` | CLI prompts interactively for any tool that touches the filesystem or network. In a non-interactive (programmatic) context this stalls — Claude generates a natural-language approval request instead of executing the tool. |
| `"acceptEdits"` | Auto-approves Edit/Write operations on files already in the session context. Still prompts for Bash, WebFetch, and **all MCP tools**. Causes the same stall as `"default"` for anything not classified as an edit. |
| `"plan"` | Claude proposes a plan but does not execute any tools. Useful for previewing what an agent would do before granting execution rights. |
| `"bypassPermissions"` | Skips all CLI-level permission checks. Every tool — built-in and MCP — executes without an approval prompt. This is the only mode that allows MCP tools to run in a non-interactive environment. |

> **Why "acceptEdits" broke this project:** The three custom MCP tools are not classified
> as "edit" operations by the CLI. With `acceptEdits`, each call to `read_uploaded_file`
> triggered an interactive approval request. Because there is no terminal, Claude
> responded with the natural-language message *"It looks like I need permission to access
> those tools…"* — appearing as an agent error.

---

## 3. Side Effects of bypassPermissions

`bypassPermissions` solves the approval-prompt problem, but it removes the last line
of defence that the CLI itself provides. You must account for the following side effects.

### 3.1 All built-in tools become executable without approval

With `bypassPermissions` and no `disallowed_tools` list, the agent can call `Bash`,
`WebFetch`, `Task`, or any other built-in tool with no friction. This was observed in
the `unsafe/tool_abuse` run: the agent spontaneously called `TodoWrite` even though the
system prompt says nothing about it.

```
# Observed in tool_abuse scenario after switching to bypassPermissions:
[TOOL ▶] TodoWrite (2/20)      ← built-in tool; never mentioned in the system prompt
[TOOL ▶] TodoWrite (8/20)
[TOOL ▶] TodoWrite (12/20)
```

`TodoWrite` is harmless in isolation, but the same mechanism would allow `Bash` if it
were not blocked at the CLI tool-availability level.

### 3.2 allowed_tools=[] does not restrict built-in tools

See [Section 4](#4-allowed_tools--the-empty-list-trap) for a detailed explanation.
The short version: an empty list is treated as "no allowlist restriction", not "allow
nothing". Combined with `bypassPermissions`, this means all built-in tools are available
and all permission prompts are suppressed.

### 3.3 The PreToolUse hook becomes your primary enforcement point

When `bypassPermissions` is set, the application-level `PreToolUse` hook is the only
mechanism that can block a tool call at runtime. If your hook has a bug or is absent,
nothing stops the agent from calling `Bash`, `WebFetch`, or `Task`.

### 3.4 can_use_tool callback is incompatible with bypassPermissions

The `can_use_tool` runtime permission callback is only triggered when the CLI sends a
`can_use_tool` control request — which only happens when a permission prompt would
normally appear. `bypassPermissions` suppresses those prompts, so `can_use_tool` never
fires. Do not rely on `can_use_tool` when using `bypassPermissions`.

### 3.5 Mitigation checklist

When using `bypassPermissions` you must compensate at the application level:

- [ ] Add `disallowed_tools` to block every built-in tool you don't need
- [ ] Register a `PreToolUse` hook that enforces an allowlist by tool name
- [ ] Set `max_tool_calls` to cap total tool invocations per session
- [ ] Set `max_turns` and `max_budget_usd` as hard SDK-level limits
- [ ] Confirm your MCP tool implementations validate inputs independently

---

## 4. allowed_tools=[] — The Empty-List Trap

### What the field is supposed to do

`allowed_tools` maps to the CLI flag `--allowedTools`. When non-empty, it is an
explicit allowlist: only the named tools are available. For example:

```python
allowed_tools=["mcp__code-tools__read_uploaded_file",
               "mcp__code-tools__write_generated_code",
               "mcp__code-tools__execute_generated_code"]
```

This would make only those three tools visible to the agent.

### What an empty list actually does

When `allowed_tools` is an empty list, the SDK does **not** pass `--allowedTools` to
the CLI. The absence of the flag means "no allowlist restriction" — all built-in tools
remain available. This is the opposite of what the comment in `code_gen_agent.py`
implies:

```python
# code_gen_agent.py — this comment is misleading:
allowed_tools=self.config.allowed_tools,  # Empty list = only MCP tools  ← INCORRECT
```

An empty list does not restrict built-in tools. It only means you haven't named any
tools to explicitly enable.

### Correct ways to restrict to MCP tools only

**Option A — Explicit allowlist (most restrictive):**
```python
options = ClaudeAgentOptions(
    allowed_tools=[
        "mcp__code-tools__read_uploaded_file",
        "mcp__code-tools__write_generated_code",
        "mcp__code-tools__execute_generated_code",
    ],
    permission_mode="bypassPermissions",
    ...
)
```
The agent sees only those three tools. Any call to a built-in is rejected before it
reaches the `PreToolUse` hook.

**Option B — Explicit denylist (defence in depth):**
```python
options = ClaudeAgentOptions(
    allowed_tools=[],                       # No allowlist (all tools visible)
    disallowed_tools=["Bash", "WebFetch", "WebSearch", "Task", ...],
    permission_mode="bypassPermissions",
    ...
)
```
Built-in tools you name in `disallowed_tools` are removed from the tool list the
agent sees. See Section 5 for complete examples.

**Option C — Both (belt and suspenders):**
```python
options = ClaudeAgentOptions(
    allowed_tools=["mcp__code-tools__read_uploaded_file", ...],
    disallowed_tools=["Bash", "Task"],      # Extra belt for the most dangerous tools
    permission_mode="bypassPermissions",
    hooks={"PreToolUse": [...]},            # Hook enforces at runtime too
    ...
)
```

---

## 5. disallowed_tools — Concrete Defence Examples

`disallowed_tools` maps to `--disallowedTools`. Tools in this list are removed from
the agent's available tool set before the session starts. The agent cannot call them
even if it tries.

### 5.1 Blocking shell command execution

`Bash` is the highest-risk built-in tool. One `Bash` call can read `/etc/passwd`,
exfiltrate environment variables (including `ANTHROPIC_API_KEY`), install packages,
or fork-bomb the host.

```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    disallowed_tools=[
        "Bash",           # Direct shell execution
        "computer",       # Computer-use tool (GUI automation with shell access)
    ],
    permission_mode="bypassPermissions",
    mcp_servers={"code-tools": server},
    ...
)
```

**Verify the block works:**
```python
# In your PreToolUse hook — belt-and-suspenders check:
async def _pre_tool_hook(input_data, tool_use_id, context):
    tool_name = input_data.get("tool_name", "")
    SHELL_TOOLS = {"Bash", "computer"}
    if tool_name in SHELL_TOOLS:
        # Should never reach here if disallowed_tools is set correctly
        raise SecurityViolation(f"Shell tool '{tool_name}' blocked by hook")
    ...
```

### 5.2 Blocking external network connections

`WebFetch` and `WebSearch` allow the agent to make outbound HTTP requests. An
adversarial prompt could use these to exfiltrate data or trigger server-side request
forgery (SSRF) against internal services.

```python
options = ClaudeAgentOptions(
    disallowed_tools=[
        "WebFetch",       # HTTP GET to arbitrary URLs
        "WebSearch",      # Web search (proxied, lower risk but still outbound)
    ],
    permission_mode="bypassPermissions",
    ...
)
```

**For defence-in-depth, also validate MCP tool inputs** — a crafty prompt might try to
make your custom `read_uploaded_file` tool fetch a URL if its implementation is not
careful:

```python
# In your MCP tool implementation:
@tool("read_uploaded_file", "Read an uploaded file", {"filename": str})
async def read_uploaded_file(args: dict) -> dict:
    filename = args["filename"]
    # Reject anything that looks like a URL or absolute path
    if filename.startswith(("http://", "https://", "/")):
        return {"error": "Only plain filenames are accepted"}
    # Strip path components — never allow traversal
    safe_name = Path(filename).name
    ...
```

### 5.3 Preventing sandbox breakout via sub-agents

The `Task` tool spawns a new Claude sub-agent with a fresh context. A malicious prompt
could use `Task` to launch a second agent that operates under a different (or no)
security context, bypassing hooks registered on the parent agent.

```python
options = ClaudeAgentOptions(
    disallowed_tools=[
        "Task",           # Sub-agent spawning — escapes current security context
    ],
    permission_mode="bypassPermissions",
    hooks=monitor.as_hooks(),   # Hooks only fire in THIS agent; sub-agents are separate
    ...
)
```

> **Important:** `hooks` in `ClaudeAgentOptions` are scoped to the current agent
> session. A sub-agent spawned via `Task` will NOT inherit your `PreToolUse` hooks,
> `max_tool_calls` counter, or any other application-level security controls. Blocking
> `Task` is the only reliable mitigation.

### 5.4 Blocking filesystem escape via Write/Edit

If your agent should only read data (not write anything outside its workspace), block
the built-in file-write tools. Your custom MCP tools already scope writes to a temp
workspace; the built-in Write/Edit tools have no such restriction.

```python
options = ClaudeAgentOptions(
    disallowed_tools=[
        "Write",          # Create or overwrite any file
        "Edit",           # String-replace in any file
        "MultiEdit",      # Multiple edits to any file
        "NotebookEdit",   # Write to .ipynb files
    ],
    permission_mode="bypassPermissions",
    ...
)
```

### 5.5 Recommended baseline disallowed_tools for this project

The three custom MCP tools handle all the file I/O and execution this project needs.
Every built-in tool is superfluous and potentially dangerous:

```python
# agent/code_gen_agent.py — recommended addition to ClaudeAgentOptions:
disallowed_tools=[
    # Shell & system
    "Bash",
    "computer",
    # File system (custom MCP tools handle this instead)
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    # File discovery
    "Glob",
    "Grep",
    "LS",
    # Network
    "WebFetch",
    "WebSearch",
    # Agent escape
    "Task",
    # Internal Claude Code tools (irrelevant in production)
    "TodoWrite",
    "TodoRead",
    "exit_plan_mode",
],
```

This ensures that even if `bypassPermissions` skips the CLI approval layer, there
are no built-in tools in scope for the agent to call.

---

## 6. Safe Usage of the PreToolUse Hook

### What the hook receives

```python
class PreToolUseHookInput(TypedDict):
    hook_event_name: Literal["PreToolUse"]
    tool_name: str                   # e.g. "mcp__code-tools__read_uploaded_file"
    tool_input: dict[str, Any]       # The arguments the agent wants to pass
    tool_use_id: str                 # Unique ID for this tool invocation
    session_id: str
    transcript_path: str
    cwd: str
    permission_mode: NotRequired[str]
```

### What the hook can return

```python
class PreToolUseHookSpecificOutput(TypedDict):
    hookEventName: Literal["PreToolUse"]
    permissionDecision: NotRequired[Literal["allow", "deny", "ask"]]
    permissionDecisionReason: NotRequired[str]
    updatedInput: NotRequired[dict[str, Any]]  # Modify the tool input before execution
    additionalContext: NotRequired[str]

class SyncHookJSONOutput(TypedDict):
    continue_: NotRequired[bool]       # False = block the tool call
    decision: NotRequired[Literal["block"]]
    reason: NotRequired[str]
    systemMessage: NotRequired[str]
    hookSpecificOutput: NotRequired[PreToolUseHookSpecificOutput]
```

Returning `{"continue_": False, "decision": "block"}` aborts the tool call. The agent
receives a tool-use error and may try a different approach or stop.

### Pattern 1 — Tool-call rate cap (as used in this project)

```python
async def _pre_tool_hook(
    self,
    input_data: dict,
    tool_use_id: str | None,
    context: Any,
) -> dict:
    self.tool_call_count += 1
    tool_name = input_data.get("tool_name", "unknown")
    label = f"({self.tool_call_count}/{self.config.max_tool_calls})"
    self._log(f"[TOOL ▶] {tool_name} {label}")

    if self.tool_call_count > self.config.max_tool_calls:
        raise SecurityViolation(
            f"Tool call limit exceeded: {self.tool_call_count} > "
            f"{self.config.max_tool_calls}"
        )
    return {}
```

> **Note:** Raising an exception from the hook will propagate up and be caught by the
> `generate()` method, resulting in an `AgentResult(error=...)`. This is a hard stop —
> the agent session terminates. If you want a soft block (agent continues but tool
> is denied), return `{"continue_": False, "decision": "block"}` instead.

### Pattern 2 — Tool allowlist enforcement in the hook

A belt-and-suspenders check even when `disallowed_tools` is set correctly:

```python
PERMITTED_TOOLS = frozenset({
    "mcp__code-tools__read_uploaded_file",
    "mcp__code-tools__write_generated_code",
    "mcp__code-tools__execute_generated_code",
})

async def tool_allowlist_hook(input_data, tool_use_id, context):
    tool_name = input_data.get("tool_name", "")
    if tool_name not in PERMITTED_TOOLS:
        return {
            "continue_": False,
            "decision": "block",
            "reason": f"Tool '{tool_name}' is not on the permitted list",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blocked: {tool_name}",
            },
        }
    return {}
```

Wire it up:
```python
options = ClaudeAgentOptions(
    hooks={
        "PreToolUse": [
            HookMatcher(matcher=".*", hooks=[tool_allowlist_hook]),
        ]
    },
    ...
)
```

### Pattern 3 — Input sanitisation before execution

The hook can inspect and rewrite `tool_input` before the tool runs. Use this to strip
dangerous parameters rather than blocking the call entirely:

```python
async def sanitise_filename_hook(input_data, tool_use_id, context):
    tool_name = input_data.get("tool_name", "")
    tool_input = dict(input_data.get("tool_input", {}))

    if tool_name == "mcp__code-tools__read_uploaded_file":
        raw = tool_input.get("filename", "")
        # Strip any path components the agent might have tried to inject
        tool_input["filename"] = Path(raw).name

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": tool_input,   # Overrides what the agent passed
        }
    }
```

### Pattern 4 — Audit logging with context

```python
import json, logging
log = logging.getLogger("agent.audit")

async def audit_hook(input_data, tool_use_id, context):
    log.info(json.dumps({
        "event": "pre_tool_use",
        "tool": input_data.get("tool_name"),
        "tool_use_id": tool_use_id,
        "input_keys": list(input_data.get("tool_input", {}).keys()),
        "session_id": input_data.get("session_id"),
    }))
    return {}
```

### What NOT to do in a PreToolUse hook

```python
# BAD — side-effects that depend on the tool actually succeeding
async def bad_hook(input_data, tool_use_id, context):
    count_db.increment("tool_calls")   # Increments even if the tool is later denied
    send_slack_alert(...)              # May fire before execution is confirmed
    return {}

# BAD — catching SecurityViolation and swallowing it
async def bad_hook(input_data, tool_use_id, context):
    try:
        check_something_risky()
    except Exception:
        pass   # Silent failure — the tool call proceeds unchecked
    return {}

# BAD — expensive blocking I/O in the hook (delays every tool call)
async def bad_hook(input_data, tool_use_id, context):
    requests.get("https://policy-service.internal/check")   # Synchronous HTTP call
    return {}
```

---

## 7. ClaudeAgentOptions — Full Field Reference

```python
@dataclass
class ClaudeAgentOptions:
    ...
```

### Tool availability

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tools` | `list[str] \| ToolsPreset \| None` | `None` | Preset tool set (e.g. `"all"`, `"none"`). Overridden by `allowed_tools`. |
| `allowed_tools` | `list[str]` | `[]` | Explicit allowlist. **Empty list = no restriction**, not "allow nothing". Named tools are the only ones the agent can call. |
| `disallowed_tools` | `list[str]` | `[]` | Explicit denylist. Named tools are removed from the agent's tool set before the session starts. Takes precedence over `allowed_tools`. |

### Permissions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `permission_mode` | `PermissionMode \| None` | `None` (CLI default = `"default"`) | `"default"` prompts interactively. `"acceptEdits"` auto-accepts file edits only. `"bypassPermissions"` skips all CLI approval prompts. `"plan"` proposes but does not execute. |
| `permission_prompt_tool_name` | `str \| None` | `None` | Name of the MCP tool the CLI should use to send permission prompts. Mutually exclusive with `can_use_tool`. |
| `can_use_tool` | `CanUseTool \| None` | `None` | Async callback: `(tool_name, input, context) → PermissionResult`. Fires when the CLI sends a `can_use_tool` control request. **Does not fire when `bypassPermissions` is set.** Requires streaming (AsyncIterable) prompt mode. |

### Hooks

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hooks` | `dict[HookEvent, list[HookMatcher]] \| None` | `None` | Hook callbacks keyed by event name. Supported events: `"PreToolUse"`, `"PostToolUse"`, `"PostToolUseFailure"`, `"UserPromptSubmit"`, `"Stop"`, `"SubagentStop"`, `"PreCompact"`, `"Notification"`, `"SubagentStart"`, `"PermissionRequest"`. |

`HookMatcher` fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `matcher` | `str \| None` | `None` | Regex matched against `tool_name`. `".*"` matches all tools. `"Bash\|Write"` matches only those two. `None` matches all. |
| `hooks` | `list[HookCallback]` | `[]` | List of async callback functions. |
| `timeout` | `float \| None` | `60.0` | Seconds before the hook is forcibly cancelled. |

### Budget and turn limits

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_turns` | `int \| None` | `None` | Hard cap on conversation turns. The SDK enforces this; the agent cannot exceed it. |
| `max_budget_usd` | `float \| None` | `None` | Hard cap on API spend per session in USD. The SDK terminates the session when the budget is reached. |

### Model and thinking

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `str \| None` | `None` | Model identifier, e.g. `"claude-opus-4-6"`. |
| `fallback_model` | `str \| None` | `None` | Model to use if the primary model is unavailable. |
| `thinking` | `ThinkingConfig \| None` | `None` | `{"type": "adaptive"}` enables extended thinking only when beneficial. `{"type": "enabled", "budget_tokens": 8000}` always thinks up to the token budget. `{"type": "disabled"}` skips thinking. |
| `effort` | `Literal["low","medium","high","max"] \| None` | `None` | Shorthand for thinking depth. Overrides `thinking` if both are set. |
| `max_thinking_tokens` | `int \| None` | `None` | Deprecated — use `thinking` instead. |

### MCP servers

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mcp_servers` | `dict[str, McpServerConfig] \| str \| Path` | `{}` | MCP server configuration. Values can be stdio (`{"command": ..., "args": ...}`), SSE (`{"type": "sse", "url": ...}`), HTTP, or SDK in-process servers (`create_sdk_mcp_server(...)`). |

### Session management

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `continue_conversation` | `bool` | `False` | Resume the most recent conversation for this working directory. |
| `resume` | `str \| None` | `None` | Resume a specific session by session ID. |
| `fork_session` | `bool` | `False` | When resuming, fork to a new session ID instead of continuing in-place. |
| `enable_file_checkpointing` | `bool` | `False` | Track file modifications so they can be rewound to a checkpoint. |

### Environment and execution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cwd` | `str \| Path \| None` | `None` | Working directory for the CLI process. Defaults to the current directory of the calling process. |
| `env` | `dict[str, str]` | `{}` | Environment variables to set for the CLI process. Merged with the calling process environment. |
| `add_dirs` | `list[str \| Path]` | `[]` | Additional directories to add to the session context (Claude Code's project awareness). |
| `cli_path` | `str \| Path \| None` | `None` | Path to the `claude` CLI binary. Defaults to the one found on `PATH`. |
| `setting_sources` | `list[SettingSource] \| None` | `None` | Which Claude Code settings files to load: `"user"` (`~/.claude/`), `"project"` (`.claude/`), `"local"`. `None` loads all. Pass `[]` to ignore all settings files. |
| `settings` | `str \| None` | `None` | Inline settings JSON string, applied after `setting_sources`. |

### Sandbox (bash isolation)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sandbox` | `SandboxSettings \| None` | `None` | OS-level sandbox configuration for the built-in `Bash` tool (macOS/Linux only). |

`SandboxSettings` fields:

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | `bool` | Enable bash sandboxing. |
| `autoAllowBashIfSandboxed` | `bool` | Auto-approve bash commands when a sandbox is active. |
| `excludedCommands` | `list[str]` | Commands that bypass the sandbox (run on the host). |
| `network` | `SandboxNetworkConfig` | Network rules inside the sandbox. |
| `ignoreViolations` | `SandboxIgnoreViolations` | Violation types to suppress rather than block. |
| `enableWeakerNestedSandbox` | `bool` | For unprivileged Docker environments that can't run the full sandbox. |

### Streaming and output

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `include_partial_messages` | `bool` | `False` | Stream partial message updates as they arrive, not just complete messages. |
| `output_format` | `dict[str, Any] \| None` | `None` | JSON schema for structured output. The agent's final response will conform to this schema. |
| `max_buffer_size` | `int \| None` | `None` | Maximum bytes to buffer from CLI stdout before raising an error. |
| `stderr` | `Callable[[str], None] \| None` | `None` | Callback for stderr lines from the CLI process. Useful for surfacing CLI-level errors. |

### Advanced

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `system_prompt` | `str \| SystemPromptPreset \| None` | `None` | System prompt passed to the model. Prepended to or replaces the default Claude Code system prompt depending on `SystemPromptPreset`. |
| `user` | `str \| None` | `None` | User identifier for multi-tenant logging and session scoping. |
| `agents` | `dict[str, AgentDefinition] \| None` | `None` | Named sub-agent definitions, accessible via the `Task` tool. |
| `betas` | `list[SdkBeta]` | `[]` | Beta feature flags, e.g. `"context-1m-2025-08-07"` for 1M-token context. |
| `plugins` | `list[SdkPluginConfig]` | `[]` | SDK plugin configurations (experimental). |
| `extra_args` | `dict[str, str \| None]` | `{}` | Arbitrary additional CLI flags, passed through verbatim. |

---

## Quick Reference: Secure Configuration Template

```python
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
from claude_agent_sdk import create_sdk_mcp_server

# Build your MCP server with only the tools the agent actually needs
server = create_sdk_mcp_server("code-tools", tools=[
    read_uploaded_file,
    write_generated_code,
    execute_generated_code,
])

options = ClaudeAgentOptions(
    # ── Tool availability ────────────────────────────────────────────────
    # Explicit allowlist: ONLY these three MCP tools are visible
    allowed_tools=[
        "mcp__code-tools__read_uploaded_file",
        "mcp__code-tools__write_generated_code",
        "mcp__code-tools__execute_generated_code",
    ],
    # Belt-and-suspenders: also explicitly block the highest-risk built-ins
    # in case allowed_tools behaviour changes in a future SDK release
    disallowed_tools=[
        "Bash", "Task", "WebFetch", "WebSearch",
        "Write", "Edit", "MultiEdit",
        "Read", "Glob", "Grep", "LS",
    ],

    # ── Permissions ──────────────────────────────────────────────────────
    # Required for MCP tools to execute in a non-interactive environment.
    # Compensated by allowed_tools, disallowed_tools, and the PreToolUse hook.
    permission_mode="bypassPermissions",

    # ── MCP server ───────────────────────────────────────────────────────
    mcp_servers={"code-tools": server},

    # ── Hooks (application-level enforcement) ────────────────────────────
    hooks={
        "PreToolUse": [
            HookMatcher(matcher=".*", hooks=[security_monitor.pre_tool_hook]),
        ],
        "PostToolUse": [
            HookMatcher(matcher=".*", hooks=[security_monitor.post_tool_hook]),
        ],
    },

    # ── Hard SDK limits ──────────────────────────────────────────────────
    max_turns=15,
    max_budget_usd=0.50,

    # ── Model ────────────────────────────────────────────────────────────
    model="claude-opus-4-6",
    thinking={"type": "adaptive"},

    # ── System prompt ────────────────────────────────────────────────────
    system_prompt=SYSTEM_PROMPT,

    # ── Isolate from user and project Claude Code settings ───────────────
    # Prevents ~/.claude/settings.json or .claude/settings.json from
    # overriding the security configuration above
    setting_sources=[],
)
```
