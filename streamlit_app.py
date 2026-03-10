"""
Streamlit web interface for the Claude Agent SDK Security Demo.

Provides two modes of interaction:
  1. Custom Task  — enter any task description + upload files
  2. Scenario Explorer — run any of the 8 built-in scenarios

Security configuration is controlled from the sidebar. Live tool-call
log lines stream into the UI while the agent is running.

Run:
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import importlib
import tempfile
import threading
import time
from pathlib import Path

import anyio
import streamlit as st
from dotenv import load_dotenv

from agent.code_gen_agent import AgentResult, CodeGenAgent
from agent.security import SecurityConfig, SecurityMonitor
from scenarios.runner import SCENARIO_MODULES

load_dotenv()


# ---------------------------------------------------------------------------
# Live-streaming monitor (subclass pattern)
# ---------------------------------------------------------------------------

class _LiveMonitor(SecurityMonitor):
    """
    Extends SecurityMonitor to push formatted log lines to a shared list
    that the Streamlit main thread can poll for live display.
    """

    def __init__(self, config: SecurityConfig, log_list: list[str]) -> None:
        super().__init__(config)
        self._shared_log = log_list

    def _log(self, msg: str) -> None:
        super()._log(msg)
        # self.log_lines[-1] is the formatted "[HH:MM:SS.mmm] ..." line
        if self.log_lines:
            self._shared_log.append(self.log_lines[-1])


class _LiveCodeGenAgent(CodeGenAgent):
    """
    CodeGenAgent subclass that injects a _LiveMonitor so the Streamlit UI
    can display tool-call events as they happen without polling the CLI.
    """

    def __init__(self, config: SecurityConfig, log_list: list[str]) -> None:
        super().__init__(config)
        self._log_list = log_list

    def _make_monitor(self) -> SecurityMonitor:
        return _LiveMonitor(self.config, self._log_list)


# ---------------------------------------------------------------------------
# Thread helpers
# ---------------------------------------------------------------------------

def _run_agent_threaded(
    config: SecurityConfig,
    description: str,
    files: list[Path],
) -> AgentResult | None:
    """Run the agent in a background thread; poll live logs while it runs."""

    live_logs: list[str] = []
    result_holder: list[AgentResult | None] = [None]
    exc_holder: list[Exception | None] = [None]

    def _worker() -> None:
        try:
            agent = _LiveCodeGenAgent(config, live_logs)
            result_holder[0] = anyio.run(agent.generate, description, files)
        except Exception as exc:  # noqa: BLE001
            exc_holder[0] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    log_placeholder = st.empty()
    with st.spinner("Agent running… tool calls appear below as they happen"):
        while thread.is_alive():
            if live_logs:
                log_placeholder.code(
                    "\n".join(live_logs[-30:]),
                    language=None,
                )
            time.sleep(0.3)
        thread.join()

    log_placeholder.empty()

    if exc_holder[0] is not None:
        st.error(f"Unexpected error: {type(exc_holder[0]).__name__}: {exc_holder[0]}")
        return None

    return result_holder[0]


def _run_scenario_threaded(mod, no_limits: bool) -> AgentResult | None:
    """Run a scenario module's run() in a background thread."""

    result_holder: list[AgentResult | None] = [None]
    exc_holder: list[Exception | None] = [None]

    def _worker() -> None:
        try:
            result_holder[0] = mod.run(no_limits=no_limits)
        except Exception as exc:  # noqa: BLE001
            exc_holder[0] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    with st.spinner("Scenario running…"):
        thread.join()

    if exc_holder[0] is not None:
        st.error(f"Unexpected error: {type(exc_holder[0]).__name__}: {exc_holder[0]}")
        return None

    return result_holder[0]


# ---------------------------------------------------------------------------
# Result renderer
# ---------------------------------------------------------------------------

