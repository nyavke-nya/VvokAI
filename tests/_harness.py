"""Shared bits for the test suite.

The playstyle is not importable - it is a .pyla script that runs inside a
sandbox with no builtins and a context the engine injects. So instead of
importing it, the tests lift individual functions out of its AST and run them
against a stub context. That keeps the tests honest: they exercise the code
that actually ships, not a copy of it that can drift.
"""

import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
os.chdir(REPO)

PLAYSTYLE = os.path.join(REPO, "playstyles", "unified_dodge.pyla")

# Every playstyle shipped with the fork. The name and signature checks run
# against all of them: a context key renamed in play.py breaks each one the
# same way, and the light variant is derived from the full one so it inherits
# any mistake made there.
# Found rather than listed. A hardcoded list means a new playstyle ships
# without ever being checked - which is exactly what happened: unified_aggro
# was added, the suite still reported the same 78 checks, and nobody would
# have noticed until it failed in a match.
PLAYSTYLES = sorted(
    os.path.join(REPO, "playstyles", name)
    for name in os.listdir(os.path.join(REPO, "playstyles"))
    if name.endswith(".pyla")
)


class Failures:
    """Collects check results so one test file reports every failure at once."""

    def __init__(self, title):
        self.title = title
        self.failed = []
        self.passed = 0
        print(f"\n=== {title} ===")

    def section(self, name):
        print(f"\n{name}")

    def check(self, label, got, want):
        ok = got == want
        self._record(ok, label, f"{got!r} (want {want!r})")

    def near(self, label, got, want, tolerance):
        ok = got is not None and abs(got - want) <= tolerance
        shown = f"{got:.3f}" if isinstance(got, float) else repr(got)
        self._record(ok, label, f"{shown} (want {want} +-{tolerance})")

    def at_most(self, label, got, limit):
        self._record(got <= limit, label, f"{got} (limit {limit})")

    def at_least(self, label, got, limit):
        self._record(got >= limit, label, f"{got} (min {limit})")

    def _record(self, ok, label, detail):
        if ok:
            self.passed += 1
        else:
            self.failed.append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: {detail}")

    def finish(self):
        print(f"\n{self.title}: {self.passed} passed, {len(self.failed)} failed")
        if self.failed:
            for label in self.failed:
                print(f"  FAILED: {label}")
            return 1
        return 0


def playstyle_source(path=None):
    """The playstyle body, without its JSON metadata header line."""
    text = open(path or PLAYSTYLE, encoding="utf-8").read()
    header, _, body = text.partition("\n")
    json.loads(header)          # the header must stay valid JSON
    return body


def playstyle_meta(path=None):
    return json.loads(open(path or PLAYSTYLE, encoding="utf-8").readline())


def lift(names, constants, context):
    """Run the named functions and constants from the playstyle in `context`.

    Everything the lifted code calls has to be present in `context`, so a name
    the engine stops providing shows up here as a NameError rather than as the
    bot standing still mid-match.
    """
    tree = ast.parse(playstyle_source())
    body = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in constants)
    ]
    body += [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]

    found = {n.name for n in body if isinstance(n, ast.FunctionDef)}
    missing = set(names) - found
    if missing:
        raise AssertionError(f"playstyle no longer defines: {sorted(missing)}")

    module = ast.Module(body=body, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<pyla>", "exec"), context)
    return context


def box(x, y, w=90.0, h=120.0):
    return [x - w / 2, y - h / 2, x + w / 2, y + h / 2]


def base_context(**overrides):
    """A stub of the context play.py hands the playstyle."""
    import math as _math

    def entity_pos(b):
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def distance(a, b):
        return _math.hypot(a[0] - b[0], a[1] - b[1])

    context = {
        "math": _math,
        "print": print,
        "debug": False,
        "TILE_SIZE": 54.0,
        "JOYSTICK_RADIUS": 100.0,
        "width": 1920.0,
        "height": 1080.0,
        "attack_range": 480.0,
        "safe_range": 300.0,
        "player_pos": (900.0, 500.0),
        "player_data": box(900, 500),
        "enemy_data": [],
        "teammate_data": [],
        "projectiles": [],
        "walls": [],
        "odometer": (0.0, 0.0),
        # No gas anywhere near the player, which is what the engine reports for
        # most of a match and for every mode that has none.
        "gas_reading": {"up": 0, "down": 0, "left": 0, "right": 0},
        "persistent_data": {},
        "health_enabled": True,
        "player_health": None,
        "get_entity_pos": entity_pos,
        "get_distance": distance,
        # Signatures here MUST match play.py exactly. A stub that accepts
        # whatever the playstyle happens to pass will happily validate a call
        # that raises TypeError in the real engine - which is precisely how a
        # three-argument call to a four-argument is_enemy_hittable shipped, and
        # stopped the bot attacking at all. test_playstyle.py now compares call
        # sites against the real definitions, but the stubs must not lie either.
        "is_enemy_hittable": lambda player_pos, enemy_pos, walls, skill_type: True,
        "is_path_blocked": lambda player, move, walls: False,
        "health_of": lambda b, hostile=None: None,
        "move_toward": lambda target: ("toward", target),
        "move_away_from": lambda target: ("away", target),
        "random_safe_movement": lambda: ("random", None),
    }
    context.update(overrides)
    return context
