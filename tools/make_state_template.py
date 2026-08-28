"""Cut a state template out of a screenshot.

    venv\\Scripts\\python.exe tools\\make_state_template.py shot.png daily_wins

Every screen the bot recognises is a small picture in images/states/ plus a
region in cfg/lobby_config.toml saying where to look for it. Making one by hand
means cropping to exact pixels and remembering to scale it to 1920x1080 first,
which is easy to get slightly wrong and then spend an evening wondering why the
match never fires.

This does both from a full screenshot of the screen in question. The region is
read from lobby_config, so the crop and the search area cannot disagree - they
come from the same numbers.
"""

import argparse
import pathlib
import sys

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from utils import load_toml_as_dict  # noqa: E402

REFERENCE = (1920, 1080)
PROJECT = pathlib.Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("screenshot", help="a full screenshot of the screen")
    parser.add_argument("region", help="the key in [template_matching]")
    parser.add_argument("--name", help="output file name, defaults to the region")
    parser.add_argument("--shrink", type=float, default=0.82,
                        help="fraction of the region to keep, centred. Slightly "
                             "smaller than the search area so the match has room "
                             "to slide rather than needing pixel alignment.")
    parser.add_argument("--crop", metavar="X,Y,W,H",
                        help="cut exactly this box instead, in 1920x1080 pixels "
                             "of the whole screen. Use it when only part of the "
                             "dialog is constant - a title banner is the same "
                             "every time, while the sender's name and avatar "
                             "are not, and a template containing those matches "
                             "only the one invite it was cut from.")
    args = parser.parse_args()

    image = cv2.imread(args.screenshot)
    if image is None:
        raise SystemExit(f"could not read {args.screenshot}")

    regions = load_toml_as_dict("./cfg/lobby_config.toml")["template_matching"]
    if args.region not in regions:
        raise SystemExit(f"{args.region} is not in [template_matching]. "
                         f"Known: {', '.join(sorted(regions))}")
    x, y, w, h = regions[args.region]

    # The screenshot is whatever size the device is; every region is written
    # for 1920x1080, so it is scaled to that once rather than at match time.
    height, width = image.shape[:2]
    if (width, height) != REFERENCE:
        print(f"screenshot is {width}x{height}, scaling to 1920x1080")
        image = cv2.resize(image, REFERENCE, interpolation=cv2.INTER_AREA)

    if args.crop:
        try:
            cx, cy, cw, ch = (int(part) for part in args.crop.split(","))
        except ValueError:
            raise SystemExit("--crop wants four numbers: X,Y,W,H")
        # It has to sit inside the search region, or the matcher will never
        # look where the template came from.
        if not (x <= cx and y <= cy and cx + cw <= x + w and cy + ch <= y + h):
            raise SystemExit(
                f"--crop {[cx, cy, cw, ch]} is not inside {args.region} = {[x, y, w, h]}. "
                f"Widen the region in cfg/lobby_config.toml, or move the crop.")
        crop = image[cy:cy + ch, cx:cx + cw]
    else:
        keep = max(0.1, min(args.shrink, 1.0))
        inset_x = int(w * (1 - keep) / 2)
        inset_y = int(h * (1 - keep) / 2)
        crop = image[y + inset_y:y + h - inset_y, x + inset_x:x + w - inset_x]
    if crop.size == 0:
        raise SystemExit(f"region {args.region} = {[x, y, w, h]} is outside the image")

    out = PROJECT / "assets" / "images" / "states" / f"{args.name or args.region}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), crop)
    print(f"wrote {out}  ({crop.shape[1]}x{crop.shape[0]})")
    print("Check it looks like the thing you meant to match, then restart the bot.")
    print()
    print("It must be a part of the dialog that is the SAME every time. A title")
    print("banner is; a player name, an avatar or a trophy count is not, and a")
    print("template containing one of those matches only the screenshot it came")
    print("from. Use --crop X,Y,W,H to take just the constant part.")


if __name__ == "__main__":
    main()
