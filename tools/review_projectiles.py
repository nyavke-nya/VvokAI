"""A working copy of the projectile frames, built to be deleted from quickly.

The projectile labels are collected loose on purpose: a box that should not be
there is one keypress to remove, while a shot that was never labelled means
going back through a night of frames to find it, which nobody will do. That
trade only pays if removing one really is one keypress.

It is not, in the full set. Every frame there carries the brawlers too, so a
reviewer hunting for a wrong fireball is picking it out of a screen of boxes
they have no intention of touching, and the game underneath is invisible.

So this writes the same frames with the projectile boxes and nothing else -
usually one or two to a picture - and orders them worst-first, by the same
signals the collector was told to stop trusting. A track the tracker followed
for three frames at half confidence is far more likely to be an explosion than
one it followed for nine; put those at the front and the reviewer's first
minutes remove the most rubbish.

Deleting a box in the labelling tool is enough. --merge afterwards keeps
whatever survived and restores the brawler labels around it.
"""

import argparse
import json
import shutil
from pathlib import Path

PROJECTILE = 6
CLASS_NAMES = ["player", "teammate", "enemy", "wall", "bush", "close_bush",
               "projectile", "map_border"]


def read(path):
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except OSError:
        return []


def class_of(line):
    try:
        return int(line.split()[0])
    except (ValueError, IndexError):
        return -1


def doubt(entry):
    """How likely this frame's projectiles are to be rubbish. Higher is worse.

    Built from what the collector recorded about each track rather than from
    the picture: a shot followed for many frames at high confidence is a shot,
    and a two-frame flicker at half confidence is a muzzle flash.
    """
    tracks = entry.get("tracks") or []
    if not tracks:
        return 0.0
    worst = 0.0
    for track in tracks:
        confidence = float(track.get("conf", 1.0))
        hits = int(track.get("hits", 9))
        speed = float(track.get("speed", 1000))
        score = (1.0 - min(confidence, 1.0)) * 2.0
        score += max(0, 8 - hits) * 0.25
        if speed < 400:
            score += 1.0
        worst = max(worst, score)
    return worst


def build(source, out, limit):
    images_out, labels_out = out / "images", out / "labels"
    images_out.mkdir(parents=True, exist_ok=True)
    labels_out.mkdir(parents=True, exist_ok=True)

    journal = {}
    log = source / "log.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            journal[entry.get("frame")] = entry

    frames = []
    for label_path in sorted((source / "labels").glob("*.txt")):
        shots = [line for line in read(label_path)
                 if class_of(line) == PROJECTILE]
        if not shots:
            continue
        frames.append((doubt(journal.get(label_path.stem, {})),
                       label_path.stem, shots))

    frames.sort(key=lambda row: (-row[0], row[1]))
    chosen = frames[:limit] if limit else frames

    order = []
    for rank, (score, stem, shots) in enumerate(chosen):
        image = source / "images" / f"{stem}.jpg"
        if not image.exists():
            continue
        name = f"{rank:05d}_{stem}"
        shutil.copy2(image, images_out / f"{name}.jpg")
        (labels_out / f"{name}.txt").write_text(
            "\n".join(shots) + "\n", encoding="utf-8")
        order.append({"name": name, "from": stem, "doubt": round(score, 2)})

    # One class, so the labelling tool cannot renumber anything by accident.
    for target in (out / "classes.txt", labels_out / "classes.txt"):
        target.write_text("projectile\n", encoding="utf-8")
    (out / "data.yaml").write_text(
        f"path: {out.resolve().as_posix()}\ntrain: images\nval: images\n"
        "nc: 1\nnames:\n  0: projectile\n", encoding="utf-8")
    (out / "order.json").write_text(json.dumps(order, indent=1),
                                    encoding="utf-8")

    boxes = sum(len(row[2]) for row in chosen)
    print(f"{len(order)} frames, {boxes} projectile boxes -> {out.resolve()}")
    print(f"   {sum(1 for r in chosen if r[0] >= 2.0)} frames flagged as most "
          "doubtful, and they are first")
    print("   delete the boxes that are not projectiles, then run --merge")


def merge(source, review):
    """Keep what survived; restore the brawler labels around it."""
    order = json.loads((review / "order.json").read_text(encoding="utf-8"))
    changed = removed = 0
    for entry in order:
        kept_path = review / "labels" / f"{entry['name']}.txt"
        original = source / "labels" / f"{entry['from']}.txt"
        if not original.exists():
            continue

        # The reviewer's file uses class 0 because the working copy has one
        # class; those are projectiles, whatever they are numbered there.
        survivors = []
        for line in read(kept_path):
            parts = line.split()
            if len(parts) >= 5:
                survivors.append(" ".join([str(PROJECTILE)] + parts[1:5]))

        others = [line for line in read(original)
                  if class_of(line) != PROJECTILE]
        before = sum(1 for line in read(original)
                     if class_of(line) == PROJECTILE)
        removed += before - len(survivors)
        original.write_text("\n".join(survivors + others) + "\n",
                            encoding="utf-8")
        changed += 1
    print(f"merged {changed} frames back; {removed} projectile boxes removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="dataset_live")
    parser.add_argument("--out", default=r"C:/Users/vvok/Desktop/VvokAI_shots_review")
    parser.add_argument("--limit", type=int, default=0,
                        help="0 for every frame")
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()

    source, out = Path(args.source), Path(args.out)
    if args.merge:
        merge(source, out)
    else:
        build(source, out, args.limit)


if __name__ == "__main__":
    main()
