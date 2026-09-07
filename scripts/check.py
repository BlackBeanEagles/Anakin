"""Run every check. Do this before you demo, deploy, or record anything.

    python scripts/check.py

Runs against a throwaway database so it never touches your real cases.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = [
    ("schema coercion", ["scripts/test_coerce.py"]),
    ("escalation ladder", ["scripts/test_ladder.py"]),
    ("fixed-bug regressions", ["scripts/test_bugs.py"]),
    ("routing baseline", ["scripts/run_eval.py", "--baseline-only"]),
]


def main() -> int:
    env = dict(os.environ, LLM_PROVIDER="mock", DRY_RUN="true")
    tmp = ROOT / "check.db"
    for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        p.unlink(missing_ok=True)
    env["PERSIST_DB"] = str(tmp)

    failed = []
    for name, argv in SUITES:
        print(f"\n{'=' * 62}\n  {name}\n{'=' * 62}")
        r = subprocess.run([sys.executable, *argv], cwd=ROOT, env=env)
        if r.returncode != 0:
            failed.append(name)

    for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        p.unlink(missing_ok=True)

    print(f"\n{'=' * 62}")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
    else:
        print("  all suites passed")
    print(f"{'=' * 62}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