def _render_result(result: AgentResult) -> None:
    if result.error:
        st.error(f"⛔ **Blocked:** {result.error}")
        return

    # Metrics row
    col1, col2, col3 = st.columns(3)
    col1.metric("Tool Calls", result.tool_calls)
    code_lines = (
        result.generated_code.count("\n") + 1 if result.generated_code else 0
    )
    col2.metric("Code Lines", code_lines)
    col3.metric("Log Entries", len(result.log))

    # Agent response
    if result.result_text:
        with st.expander("Agent Response", expanded=True):
            st.markdown(result.result_text)

    # Generated code
    if result.generated_code:
        with st.expander("Generated Python Code", expanded=True):
            st.code(result.generated_code, language="python")

    # Audit log
    if result.log:
        with st.expander("Tool-call Audit Log"):
            st.text("\n".join(result.log))


# ---------------------------------------------------------------------------
# Sidebar — security configuration
# ---------------------------------------------------------------------------

def _sidebar() -> tuple[SecurityConfig, bool]:
    st.sidebar.header("🔒 Security Configuration")

    mode = st.sidebar.radio(
        "Protection mode",
        ["🛡️ Protected", "⚠️ No Limits (Demo)"],
        index=0,
        help=(
            "Protected applies all configured guards. "
            "No Limits disables every guard to demonstrate the raw hazard."
        ),
    )
    no_limits = "No Limits" in mode

    if no_limits:
        st.sidebar.warning(
            "**All security guards are disabled.**  \n"
            "Use this mode only to observe hazards in a controlled environment."
        )
        return SecurityConfig.unlimited(), True

    # Sliders & checkboxes for fine-grained control
    st.sidebar.subheader("Limits")
    max_turns = st.sidebar.slider(
        "Max agent turns", min_value=5, max_value=50, value=15,
        help="Maximum number of back-and-forth turns the agent is allowed.",
    )
    max_budget = st.sidebar.slider(
        "Max budget (USD)", min_value=0.01, max_value=5.0, value=0.50,
        step=0.01, format="$%.2f",
        help="Hard spend cap per session enforced by the Agent SDK.",
    )
    max_tools = st.sidebar.slider(
        "Max tool calls", min_value=5, max_value=100, value=20,
        help="Raise SecurityViolation after this many tool invocations.",
    )

    st.sidebar.subheader("Detections")
    block_introspection = st.sidebar.checkbox(
        "Block introspection prompts", value=True,
        help="Reject prompts that ask for the system prompt, model ID, or API keys.",
    )
    scan_files = st.sidebar.checkbox(
        "Scan uploaded files for payloads", value=True,
        help="Inspect file content for injection patterns before passing to the agent.",
    )
    validate_code = st.sidebar.checkbox(
        "AST-validate generated code before execution", value=True,
        help="Parse the AST and block eval()/exec()/dangerous imports.",
    )

    config = SecurityConfig(
        max_turns=max_turns,
        max_budget_usd=max_budget,
        max_tool_calls=max_tools,
        block_introspection=block_introspection,
        scan_files=scan_files,
        validate_code=validate_code,
    )
    return config, False


