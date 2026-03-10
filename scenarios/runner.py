"""
Scenario runner.

Usage:
    python -m scenarios.runner safe/profit_loss
    python -m scenarios.runner unsafe/memory_abuse --no-limits
    python -m scenarios.runner --list

Each scenario module must expose:
    SCENARIO_NAME : str          — human-readable title
    HAZARD        : str          — one-sentence description of the hazard
    MITIGATION    : str          — one-sentence description of the fix
    run(no_limits: bool) -> None — run the scenario and print results
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import time


SCENARIO_MODULES = {
    "safe/profit_loss":       "scenarios.safe.profit_loss",
    "safe/data_summary":      "scenarios.safe.data_summary",
    "unsafe/memory_abuse":    "scenarios.unsafe.memory_abuse",
    "unsafe/cpu_abuse":       "scenarios.unsafe.cpu_abuse",
    "unsafe/token_exhaustion":"scenarios.unsafe.token_exhaustion",
    "unsafe/tool_abuse":      "scenarios.unsafe.tool_abuse",
    "unsafe/introspection":   "scenarios.unsafe.introspection",
    "unsafe/file_injection":  "scenarios.unsafe.file_injection",
}


def _banner(title: str, char: str = "=") -> None:
    width = min(72, len(title) + 4)
    print(char * width)
    print(f"  {title}")
    print(char * width)


def _print_result(result) -> None:
    """Pretty-print an AgentResult."""
    from agent.code_gen_agent import AgentResult  # local import to avoid circular deps

    print()
    _banner("Agent Output", "-")

    if result.error:
        print(f"\n⛔ BLOCKED: {result.error}\n")
    else:
        if result.result_text:
            print(result.result_text[:3000])
        else:
            print("(no result text returned)")

    print()
    _banner("Statistics", "-")
    print(f"  Tool calls : {result.tool_calls}")
    print(f"  Log entries: {len(result.log)}")
    if result.generated_code:
        lines = result.generated_code.count("\n") + 1
        print(f"  Generated  : {lines} lines of Python")

    if result.log:
        print()
        _banner("Tool-call audit log", "-")
        for line in result.log:
            print(" ", line)


def list_scenarios() -> None:
    _banner("Available Scenarios")
    for key, mod_path in SCENARIO_MODULES.items():
        try:
            mod = importlib.import_module(mod_path)
            hazard = getattr(mod, "HAZARD", "—")
            name = getattr(mod, "SCENARIO_NAME", key)
            tag = "🟢 SAFE" if key.startswith("safe/") else "🔴 UNSAFE"
            print(f"\n  {tag}  {key}")
            print(f"         {name}")
            print(f"         Hazard: {textwrap.shorten(hazard, 60)}")
        except ImportError as e:
            print(f"\n  {key}  [import error: {e}]")
    print()


def run_scenario(name: str, no_limits: bool) -> None:
    mod_path = SCENARIO_MODULES.get(name)
    if mod_path is None:
        print(f"Unknown scenario: {name!r}")
        print("Run with --list to see available scenarios.")
        sys.exit(1)

    try:
        mod = importlib.import_module(mod_path)
    except ImportError as e:
        print(f"Cannot import scenario {name!r}: {e}")
        sys.exit(1)

    scenario_name = getattr(mod, "SCENARIO_NAME", name)
    hazard = getattr(mod, "HAZARD", "")
    mitigation = getattr(mod, "MITIGATION", "")

    _banner(f"Scenario: {name}")
    print(f"  Name      : {scenario_name}")
    print(f"  Hazard    : {hazard}")
    print(f"  Mitigation: {mitigation}")
    mode = "⚠️  NO LIMITS (hazard demonstration)" if no_limits else "🛡️  PROTECTED (limits enabled)"
    print(f"  Mode      : {mode}")
    print()

    start = time.monotonic()
    try:
        result = mod.run(no_limits=no_limits)
    except Exception as e:
        print(f"\n💥 Scenario raised an unhandled exception: {type(e).__name__}: {e}")
        sys.exit(1)
    elapsed = time.monotonic() - start

    _print_result(result)
    print()
    _banner(f"Completed in {elapsed:.1f}s")
    print()


def main() -> None:
    args = sys.argv[1:]

    if not args or "--list" in args:
        list_scenarios()
        return

    no_limits = "--no-limits" in args
    scenario_args = [a for a in args if not a.startswith("--")]

    if not scenario_args:
        print("Usage: python -m scenarios.runner <scenario> [--no-limits]")
        sys.exit(1)

    run_scenario(scenario_args[0], no_limits=no_limits)


if __name__ == "__main__":
    main()
