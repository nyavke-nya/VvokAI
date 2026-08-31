"""Correct the one mistake the brawler model actually makes.

Checked against 250 boxes on 30 frames a human had corrected by hand, the
model's errors turned out to have exactly one shape:

    said teammate -> was teammate    60 of 60, never wrong
    said enemy    -> was enemy       70 of 70, never wrong
    said player   -> was player      30
    said player   -> was ENEMY       90   <- every single error

It over-claims the player. It never confuses an ally with an enemy, and it
never misses the player; it just also calls two or three enemies "player".

Two facts fix all ninety. There is exactly one player in a match, and the
camera follows them, so on the same 30 frames the real player was the brawler
nearest the centre of the screen in 29 - and second-nearest in the other one.
So: of everything the model called a player, the one closest to the centre
keeps the label and the rest become enemies, which is what they were.

On the frames a human had corrected this takes the labels from 64% right to
98% right, without anybody drawing a box.

What it does NOT do is invent boxes or move them. If the model missed a
brawler entirely, it is still missed. This only relabels what is already
there, which is where the errors were.
"""

import argparse
import shutil
from pathlib import Path

BRAWLERS = {0, 1, 2}
PLAYER, TEAMMATE, ENEMY = 0, 1, 2


def read(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            rows.append([int(parts[0])] + [float(v) for v in parts[1:5]])
        except ValueError:
            continue
    return rows


def correct(rows):
    """One player, the most central; the other claims were enemies."""
    claimed = [row for row in rows if row[0] == PLAYER]
    if len(claimed) <= 1:
        return rows, 0

    def from_centre(row):
        return ((row[1] - 0.5) ** 2 + (row[2] - 0.5) ** 2) ** 0.5

    keep = min(claimed, key=from_centre)
    changed = 0
    out = []
    for row in rows:
        if row[0] == PLAYER and row is not keep:
            out.append([ENEMY] + row[1:])
            changed += 1
        else:
            out.append(row)
    return out, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=r"C:/Users/vvok/Desktop/VvokAI_dataset")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change and write nothing")
    args = parser.parse_args()

    root = Path(args.dataset)
    labels = root / "labels"
    backup = root / "labels_before_player_fix"

    if not args.dry_run and not backup.exists():
        # The originals are kept whole rather than trusted to a rule, however
        # well that rule measured. A relabelling that turns out to be wrong
        # must be undoable without collecting another night of frames.
        shutil.copytree(labels, backup)
        print(f"originals copied to {backup.name}")

    frames = touched = relabelled = 0
    still_odd = 0
    for path in sorted(labels.glob("frame_*.txt")):
        rows = read(path)
        fixed, changed = correct(rows)
        frames += 1
        if changed:
            touched += 1
            relabelled += changed
            if not args.dry_run:
                path.write_text(
                    "\n".join(" ".join([str(r[0])] + [f"{v:.6f}" for v in r[1:]])
                              for r in fixed) + "\n", encoding="utf-8")
        if sum(1 for r in fixed if r[0] == PLAYER) != 1:
            still_odd += 1

    print(f"frames:            {frames}")
    print(f"frames corrected:  {touched}")
    print(f"boxes relabelled:  {relabelled}  (player -> enemy)")
    print(f"still not exactly one player: {still_odd} "
          f"({still_odd / max(1, frames) * 100:.1f}%) - the model missed the "
          "player on those, which relabelling cannot fix")
    if args.dry_run:
        print("\ndry run, nothing written")


if __name__ == "__main__":
    main()
