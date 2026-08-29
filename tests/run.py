"""Run every test.

    venv\\Scripts\\python.exe tests\\run.py

No framework on purpose: the suite has to run from the same venv the bot uses,
on a machine where the interesting dependencies are OpenCV and a GPU runtime,
and adding pytest to that mix buys nothing here. Each file is a script that
prints what it checked and exits non-zero if anything failed.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["test_remote.py", "test_tracker.py", "test_health.py", "test_solver.py", "test_hazards.py", "test_aim.py", "test_playstyle.py", "test_api.py", "test_updater.py", "test_resync.py", "test_profile.py", "test_stats_agree.py", "test_robustness.py", "test_team_invite.py", "test_i18n.py", "test_trophies.py", "test_layout.py", "test_watchdog.py"]


def main():
    failures = []
    for name in FILES:
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, name)],
            cwd=os.path.dirname(HERE),
            env={**os.environ, "PYTHONPATH": HERE + os.pathsep + os.environ.get("PYTHONPATH", "")},
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(result.stdout, end="")
        if result.stderr.strip():
            print(result.stderr, end="")

        # Exit code alone is not enough. A file that runs nothing exits 0 and
        # would be counted as passing - which is exactly what happened when an
        # edit swallowed a __main__ guard and a whole test file silently stopped
        # executing while the suite kept reporting green.
        ran = "passed," in result.stdout
        if result.returncode != 0 or not ran:
            if not ran and result.returncode == 0:
                print(f"  {name} produced no results at all - is its __main__ guard intact?")
            failures.append(name)

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print(f"All {len(FILES)} test files passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
