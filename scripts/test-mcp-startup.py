#!/usr/bin/env python
"""Regression test for J.A.R.V.I.S's start-up connection to its MCP servers (the bcbrain knowledge base): one failed
request must not leave every call without the knowledge base until the next restart.

connect_mcp is lifted out of jarvis/voice_agent.py by name, because importing that module loads the speech models.

Usage: python scripts/test-mcp-startup.py   -> exit 0 when every case passes
"""
import ast
import logging
import sys
import types
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "jarvis" / "voice_agent.py"
NAMES = {"MCP_CONNECT_TRIES", "MCP_RETRY_WAIT_SEC", "connect_mcp"}


def defined_names(node):
    if isinstance(node, ast.FunctionDef):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    return set()


def connect_with(failures):
    """Run connect_mcp against a server that fails `failures` times, then answers: (result, attempts, waits)."""
    picked = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body if defined_names(node) & NAMES]
    missing = NAMES - set().union(*map(defined_names, picked))
    if missing:
        sys.exit(f"not found in {SOURCE.name}: {sorted(missing)}")
    attempts, waits = [], []

    class FakeClient:
        def __init__(self, url):
            attempts.append(url)
            if len(attempts) <= failures:
                raise RuntimeError("502 Bad Gateway")

        def read_only_tools(self):
            return [{"name": "ask_brain"}]

    quiet = logging.getLogger("test-mcp-startup")
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    scope = {"MCPClient": FakeClient, "log": quiet, "time": types.SimpleNamespace(sleep=waits.append)}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(SOURCE), "exec"), scope)
    connected = scope["connect_mcp"]("bcbrain", "http://mcp.test/mcp")
    return (connected[1] if connected else None), len(attempts), len(waits)


# (case, failed requests before the server answers, expected (tools, attempts, waits))
CASES = [
    ("answers at once", 0, ([{"name": "ask_brain"}], 1, 0)),
    ("one failed request", 1, ([{"name": "ask_brain"}], 2, 1)),
    ("answers on the last try", 2, ([{"name": "ask_brain"}], 3, 2)),
    ("stays down: skipped, start-up goes on", 99, (None, 3, 2)),
]


def main():
    failed = 0
    for name, failures, want in CASES:
        got = connect_with(failures)
        if got != want:
            failed += 1
            print(f"FAIL {name}: got {got}, want {want}")
    print(f"{len(CASES) - failed}/{len(CASES)} MCP start-up cases passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
