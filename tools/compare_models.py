"""Score the new model and the one it would replace, on the same frames.

A training run ends by printing how good it thinks it is. That number is not
comparable to anything: it comes from ultralytics' own validation loop, and
the model it would replace is an ONNX file the bot loads through its own
detector with its own preprocessing and its own confidence threshold. Comparing
one to the other is comparing two measurements of different things and calling
the difference progress.

So both are run here, over the same held-out frames, through the same matching
code, and reported side by side. If the new one is not better on this, it is
not better, whatever the training log said.

The frames are the validation split, which neither model was fitted to - the
old one because it was trained by somebody else on other footage entirely, the
new one because the split held those frames back.

Note on what is being measured. The labels these are scored against were
written by the old model and then corrected by rule, so a perfect score
against them means "agrees with the old model where the rule did not fire".
That makes this a fair test of the correction and a weak test of everything
else: both models are being graded partly on their own homework. It answers
"did the player/enemy confusion get fixed" honestly, and "does it see brawlers
better" only loosely.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

TRAIN_PYTHON = Path(r"C:/Users/vvok/Desktop/train_venv/Scripts/python.exe")
IOU_MATCH = 0.5


def load_truth(labels_dir, stem, width, height):
    path = labels_dir / f"{stem}.txt"
    boxes = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return boxes
    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        class_id = int(parts[0])
        cx, cy, bw, bh = (float(v) for v in parts[1:5])
        boxes.append((class_id,
                      (cx - bw / 2) * width, (cy - bh / 2) * height,
                      (cx + bw / 2) * width, (cy + bh / 2) * height))
    return boxes


def iou(a, b):
    ax1, ay1, ax2, ay2 = a[1:5]
    bx1, by1, bx2, by2 = b[1:5]
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def score(predictions, truths, class_count):
    """Precision and recall per class, matching greedily by overlap."""
    hit = [0] * class_count
    predicted = [0] * class_count
    actual = [0] * class_count

    for preds, truth in zip(predictions, truths):
        used = set()
        for box in truth:
            actual[box[0]] += 1
        for box in preds:
            predicted[box[0]] += 1
            best, best_index = 0.0, None
            for index, target in enumerate(truth):
                if index in used or target[0] != box[0]:
                    continue
                overlap = iou(box, target)
                if overlap > best:
                    best, best_index = overlap, index
            if best_index is not None and best >= IOU_MATCH:
                used.add(best_index)
                hit[box[0]] += 1
    return hit, predicted, actual


def run_old(images, labels_dir, names, confidence):
    """The shipped ONNX, through the detector the bot itself uses."""
    import detect as detect_module

    original = detect_module.load_toml_as_dict

    def patched(path, *args, **kwargs):
        values = original(path, *args, **kwargs)
        if str(path).endswith("general_config.toml"):
            values = dict(values)
            # CPU: the GPU is busy training, and a fair comparison does not
            # depend on which device ran it.
            values["cpu_or_gpu"] = "cpu"
            values["execution_provider"] = "cpu"
            values["used_threads"] = 4
        return values

    detect_module.load_toml_as_dict = patched
    try:
        model = detect_module.Detect("models/mainInGameModel.onnx",
                                     classes=["enemy", "teammate", "player"])
    finally:
        detect_module.load_toml_as_dict = original

    # The shipped model numbers its classes differently from the dataset.
    mapping = {"player": names.index("player"),
               "teammate": names.index("teammate"),
               "enemy": names.index("enemy")}

    predictions, truths = [], []
    for path in images:
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        found = model.detect_objects(rgb, conf_tresh=confidence) or {}
        boxes = []
        for name, items in found.items():
            if name not in mapping:
                continue
            for box in items or []:
                boxes.append((mapping[name], float(box[0]), float(box[1]),
                              float(box[2]), float(box[3])))
        predictions.append(boxes)
        truths.append(load_truth(labels_dir, path.stem, width, height))
    return predictions, truths


def run_new(weights, images, confidence):
    """The freshly trained weights, run in the training environment."""
    import subprocess

    listing = [str(p) for p in images]
    script = f'''
import json, sys
from ultralytics import YOLO
model = YOLO({str(weights)!r})
out = []
for path in {listing!r}:
    result = model.predict(path, conf={confidence}, verbose=False, device=0)[0]
    boxes = []
    for box in result.boxes:
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        boxes.append([int(box.cls.item()), x1, y1, x2, y2])
    out.append(boxes)
print("PREDICTIONS", json.dumps(out))
'''
    result = subprocess.run([str(TRAIN_PYTHON), "-c", script],
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("PREDICTIONS "):
            return [[tuple(b) for b in frame]
                    for frame in json.loads(line[len("PREDICTIONS "):])]
    print(result.stdout[-2000:])
    print(result.stderr[-2000:])
    raise SystemExit("the new model produced nothing")


def report(title, hit, predicted, actual, names):
    print(f"\n{title}")
    print(f"  {'class':<10}{'precision':>11}{'recall':>9}{'found':>8}{'real':>7}")
    for index, name in enumerate(names):
        precision = hit[index] / predicted[index] if predicted[index] else 0.0
        recall = hit[index] / actual[index] if actual[index] else 0.0
        print(f"  {name:<10}{precision:>10.1%}{recall:>9.1%}"
              f"{predicted[index]:>8}{actual[index]:>7}")
    total_hit, total_pred, total_actual = sum(hit), sum(predicted), sum(actual)
    print(f"  {'all':<10}"
          f"{(total_hit / total_pred if total_pred else 0):>10.1%}"
          f"{(total_hit / total_actual if total_actual else 0):>9.1%}"
          f"{total_pred:>8}{total_actual:>7}")
    return (total_hit / total_pred if total_pred else 0.0,
            total_hit / total_actual if total_actual else 0.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",
                        default=r"C:/Users/vvok/Desktop/VvokAI_training/entities")
    parser.add_argument("--weights",
                        default=r"C:/Users/vvok/Desktop/VvokAI_training/runs/entities/weights/best.pt")
    parser.add_argument("--limit", type=int, default=300,
                        help="frames to score; all of them takes a while")
    parser.add_argument("--confidence", type=float, default=0.5)
    args = parser.parse_args()

    root = Path(args.dataset)
    images = sorted((root / "images" / "val").glob("*.jpg"))[:args.limit]
    labels_dir = root / "labels" / "val"
    if not images:
        sys.exit(f"no validation frames in {root}")

    names = []
    for line in (root / "data.yaml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and stripped[0].isdigit() and ":" in stripped:
            names.append(stripped.split(":", 1)[1].strip())

    print(f"scoring {len(images)} held-out frames, both models, "
          f"confidence {args.confidence}, IoU {IOU_MATCH}")

    old_predictions, truths = run_old(images, labels_dir, names, args.confidence)
    old = report("the model the bot ships with",
                 *score(old_predictions, truths, len(names)), names)

    weights = Path(args.weights)
    if not weights.exists():
        print(f"\nno trained weights at {weights} yet - nothing to compare")
        return

    new_predictions = run_new(weights, images, args.confidence)
    new = report("the model trained on the collected frames",
                 *score(new_predictions, truths, len(names)), names)

    print()
    print(f"precision {old[0]:.1%} -> {new[0]:.1%}   "
          f"recall {old[1]:.1%} -> {new[1]:.1%}")
    if new[0] >= old[0] and new[1] >= old[1]:
        print("better on both. worth exporting and trying in the bot.")
    else:
        print("NOT better on both. do not swap it in.")


if __name__ == "__main__":
    main()
