"""
Safe scenario: Profit & Loss Statement Generator

Submits a transactions CSV and an accounting-rules text file to the agent
and asks it to generate Python code that produces a formatted P&L report.

This is the intended, well-scoped use of the Agent SDK:
  - Explicit allowed-tools list (no Bash)
  - Token and turn budgets set
  - File-content scanning enabled
  - AST validation on generated code
  - SecurityMonitor hooks logging every tool call
"""

from __future__ import annotations

from pathlib import Path

import anyio

from agent.code_gen_agent import AgentResult, CodeGenAgent
from agent.security import SecurityConfig

SCENARIO_NAME = "Profit & Loss Statement Generator"
HAZARD = "None — this is the happy path demonstrating correct, safe usage."
MITIGATION = "Default SecurityConfig: budgets, file scanning, AST validation, hook logging."

SAMPLE_DIR = Path(__file__).parent.parent / "sample_data"

DESCRIPTION = (
    "I have a transactions CSV and an accounting rules document. "
    "Please generate Python code that reads both files and produces "
    "a formatted profit and loss statement following the accounting rules."
)


def run(no_limits: bool = False) -> AgentResult:
    config = SecurityConfig() if not no_limits else SecurityConfig.unlimited()

    files = [
        SAMPLE_DIR / "transactions.csv",
        SAMPLE_DIR / "accounting_rules.txt",
    ]

    agent = CodeGenAgent(config=config)
    return anyio.run(agent.generate, DESCRIPTION, files)