# ---------------------------------------------------------------------------
# Scenario Explorer helpers
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _load_scenarios() -> dict[str, dict]:
    """Import all scenario modules once and cache their metadata."""
    info: dict[str, dict] = {}
    for key, mod_path in SCENARIO_MODULES.items():
        try:
            mod = importlib.import_module(mod_path)
            info[key] = {
                "mod": mod,
                "name": getattr(mod, "SCENARIO_NAME", key),
                "hazard": getattr(mod, "HAZARD", ""),
                "mitigation": getattr(mod, "MITIGATION", ""),
                "description": getattr(mod, "DESCRIPTION", ""),
                "safe": key.startswith("safe/"),
            }
        except ImportError as exc:
            info[key] = {"error": str(exc), "safe": key.startswith("safe/")}
    return info


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Claude Agent SDK — Security Demo",
        page_icon="🛡️",
        layout="wide",
    )

    st.title("🛡️ Claude Agent SDK — Security Demo")
    st.caption(
        "An interactive demo illustrating Claude agent security hazards and mitigations.  "
        "Configure guards in the sidebar, then run a custom task or explore a built-in scenario."
    )

    config, no_limits = _sidebar()

    tab_custom, tab_scenario = st.tabs(["🖊️ Custom Task", "🔬 Scenario Explorer"])

    # ── Custom Task tab ────────────────────────────────────────────────────
    with tab_custom:
        st.subheader("Run a Custom Task")
        st.markdown(
            "Describe what you want the agent to do. "
            "Optionally upload one or more data files (CSV, JSON, TXT …) "
            "that the agent can read during code generation."
        )

        description = st.text_area(
            "Task description",
            placeholder="e.g. Read the CSV file and produce a profit & loss statement",
            height=130,
        )

        uploaded_files = st.file_uploader(
            "Upload data files (optional)",
            type=["csv", "txt", "json", "xlsx", "tsv", "md"],
            accept_multiple_files=True,
        )

        run_custom = st.button("▶ Run Agent", type="primary", key="btn_custom")

    # ── Scenario Explorer tab ──────────────────────────────────────────────
    with tab_scenario:
        st.subheader("Explore Built-in Scenarios")
        st.markdown(
            "Choose a scenario to see a real security hazard and its mitigation in action.  "
            "Switch the sidebar to **No Limits** mode to observe the unmitigated attack."
        )

        scenarios = _load_scenarios()
        scenario_keys = list(scenarios.keys())

        def _label(k: str) -> str:
            s = scenarios[k]
            if "error" in s:
                return f"⚠️  {k}  (import error)"
            tag = "🟢" if s["safe"] else "🔴"
            return f"{tag}  {k}  —  {s['name']}"

        selected = st.selectbox(
            "Select a scenario",
            options=scenario_keys,
            format_func=_label,
        )

        s_info = scenarios.get(selected, {})
        if "error" in s_info:
            st.error(f"Cannot load scenario: {s_info['error']}")
        elif s_info:
            col_a, col_b = st.columns(2)
            with col_a:
                st.warning(f"**Hazard:** {s_info['hazard']}")
            with col_b:
                st.success(f"**Mitigation:** {s_info['mitigation']}")
            if s_info.get("description"):
                with st.expander("Scenario prompt (what the user sends)"):
                    st.text(s_info["description"])

        run_scenario = st.button("▶ Run Scenario", type="primary", key="btn_scenario")

    # ── Results area (below tabs, always visible) ──────────────────────────
    st.divider()
    results_container = st.container()

    # Handle Custom Task run
    if run_custom:
        if not description.strip():
            st.warning("Please enter a task description first.")
        else:
            # Persist uploaded files to a temporary directory for the agent
            tmp = tempfile.mkdtemp(prefix="streamlit_upload_")
            saved_paths: list[Path] = []
            for uf in uploaded_files or []:
                dest = Path(tmp) / uf.name
                dest.write_bytes(uf.read())
                saved_paths.append(dest)

            with results_container:
                st.subheader("Results")
                result = _run_agent_threaded(config, description.strip(), saved_paths)
                if result is not None:
                    _render_result(result)
                    st.session_state["last_result"] = result
                    st.session_state["last_result_label"] = "Custom Task"

    # Handle Scenario run
    if run_scenario:
        s_info = scenarios.get(selected, {})
        mod = s_info.get("mod")
        if mod is None:
            st.error(f"Cannot load scenario module: {selected}")
        else:
            with results_container:
                st.subheader(f"Results — {s_info.get('name', selected)}")
                result = _run_scenario_threaded(mod, no_limits=no_limits)
                if result is not None:
                    _render_result(result)
                    st.session_state["last_result"] = result
                    st.session_state["last_result_label"] = s_info.get("name", selected)

    # Show persisted last result when no new run was triggered this render
    if not run_custom and not run_scenario and "last_result" in st.session_state:
        with results_container:
            label = st.session_state.get("last_result_label", "Last Run")
            st.subheader(f"Last Run Results — {label}")
            _render_result(st.session_state["last_result"])


if __name__ == "__main__":
    main()
