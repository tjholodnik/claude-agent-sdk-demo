"""
Interactive CLI for the Claude Agent SDK code-generation demo.

Usage:
    poetry run claude-agent-sdk-demo
    python main.py

The CLI accepts a free-text task description and optional file paths,
then runs the agent with default security settings and prints the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
from dotenv import load_dotenv

from agent.code_gen_agent import AgentResult, CodeGenAgent
from agent.security import SecurityConfig, SecurityViolation


def _header() -> None:
    print()
    print("=" * 60)
    print("  Claude Agent SDK — Code Generation Demo")
    print("  Security mode: PROTECTED (default SecurityConfig)")
    print("=" * 60)
    print()
    print("This agent accepts a task description and optional files,")
    print("then generates and executes Python code to accomplish the task.")
    print()
    print("Type 'quit' or Ctrl-C to exit.")
    print()


def _prompt_files() -> list[Path]:
    raw = input("File paths (comma-separated, or Enter to skip): ").strip()
    if not raw:
        return []

    files: list[Path] = []
    for part in raw.split(","):
        p = Path(part.strip()).expanduser()
        if not p.exists():
            print(f"  ⚠  File not found: {p} — skipping")
        elif not p.is_file():
            print(f"  ⚠  Not a file: {p} — skipping")
        else:
            files.append(p)
    return files


def _print_result(result: AgentResult) -> None:
    print()
    print("-" * 60)

    if result.error:
        print(f"\n⛔  BLOCKED: {result.error}\n")
        return

    if result.result_text:
        print("\n--- Agent response ---")
        print(result.result_text[:4000])

    if result.generated_code:
        print(f"\n--- Generated code ({result.generated_code.count(chr(10))+1} lines) ---")
        print(result.generated_code[:2000])

    print(f"\n  Tool calls made: {result.tool_calls}")

    if result.log:
        print("\n  Tool-call audit log:")
        for line in result.log[-10:]:   # last 10 entries
            print(f"    {line}")
    print()


def main() -> None:
    load_dotenv()
    _header()

    config = SecurityConfig()

    while True:
        try:
            description = input("Task description: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not description:
            continue
        if description.lower() in {"quit", "exit", "q"}:
            print("Bye!")
            break

        files = _prompt_files()

        print(f"\n⏳ Running agent (max {config.max_turns} turns, "
              f"${config.max_budget_usd:.2f} budget)...\n")

        try:
            result = anyio.run(CodeGenAgent(config=config).generate, description, files)
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        except Exception as e:
            print(f"\n💥 Unexpected error: {type(e).__name__}: {e}")
            continue

        _print_result(result)


if __name__ == "__main__":
    main()
