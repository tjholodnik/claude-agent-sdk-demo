"""
Sandboxed Python code executor.

Runs AI-generated code in an isolated subprocess with:
  - Wall-clock timeout (subprocess.TimeoutExpired)
  - CPU time limit  (resource.RLIMIT_CPU)
  - Virtual memory cap (resource.RLIMIT_AS)
  - Restricted working directory (isolated temp dir)
  - AST-based pre-execution analysis (optional)

Hazards illustrated:
  - Without RLIMIT_AS  → generated code can allocate gigabytes and OOM the host
  - Without RLIMIT_CPU → infinite loops stall the process indefinitely
  - Without AST validation → eval()/exec()/os.system() in generated code
    provides a backdoor to the host OS
"""

from __future__ import annotations

import ast
import os
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    return_code: int
    elapsed: float
    timed_out: bool = False
    memory_killed: bool = False
    ast_violation: str | None = None  # set when AST validation blocks execution

    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out and self.ast_violation is None

    def summary(self) -> str:
        lines = []
        if self.ast_violation:
            lines.append(f"⛔ AST VIOLATION: {self.ast_violation}")
        elif self.timed_out:
            lines.append(f"⏱  TIMED OUT after {self.elapsed:.1f}s")
        elif self.memory_killed:
            lines.append("💀 MEMORY LIMIT: process killed by OS")
        elif self.return_code != 0:
            lines.append(f"❌ Exit code {self.return_code}")
        else:
            lines.append(f"✅ Success ({self.elapsed:.2f}s)")

        if self.stdout:
            lines.append("--- stdout ---")
            lines.append(self.stdout[:2000])
        if self.stderr:
            lines.append("--- stderr ---")
            lines.append(self.stderr[:1000])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AST validation
# ---------------------------------------------------------------------------

# Built-in functions that can execute arbitrary code or access the OS
_DANGEROUS_BUILTINS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})

# Modules that provide OS/network access
_DANGEROUS_MODULES = frozenset({
    "os", "subprocess", "sys", "socket", "urllib", "http",
    "requests", "ftplib", "smtplib", "telnetlib", "pickle",
    "shelve", "shutil", "signal", "ctypes", "cffi",
})

# Safe standard-library modules explicitly allowed
_ALLOWED_MODULES = frozenset({
    "csv", "json", "math", "statistics", "datetime", "decimal",
    "collections", "itertools", "functools", "operator", "re",
    "string", "textwrap", "io", "pathlib", "copy", "pprint",
    "pandas", "numpy", "scipy", "matplotlib", "seaborn",
    "openpyxl", "xlsxwriter",
})


class _ASTDangerDetector(ast.NodeVisitor):
    """Walk a parsed AST and collect policy violations."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Direct calls: eval(...), exec(...), __import__(...)
        if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_BUILTINS:
            self.violations.append(
                f"Dangerous built-in call: {node.func.id}() at line {node.lineno}"
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in _DANGEROUS_MODULES:
                self.violations.append(
                    f"Dangerous import: '{alias.name}' at line {node.lineno}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top in _DANGEROUS_MODULES:
                self.violations.append(
                    f"Dangerous import: 'from {node.module} …' at line {node.lineno}"
                )
        self.generic_visit(node)


def validate_code(code: str) -> str | None:
    """
    Parse `code` and return the first violation string, or None if clean.

    Hazard: skipping this step allows generated code to call os.system(),
    eval(), or exec() — turning the sandboxed executor into an open shell.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    detector = _ASTDangerDetector()
    detector.visit(tree)
    return detector.violations[0] if detector.violations else None


# ---------------------------------------------------------------------------
# Resource-limit helper (runs inside the child process)
# ---------------------------------------------------------------------------

def _apply_resource_limits(memory_bytes: int, cpu_seconds: int) -> None:
    """
    Called via preexec_fn — sets OS-level resource limits on the child.

    Hazard: without RLIMIT_AS the child can allocate unlimited virtual
    memory; without RLIMIT_CPU it can spin on the CPU forever.
    """
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (ValueError, resource.error):
        pass  # Some OS configurations disallow this; executor timeout still protects us

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (ValueError, resource.error):
        pass


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def execute_code(
    code: str,
    *,
    timeout: int = 30,
    memory_mb: int = 256,
    validate: bool = True,
    extra_files: dict[str, str] | None = None,
) -> ExecutionResult:
    """
    Execute `code` in a sandboxed subprocess.

    Args:
        code:        Python source to run.
        timeout:     Wall-clock seconds before the process is killed.
        memory_mb:   Virtual-memory cap in MiB (enforced via RLIMIT_AS).
        validate:    If True, run AST validation before execution.
        extra_files: Additional files to place in the temp workspace
                     {filename: content}.

    Returns:
        ExecutionResult with stdout, stderr, timing, and flags.

    Hazard demo:
        execute_code("x = [0]*10**10", memory_mb=256)   # killed immediately
        execute_code("while True: pass",  timeout=5)    # timed out after 5 s
    """
    # ---- AST pre-check ------------------------------------------------
    if validate:
        violation = validate_code(code)
        if violation:
            return ExecutionResult(
                stdout="",
                stderr="",
                return_code=-1,
                elapsed=0.0,
                ast_violation=violation,
            )

    # ---- Write code to an isolated temp directory ----------------------
    memory_bytes = memory_mb * 1024 * 1024

    with tempfile.TemporaryDirectory(prefix="agent_exec_") as tmpdir:
        code_path = Path(tmpdir) / "generated.py"
        code_path.write_text(code, encoding="utf-8")

        if extra_files:
            for name, content in extra_files.items():
                (Path(tmpdir) / name).write_text(content, encoding="utf-8")

        start = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(code_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                preexec_fn=lambda: _apply_resource_limits(memory_bytes, timeout),
            )
            elapsed = time.monotonic() - start

            # RLIMIT_AS kills the process with SIGKILL (returncode -9 on Unix)
            memory_killed = result.returncode == -9

            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
                elapsed=elapsed,
                memory_killed=memory_killed,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return ExecutionResult(
                stdout="",
                stderr="Process exceeded wall-clock timeout.",
                return_code=-1,
                elapsed=elapsed,
                timed_out=True,
            )
