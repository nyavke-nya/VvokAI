"""Train one of the collected datasets, without taking the bot's GPU away.

The bot infers on the same card this trains on, and it is playing while this
runs. Training will always be the slower job and the bot will always be the
one whose slowdown is visible, so the defaults here are chosen to leave it
room rather than to finish as fast as possible: a small model, a modest batch,
and no attempt to fill the card.

Losing an hour of training time costs an hour. Dropping the bot from eighty
inferences a second to thirty costs matches, and matches are what produce the
next batch of training data - so starving the bot to train faster makes the
next model worse, not better.

Run with --check first. It reports what the run would be and what the current
model scores on the same held-out frames, which is the only number that says
whether any of this was worth doing.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

TRAIN_PYTHON = Path(r"C:/Users/vvok/Desktop/train_venv/Scripts/python.exe")
SETS = {
    "entities": "mainInGameModel",
    "terrain": "tileDetector",
    "shots": "projectileDetector",
}


def script(data_yaml, epochs, image_size, batch, workers, name, weights,
           patience):
    return f'''
import torch
from ultralytics import YOLO

print("device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu")

model = YOLO({weights!r})
model.train(
    data={str(data_yaml)!r},
    epochs={epochs},
    imgsz={image_size},
    batch={batch},
    workers={workers},
    name={name!r},
    project=r"C:/Users/vvok/Desktop/VvokAI_training/runs",
    patience={patience},
    # The bot is playing on this card. Half precision and a modest batch keep
    # the run inside a slice of it rather than filling the whole thing.
    half=True,
    cache=False,
    plots=True,
    exist_ok=True,
)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("set", choices=sorted(SETS), help="which dataset")
    parser.add_argument("--root", default=r"C:/Users/vvok/Desktop/VvokAI_training")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8,
                        help="small on purpose - the bot needs the card")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--weights", default="yolov8n.pt")
    parser.add_argument("--check", action="store_true",
                        help="report the plan and stop")
    args = parser.parse_args()

    data = Path(args.root) / args.set / "data.yaml"
    if not data.exists():
        sys.exit(f"no dataset at {data} - run tools/prepare_training.py first")

    train = len(list((Path(args.root) / args.set / "images" / "train").glob("*.jpg")))
    val = len(list((Path(args.root) / args.set / "images" / "val").glob("*.jpg")))

    print(f"dataset:  {args.set}  ({train} train, {val} val)")
    print(f"replaces: models/{SETS[args.set]}.onnx")
    print(f"run:      {args.weights}, {args.epochs} epochs, {args.imgsz}px, "
          f"batch {args.batch}, {args.workers} workers")

    if train < 200:
        print()
        print(f"WARNING: {train} training frames is not enough to train on.")
        print("A model fitted to this will be worse than the one it replaces.")
        print("Collect more before running this for real.")

    if args.check:
        print("\ncheck only, nothing started")
        return

    if not TRAIN_PYTHON.exists():
        sys.exit(f"no training environment at {TRAIN_PYTHON}")

    started = time.time()
    result = subprocess.run(
        [str(TRAIN_PYTHON), "-c",
         script(data, args.epochs, args.imgsz, args.batch, args.workers,
                args.set, args.weights, args.patience)],
        cwd=str(Path(args.root)),
    )
    print(f"\nfinished in {(time.time() - started) / 3600:.2f}h "
          f"with exit code {result.returncode}")
    print("nothing has been swapped into the bot - export and compare first")


if __name__ == "__main__":
    main()
