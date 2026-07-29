from __future__ import annotations

import importlib.util
import subprocess
import sys

COMMANDS = [
    [sys.executable, "scripts/validate_template.py"],
    [sys.executable, "-m", "compileall", "-q", "scripts", "tests", ".claude/hooks", "src"],
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
]

# Optional: only runs if the `dev` extra (`pip install -e ".[dev]"`) is
# installed, so the gate still works on a base install without it.
OPTIONAL_COMMANDS = [
    ([sys.executable, "-m", "ruff", "check", "src"], "ruff"),
]


def main() -> int:
    for command in COMMANDS:
        print("+", " ".join(command))
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode

    for command, module_name in OPTIONAL_COMMANDS:
        if importlib.util.find_spec(module_name) is None:
            print(f"(skipped: {module_name} not installed — pip install -e '.[dev]' to enable)")
            continue
        print("+", " ".join(command))
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode

    print("Quality gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
