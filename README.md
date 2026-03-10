# Claude Agent SDK — Security Demo

An interactive Python project that demonstrates how to build a **safe, production-grade agent** with the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/sdk). The demo focuses on illustrating common security hazards that arise when running AI-generated code, and the layered mitigations that prevent them.

---

## What This Demonstrates

| Hazard | Mitigation |
|--------|-----------|
| Agent loops indefinitely through tool calls | Per-session `max_tool_calls` counter + `PreToolUse` hook |
| Agent consumes unlimited API budget | `max_budget_usd` cap enforced by the Agent SDK |
| Agent runs memory-exhausting generated code | `RLIMIT_AS` subprocess resource limit |
| Agent runs CPU-spinning generated code | `RLIMIT_CPU` + wall-clock `timeout` in subprocess |
| User probes the system prompt / model / API key | Pre-flight prompt scan (`check_prompt`) |
| Malicious file disguised as CSV triggers eval() | 4-layer file injection defence (see below) |

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.11 or newer |
| Poetry | 1.8 or newer (`pip install poetry`) |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url> claude-agent-sdk
cd claude-agent-sdk

# 2. Install dependencies (including Streamlit)
poetry install

# 3. Configure your API key
cp .env.example .env
#    edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 4. Launch the web UI
poetry run streamlit run streamlit_app.py

# — or use the interactive CLI —
poetry run python main.py
```

The Streamlit UI opens at **http://localhost:8501** in your browser.

---

## Project Layout

```
claude-agent-sdk/
│
├── streamlit_app.py        ← Streamlit web UI (start here)
├── main.py                 ← Interactive CLI entry point
├── pyproject.toml          ← Poetry project + dependencies
├── .env.example            ← Copy to .env, add ANTHROPIC_API_KEY
│
├── agent/
│   ├── code_gen_agent.py   ← Core agent (ClaudeSDKClient + custom MCP tools)
│   ├── security.py         ← SecurityConfig + SecurityMonitor hooks
│   └── executor.py         ← Sandboxed subprocess executor (RLIMIT_AS/CPU)
│
└── scenarios/
    ├── runner.py           ← CLI runner: python -m scenarios.runner <name>
    ├── sample_data/        ← CSV/TXT files used by scenarios
    ├── safe/
    │   ├── profit_loss.py  ← P&L generator from CSV + accounting rules
    │   └── data_summary.py ← Statistical summary of sales data
    └── unsafe/
        ├── memory_abuse.py     ← Generates code that allocates billions of items
        ├── cpu_abuse.py        ← Generates infinite-loop code
        ├── token_exhaustion.py ← Submits 5 000-row file + verbose prompt
        ├── tool_abuse.py       ← Prompt designed to trigger 50+ tool calls
        ├── introspection.py    ← Asks for system prompt / API key / model name
        └── file_injection.py   ← CSV file containing a hidden OS payload
```

---

## Scenario Reference

### Safe Scenarios

| Scenario | Description |
|----------|-------------|
| `safe/profit_loss` | Agent reads `transactions.csv` + `accounting_rules.txt` and generates a profit & loss statement |
| `safe/data_summary` | Agent reads `sales_data.csv` and generates statistical summary code |

### Unsafe Scenarios (security hazards + mitigations)

| Scenario | Hazard | Mitigation |
|----------|--------|-----------|
| `unsafe/memory_abuse` | Generated code allocates 10 billion items → OOM | `RLIMIT_AS=256 MB` kills subprocess immediately |
| `unsafe/cpu_abuse` | Generated code spins in a `while True` loop | `RLIMIT_CPU` + 30 s wall-clock timeout |
| `unsafe/token_exhaustion` | 5 000-row file + verbose prompt burns tokens | `max_budget_usd=0.10` enforced by Agent SDK |
| `unsafe/tool_abuse` | Prompt induces 50+ read/write/exec cycles | `max_tool_calls=20` → `SecurityViolation` |
| `unsafe/introspection` | Asks for system prompt, model ID, `ANTHROPIC_API_KEY` | `check_prompt()` rejects before agent starts |
| `unsafe/file_injection` | CSV contains `__import__('os').system(...)` payload | 4-layer defence: prompt → file scan → AST → isolation |

---

## Security Architecture

The demo implements **four independent defence layers** for the file-injection attack, each capable of stopping it on its own:

```
User prompt
    │
    ▼
