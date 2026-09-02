"""Assemble the collected frames into a set something can actually be trained on.

Two things stand between six thousand labelled pictures and a usable dataset,
and both of them are ways of lying to yourself about how good the result is.

The first is the split. Every data.yaml written so far points validation at
the same images as training, which is fine for checking that a file loads and
useless for anything else: a model scored on pictures it memorised reports
numbers that have nothing to do with how it will play. So this splits them,
and not at random. Frames were collected seconds apart, so the picture before
and the picture after are nearly the same picture - shuffle those into
different halves and the validation set is effectively the training set with
extra steps, which flatters the score by a wide margin. They are split by
time instead: an unbroken block at the end is held out, so validation frames
come from matches the model never saw.

The second is what is in each frame. The screenshot collector labelled
brawlers, walls and bushes but never projectiles, and the live capture
labels projectiles and brawlers but no walls. Pooling them teaches a detector
that walls sometimes do not exist and shots usually do not - the exact
failure that comes from treating a missing label as a negative. So the two
sources become two datasets, one per model, matching how the bot already runs
two: one for entities, one for terrain.
"""

import argparse
import json
import random
import shutil
from pathlib import Path

ENTITY_CLASSES = ["player", "teammate", "enemy"]
TERRAIN_CLASSES = ["wall", "bush", "close_bush"]
SHOT_CLASSES = ["projectile"]

# Ids as the collectors wrote them.
SOURCE_IDS = {"player": 0, "teammate": 1, "enemy": 2,
              "wall": 3, "bush": 4, "close_bush": 5, "projectile": 6}


def read(path):
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except OSError:
        return []


def one_player(rows, remap):
    """Exactly one player, the most central. The rest were enemies.

    The same correction tools/fix_player_class.py makes, applied here as well
    because the live capture writes its labels straight from the running bot
    and never passes through that tool. Without it the frames from one
    collector are corrected and the frames from the other are not, in one
    dataset, which is worse than either.
    """
    player = remap.get(SOURCE_IDS["player"])
    enemy = remap.get(SOURCE_IDS["enemy"])
    if player is None or enemy is None:
        return rows

    claimed = [r for r in rows if r.split()[0] == str(player)]
    if len(claimed) <= 1:
        return rows

    def from_centre(row):
        parts = row.split()
        return (float(parts[1]) - 0.5) ** 2 + (float(parts[2]) - 0.5) ** 2

    keep = min(claimed, key=from_centre)
    out = []
    for row in rows:
        if row.split()[0] == str(player) and row is not keep:
            out.append(" ".join([str(enemy)] + row.split()[1:]))
        else:
            out.append(row)
    return out


def build(name, out, sources, wanted, holdout):
    """One dataset: only the frames and classes a single model should see."""
    remap = {SOURCE_IDS[c]: i for i, c in enumerate(wanted)}
    root = out / name
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Held out per source, not from the tail of everything joined together.
    # The two collectors ran at different times and label different things, so
    # concatenating them and cutting the end hands the whole live capture to
    # validation - which then measures the model on one source and trains it
    # on another, and reports that as generalisation.
    frames = []
    blocks = []
    for source in sources:
        before = len(frames)
        images, labels = source / "images", source / "labels"
        if not images.exists():
            continue
        for label_path in sorted(labels.glob("*.txt")):
            if label_path.stem == "classes":
                continue
            image = images / f"{label_path.stem}.jpg"
            if not image.exists():
                continue
            rows = []
            for line in read(label_path):
                parts = line.split()
                try:
                    class_id = int(parts[0])
                except (ValueError, IndexError):
                    continue
                if class_id in remap and len(parts) >= 5:
                    rows.append(" ".join([str(remap[class_id])] + parts[1:5]))
            # A frame with none of this model's classes in it is not a
            # negative example, it is a frame from the other collector.
            if rows:
                rows = one_player(rows, remap)
                frames.append((image, rows))
        # Mark where this source's block ends so it can be split on its own.
        if len(frames) > before:
            blocks.append((before, len(frames)))

    if not frames:
        print(f"{name}: nothing to build")
        return None

    # Split by position, not at random: neighbouring frames are seconds apart
    # and nearly identical, so a shuffled split scores the model on pictures
    # it has all but seen.
    held = set()
    for start, end in blocks:
        cut = end - max(1, int((end - start) * holdout))
        held.update(range(cut, end))

    written = {"train": 0, "val": 0}
    for index, (image, rows) in enumerate(frames):
        split = "val" if index in held else "train"
        stem = f"{index:06d}"
        shutil.copy2(image, root / "images" / split / f"{stem}.jpg")
        (root / "labels" / split / f"{stem}.txt").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")
        written[split] += 1

    names = "\n".join(f"  {i}: {c}" for i, c in enumerate(wanted))
    (root / "data.yaml").write_text(
        f"# {name}: {written['train']} train, {written['val']} val.\n"
        "# Validation is the last block in collection order, so those frames\n"
        "# come from matches the training half never saw. Do not reshuffle.\n"
        f"path: {root.resolve().as_posix()}\n"
        "train: images/train\nval: images/val\n"
        f"nc: {len(wanted)}\nnames:\n{names}\n", encoding="utf-8")

    boxes = sum(len(rows) for _, rows in frames)
    print(f"{name}: {written['train']} train, {written['val']} val, "
          f"{boxes} boxes -> {root}")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", default=r"C:/Users/vvok/Desktop/VvokAI_dataset",
                        help="the screenshot collector's output")
    parser.add_argument("--shots", default="dataset_live",
                        help="what the bot saved while playing")
    parser.add_argument("--out", default=r"C:/Users/vvok/Desktop/VvokAI_training")
    parser.add_argument("--holdout", type=float, default=0.15)
    args = parser.parse_args()

    frames, shots, out = Path(args.frames), Path(args.shots), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    build("entities", out, [frames, shots], ENTITY_CLASSES, args.holdout)
    build("terrain", out, [frames], TERRAIN_CLASSES, args.holdout)
    build("shots", out, [shots], SHOT_CLASSES, args.holdout)

    print()
    print("three sets, because one model cannot be trained on frames where")
    print("half its classes were never labelled - that teaches it they are absent.")


if __name__ == "__main__":
    main()
