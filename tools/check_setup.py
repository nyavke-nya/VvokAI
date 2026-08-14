"""Has the dependency list changed since setup last ran?

Exit 0 means the venv matches what the project asks for and setup can be
skipped; exit 1 means run it.

The launcher used to write venv/.setup_complete once and never look again, so
dependencies were verified exactly one time in the life of an install. Anyone
who updated the fork kept whatever they had, and anyone whose first install
half-failed stayed broken - which is how a download ends at
`ModuleNotFoundError: No module named 'pandas'` with the launcher cheerfully
reporting success. Keying the marker on the dependency list instead means the
check re-runs precisely when it needs to and stays out of the way otherwise.
"""

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKER = ROOT / "venv" / ".setup_complete"
WATCHED = ("setup.py", "requirements.txt")


def fingerprint():
    digest = hashlib.sha256()
    for name in WATCHED:
        path = ROOT / name
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()[:16]


def main():
    current = fingerprint()

    if "--write" in sys.argv:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(current, encoding="utf-8")
        return 0

    if not MARKER.exists():
        return 1
    try:
        return 0 if MARKER.read_text(encoding="utf-8").strip() == current else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