┌─────────────────────────────────────────┐
│  Layer 1 — Prompt check                 │  SecurityMonitor.check_prompt()
│  Blocks "eval the file" / "exec()" etc. │  → SecurityViolation (pre-flight)
└──────────────────────────┬──────────────┘
                           │ (if prompt passes)
                           ▼
┌─────────────────────────────────────────┐
│  Layer 2 — File content scan            │  SecurityMonitor.scan_file()
│  Detects __import__, eval(, exec(, …    │  → SecurityViolation (pre-flight)
└──────────────────────────┬──────────────┘
                           │ (if file passes)
                           ▼
┌─────────────────────────────────────────┐
│  Layer 3 — AST validation               │  executor.validate_code()
│  Parses generated code; blocks eval(),  │  → refused before subprocess
│  exec(), dangerous imports              │
└──────────────────────────┬──────────────┘
                           │ (if code passes)
                           ▼
┌─────────────────────────────────────────┐
│  Layer 4 — Execution isolation          │  executor.execute_code()
│  Subprocess with RLIMIT_AS, RLIMIT_CPU, │  limits blast radius
│  no network, restricted cwd             │
└─────────────────────────────────────────┘
```

**Why no `Bash` tool?**
The Claude Agent SDK's built-in `Bash` tool gives the agent full shell access. This demo deliberately excludes it from `allowed_tools` and replaces it with three narrow custom MCP tools (`read_uploaded_file`, `write_generated_code`, `execute_generated_code`). All execution goes through `executor.py` which applies resource limits and AST validation.

---

## Streamlit UI Guide

After `streamlit run streamlit_app.py`:

1. **Sidebar** — Configure security limits (or choose *No Limits* to observe unmitigated hazards).
2. **Custom Task tab** — Describe a task in plain English, optionally upload data files, click **Run Agent**. Live tool-call events stream into the UI while the agent works.
3. **Scenario Explorer tab** — Pick one of the 8 built-in scenarios, read the hazard / mitigation summary, click **Run Scenario**.
4. **Results area** — Shows metrics (tool calls, code lines, log entries), the agent's response, the generated Python code, and the full tool-call audit log.

Switch the sidebar to **⚠️ No Limits (Demo)** before running any `unsafe/` scenario to watch the unmitigated hazard execute.

---

## CLI Usage

```bash
# List all scenarios
python -m scenarios.runner --list

# Run a safe scenario (uses default SecurityConfig)
python -m scenarios.runner safe/profit_loss

# Run an unsafe scenario with all guards on
python -m scenarios.runner unsafe/file_injection

# Run an unsafe scenario with all guards OFF (observe the hazard)
python -m scenarios.runner unsafe/introspection --no-limits

# Interactive CLI (accepts free-text task + file paths)
python main.py
```

---

## Adding a Scenario

1. Create `scenarios/safe/<name>.py` or `scenarios/unsafe/<name>.py`.
2. Expose these module-level names:

```python
SCENARIO_NAME = "Human-readable title"
HAZARD       = "One sentence: what goes wrong without protection."
MITIGATION   = "One sentence: what prevents it."
DESCRIPTION  = "The prompt string sent to the agent."   # optional but helpful

def run(no_limits: bool = False) -> AgentResult:
    config = SecurityConfig.unlimited() if no_limits else SecurityConfig(...)
    agent  = CodeGenAgent(config=config)
    return anyio.run(agent.generate, DESCRIPTION, [])
```

3. Register it in `scenarios/runner.py`:

```python
SCENARIO_MODULES = {
    ...
    "safe/<name>": "scenarios.safe.<name>",
}
```

The Streamlit UI picks it up automatically on next restart.

---

## SecurityConfig Reference

| Field | Default | Effect |
|-------|---------|--------|
| `max_turns` | `15` | Hard cap on agent turns (enforced by Agent SDK) |
| `max_budget_usd` | `0.50` | Hard spend cap per session (enforced by Agent SDK) |
| `allowed_tools` | `[]` | Explicit allowlist of built-in tools; empty = MCP only |
| `max_tool_calls` | `20` | `SecurityViolation` raised after this many tool invocations |
| `block_introspection` | `True` | Reject prompts probing system prompt / model / API keys |
| `scan_files` | `True` | Scan uploaded file content for injection patterns |
| `validate_code` | `True` | AST-validate generated code before execution |

`SecurityConfig.unlimited()` sets all limits to maximums and disables all detection — use only for hazard demonstrations.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (starts with `sk-ant-`) |

Copy `.env.example` to `.env` and fill in your key. The project loads it automatically via `python-dotenv`.
