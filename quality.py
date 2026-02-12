from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(args: list[str]) -> None:
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def lint() -> None:
    _run([sys.executable, "-m", "ruff", "check", ".", "--fix"])
    _run([sys.executable, "-m", "ruff", "format", "."])


def typecheck() -> None:
    _run([sys.executable, "-m", "mypy", "."])


def test() -> None:
    _run([sys.executable, "-m", "pytest"])


def check() -> None:
    lint()
    typecheck()
    test()


def main() -> None:
    actions = {
        "lint": lint,
        "typecheck": typecheck,
        "test": test,
        "check": check,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("Usage: python -m quality <lint|typecheck|test|check>")
    actions[sys.argv[1]]()


if __name__ == "__main__":
    main()
