"""Split the collected dataset into something a person can actually correct.

The full set carries about eighty-eight boxes on every frame, and roughly
sixty of those are wall tiles. Opening that in a labelling tool buries the
picture under a grid: the brawlers - the boxes that are worth a human's
attention and the ones the models get wrong - are lost among hundreds of
corners you will never touch.

So this writes a working copy that contains only the brawler classes. Three
to six boxes a frame instead of eighty-eight, and the game is visible under
them. Wall and bush labels are not deleted, they stay in the original and are
merged back afterwards by --merge.

Frames are ordered worst-first. There is exactly one player on screen in a
Brawl Stars match, always, so any frame where the model found two or five is
certainly wrong and certainly worth opening. Sorting by that puts the
mistakes at the front instead of leaving them scattered through six thousand
files, most of which are already correct.
"""

import argparse
import json
import shutil
from pathlib import Path

BRAWLER_IDS = {0, 1, 2}          # player, teammate, enemy
SCENERY_IDS = {3, 4, 5}          # wall, bush, close_bush
CLASS_NAMES = ["player", "teammate", "enemy",
               "wall", "bush", "close_bush", "projectile", "map_border"]


def read_labels(path):
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


def wrongness(lines):
    """How likely this frame is to be mislabelled. Higher is worse.

    Built from the one thing that is known for certain rather than guessed:
    the player's own brawler appears once. Everything else about a frame is
    open to argument - how many enemies are visible depends on the match - but
    two players is not a judgement call, it is an error.
    """
    counts = {}
    for line in lines:
        cls = class_of(line)
        if cls in BRAWLER_IDS:
            counts[cls] = counts.get(cls, 0) + 1

    players = counts.get(0, 0)
    score = abs(players - 1) * 10          # the certain error, weighted first
    score += max(0, counts.get(1, 0) - 3)  # more than three allies is wrong too
    if players == 0:
        score += 5                         # no player at all: also certainly wrong
    return score


def build(source, out, limit, only_wrong):
    images_in, labels_in = source / "images", source / "labels"
    images_out, labels_out = out / "images", out / "labels"
    images_out.mkdir(parents=True, exist_ok=True)
    labels_out.mkdir(parents=True, exist_ok=True)

    frames = []
    for label_path in sorted(labels_in.glob("frame_*.txt")):
        lines = read_labels(label_path)
        brawlers = [line for line in lines if class_of(line) in BRAWLER_IDS]
        frames.append((wrongness(brawlers), label_path.stem, brawlers))

    frames.sort(key=lambda row: (-row[0], row[1]))
    if only_wrong:
        frames = [row for row in frames if row[0] > 0]
    chosen = frames[:limit] if limit else frames

    order = []
    for rank, (score, stem, brawlers) in enumerate(chosen):
        source_image = images_in / f"{stem}.jpg"
        if not source_image.exists():
            continue
        # Renamed by rank so the labelling tool, which walks the folder in
        # name order, opens the worst frames first.
        name = f"{rank:05d}_{stem}"
        shutil.copy2(source_image, images_out / f"{name}.jpg")
        (labels_out / f"{name}.txt").write_text(
            "\n".join(brawlers) + ("\n" if brawlers else ""), encoding="utf-8")
        order.append({"name": name, "from": stem, "score": score})

    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES[:3]))
    (out / "classes.txt").write_text("\n".join(CLASS_NAMES[:3]) + "\n",
                                     encoding="utf-8")
    (out / "labels" / "classes.txt").write_text(
        "\n".join(CLASS_NAMES[:3]) + "\n", encoding="utf-8")
    (out / "data.yaml").write_text(
        f"path: {out.resolve().as_posix()}\n"
        "train: images\nval: images\nnc: 3\n"
        f"names:\n{names}\n", encoding="utf-8")
    (out / "order.json").write_text(json.dumps(order, indent=1),
                                    encoding="utf-8")

    certain = sum(1 for row in chosen if row[0] >= 10)
    print(f"{len(order)} frames written to {out.resolve()}")
    print(f"   {certain} of them have a certainly-wrong player count")
    print(f"   boxes per frame: "
          f"{sum(len(r[2]) for r in chosen) / max(1, len(chosen)):.1f} "
          "(the full set has about 88)")
    print("   worst first - correct from the top and stop when you like")


def merge(source, corrected):
    """Put corrected brawler boxes back, keeping the scenery labels."""
    order = json.loads((corrected / "order.json").read_text(encoding="utf-8"))
    restored = 0
    for entry in order:
        fixed = corrected / "labels" / f"{entry['name']}.txt"
        original = source / "labels" / f"{entry['from']}.txt"
        if not fixed.exists() or not original.exists():
            continue
        scenery = [line for line in read_labels(original)
                   if class_of(line) in SCENERY_IDS]
        brawlers = read_labels(fixed)
        original.write_text("\n".join(brawlers + scenery) + "\n",
                            encoding="utf-8")
        restored += 1
    print(f"merged {restored} corrected frames back into {source.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=r"C:/Users/vvok/Desktop/VvokAI_dataset")
    parser.add_argument("--out", default=r"C:/Users/vvok/Desktop/VvokAI_review")
    parser.add_argument("--limit", type=int, default=1000,
                        help="how many frames to put in the working set")
    parser.add_argument("--only-wrong", action="store_true",
                        help="only frames with a certainly-wrong count")
    parser.add_argument("--merge", action="store_true",
                        help="put corrected labels back into the source")
    args = parser.parse_args()

    source, out = Path(args.source), Path(args.out)
    if args.merge:
        merge(source, out)
    else:
        build(source, out, args.limit, args.only_wrong)


if __name__ == "__main__":
    main()
