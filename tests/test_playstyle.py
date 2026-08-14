"""The playstyle's tactical decisions, and that it can run at all.

The .pyla sandbox has no builtins and no imports: every name the script uses
has to come from the context play.py builds. A name that stops being provided
is not a startup error, it is a NameError in the middle of a match with the bot
standing still - so the first test here simply resolves every name.

The rest drive the two decisions that decide whether the bot lives: when to
leave a fight, and when to stop waiting for a teammate who is not playing.
"""

import ast
import os
import re
import sys

from _harness import (PLAYSTYLES, Failures, base_context, box, lift,
                      playstyle_meta, playstyle_source)

from utils import SAFE_GLOBALS, is_safe_ast


def check_names(report, path=None):
    label = os.path.basename(path) if path else "unified_dodge.pyla"
    report.section(f"{label}: the sandbox must be able to run it")
    source = playstyle_source(path)

    safe, error = is_safe_ast(source)
    report.check(f"{label} passes the sandbox's AST check", safe, True)
    if not safe:
        print(f"    {error}")
        return

    # Scrape the context keys straight out of play.py, so this notices when a
    # key is renamed there without the playstyle being updated.
    play = open("play.py", encoding="utf-8").read()
    start = play.index("self.context = {")
    end = play.index("\n        }", start)
    provided = set(re.findall(r"'([a-zA-Z_][a-zA-Z0-9_]*)'\s*:", play[start:end]))
    provided |= set(SAFE_GLOBALS)
    provided |= {"time", "persistent_data", "debug", "width", "height",
                 "current_brawler"}

    tree = ast.parse(source)
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            assigned.add(node.name)
            assigned.update(arg.arg for arg in node.args.args)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.id)

    known = provided | assigned | {
        "True", "False", "None", "Exception", "ValueError", "TypeError",
        "KeyError", "IndexError", "ZeroDivisionError",
    }

    unresolved = sorted({
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        and node.id not in known
    })
    report.check(f"{label} resolves every name it uses", unresolved, [])

    check_call_signatures(report, tree, play, label)


def check_call_signatures(report, tree, play, label):
    """Every engine function must be called with a workable argument count.

    A name existing is not enough. Calling a four-argument engine function with
    three arguments resolves fine, passes every name check, and then raises
    TypeError the first time a fight starts - which takes the whole playstyle
    script down with it, so the bot neither moves nor shoots. That shipped
    once; this is the test that stops it shipping twice.

    play.py is read rather than imported: importing it drags in the detector,
    torch and a device connection, none of which a signature check needs.
    """
    play_tree = ast.parse(play)

    # Map the context key back to the method it is bound to, so a rename on
    # either side is caught.
    bindings = {}
    for node in ast.walk(play_tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if (isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name) and value.value.id == "self"):
                bindings[key.value] = value.attr

    # Argument counts of the bound methods, ignoring `self`.
    limits = {}
    for node in ast.walk(play_tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args
        names = [a.arg for a in args.posonlyargs + args.args]
        if names and names[0] == "self":
            names = names[1:]
        required = len(names) - len(args.defaults)
        limits[node.name] = (max(required, 0), None if args.vararg else len(names))

    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        method = bindings.get(node.func.id)
        if method is None or method not in limits:
            continue
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue
        given = len(node.args) + len(node.keywords)
        low, high = limits[method]
        if given < low or (high is not None and given > high):
            expected = f"{low}" if high == low else f"{low}-{high if high is not None else '*'}"
            problems.append(f"{node.func.id}() line {node.lineno}: "
                            f"{given} args, engine wants {expected}")

    report.check(f"{label} calls the engine with the right argument counts", problems, [])


# ---------------------------------------------------------------------------
#  Fight assessment
# ---------------------------------------------------------------------------

FIGHT_FUNCS = {"assess_fight", "my_health", "enemy_health", "count_within",
               "pick_target", "health_of_at", "vec_len"}
FIGHT_CONSTS = {"RETREAT_BELOW_HEALTH", "CAUTIOUS_BELOW_HEALTH",
                "FINISH_BELOW_HEALTH", "FINISH_HEALTH_LEAD",
                "TARGET_WEAKEST_WITHIN", "ENGAGE_RADIUS_TILES", "OUTNUMBERED_BY",
                "UNDER_FIRE_SHOTS", "NO_CHASE_BEYOND", "DODGE_MIN_CONFIDENCE"}


class Shot:
    def __init__(self, confidence=0.9):
        self.confidence = confidence


def fight_context():
    health = {}

    def nearest_enemy(enemies, position, walls, mode):
        best = best_distance = None
        for enemy in enemies:
            point = context["get_entity_pos"](enemy)
            distance = context["get_distance"](point, position)
            if best_distance is None or distance < best_distance:
                best, best_distance = point, distance
        return best, best_distance

    context = base_context(
        find_closest_enemy=nearest_enemy,
        health_of=lambda b, hostile=None: health.get(tuple(b)),
    )
    context["_health"] = health
    return lift(FIGHT_FUNCS, FIGHT_CONSTS, context)


def situation(context, mine=None, enemies=(), mates=(), shots=0):
    """enemies / mates as (x, y, health_or_None)."""
    context["player_health"] = mine
    context["enemy_data"] = [box(x, y) for x, y, _ in enemies]
    context["teammate_data"] = [box(x, y) for x, y, _ in mates]
    context["projectiles"] = [Shot() for _ in range(shots)]
    context["_health"].clear()
    for (_, _, health), entity in zip(enemies, context["enemy_data"]):
        if health is not None:
            context["_health"][tuple(entity)] = health


def check_fight(report):
    context = fight_context()
    stance = lambda: context["assess_fight"](300.0, (1200.0, 500.0))

    report.section("health is the last word on whether to stay")
    situation(context, mine=0.25, enemies=[(1200, 500, 1.0)],
              mates=[(950, 520, None), (960, 540, None)])
    report.check("hurt, with two friends nearby, still leaves", stance(), "retreat")

    situation(context, mine=0.20, enemies=[(1200, 500, 0.10)], mates=[(950, 520, None)])
    report.check("hurt, against a dying enemy, still leaves", stance(), "retreat")

    situation(context, mine=0.45, enemies=[(1200, 500, 0.95)], mates=[(950, 520, None)])
    report.check("wounded against a healthy enemy holds", stance(), "hold")

    situation(context, mine=0.45, enemies=[(1200, 500, 0.30)], mates=[(950, 520, None)])
    report.check("wounded but clearly ahead keeps fighting", stance(), "fight")

    report.section("headcount rules still apply when healthy")
    situation(context, mine=0.95, enemies=[(1200, 500, 1.0), (1250, 540, 1.0)])
    report.check("2v1", stance(), "retreat")

    situation(context, mine=0.95, enemies=[(1200, 500, 1.0)], mates=[(950, 520, None)])
    report.check("1v1", stance(), "fight")

    situation(context, mine=1.0, enemies=[(1200, 500, 1.0)],
              mates=[(950, 520, None)], shots=2)
    report.check("under sustained fire", stance(), "retreat")

    report.section("a nearly-dead enemy is worth pressing, within reason")
    situation(context, mine=0.9, enemies=[(1200, 500, 0.15), (1260, 560, 1.0)],
              mates=[(950, 520, None)])
    report.check("2v1 where one is nearly dead", stance(), "fight")

    situation(context, mine=0.9,
              enemies=[(1200, 500, 0.15), (1260, 560, 1.0), (1300, 600, 1.0)])
    report.check("3v1 is still too many", stance(), "retreat")

    report.section("unreadable health must fall back, never assume full")
    situation(context, mine=None, enemies=[(1200, 500, None)], mates=[(950, 520, None)])
    report.check("unknown health, 1v1", stance(), "fight")

    situation(context, mine=None, enemies=[(1200, 500, None), (1250, 540, None)])
    report.check("unknown health, 2v1", stance(), "retreat")

    context["health_enabled"] = False
    situation(context, mine=0.10, enemies=[(1200, 500, 1.0)], mates=[(950, 520, None)])
    report.check("health disabled ignores it entirely", stance(), "fight")
    context["health_enabled"] = True

    report.section("shoot whoever is closest to dying, not closest")
    situation(context, mine=1.0, enemies=[(1000, 500, 0.9), (1200, 500, 0.15)])
    report.check("switches to the wounded one further away",
                 round(context["pick_target"]()[0][0]), 1200)

    situation(context, mine=1.0, enemies=[(1000, 500, 0.5), (1200, 500, 0.45)])
    report.check("near-equal health keeps the nearest",
                 round(context["pick_target"]()[0][0]), 1000)

    situation(context, mine=1.0, enemies=[(1000, 500, 0.9), (2400, 500, 0.05)])
    report.check("a wounded enemy out of range is ignored",
                 round(context["pick_target"]()[0][0]), 1000)

    situation(context, mine=1.0, enemies=[(1000, 500, None), (1200, 500, None)])
    report.check("no health data falls back to nearest",
                 round(context["pick_target"]()[0][0]), 1000)


# ---------------------------------------------------------------------------
#  Stagnation
# ---------------------------------------------------------------------------

IDLE_FUNCS = {"update_stagnation", "to_world", "vec_len", "map_center",
              "center_run_active", "head_to_center"}
IDLE_CONSTS = {"IDLE_AFTER", "IDLE_MOVE_TILES", "IDLE_COMMIT", "REGROUP_TILES"}


def check_idle(report):
    clock = [1000.0]

    class Clock:
        @staticmethod
        def time():
            return clock[0]

    context = lift(IDLE_FUNCS, IDLE_CONSTS, base_context(time=Clock))

    def reset():
        context["persistent_data"] = {"last_activity": 0.0, "idle_anchor": None,
                                      "center_run_until": 0.0}
        context["enemy_data"] = []
        context["projectiles"] = []
        context["odometer"] = (0.0, 0.0)
        clock[0] = 1000.0

    def step(seconds, mate=None, mate_distance=None, enemies=(), shots=(),
             bot_moves=0.0):
        clock[0] += seconds
        context["enemy_data"] = list(enemies)
        context["projectiles"] = list(shots)
        context["odometer"] = (context["odometer"][0] + bot_moves,
                               context["odometer"][1])
        return context["update_stagnation"](mate, mate_distance)

    mate = (950.0, 500.0)

    report.section("an idle teammate must not hold the bot forever")
    reset()
    step(0.0, mate, 50.0)
    report.check("nothing yet at 3s", step(3.0, mate, 50.0), False)
    report.check("gives up at 6s", step(3.0, mate, 50.0), True)

    report.section("the bot's own strafing is not 'something happening'")
    reset()
    step(0.0, mate, 50.0)
    result = False
    for _ in range(6):
        # Circling: the odometer moves a long way, the teammate does not.
        result = step(1.0, (mate[0] - context["odometer"][0], mate[1]), 50.0,
                      bot_moves=200.0)
    report.check("still gives up after 6s of circling", result, True)

    report.section("a teammate who is playing keeps the bot with them")
    reset()
    step(0.0, mate, 50.0)
    walking, result = mate, False
    for _ in range(8):
        walking = (walking[0] + 150.0, walking[1])
        result = step(1.0, walking, 50.0)
    report.check("never triggers while the mate walks", result, False)

    report.section("enemies and incoming fire reset the clock")
    reset()
    step(0.0, mate, 50.0)
    step(4.0, mate, 50.0)
    report.check("an enemy appears", step(1.0, mate, 50.0, enemies=[box(1200, 500)]), False)
    report.check("2s later still not idle", step(2.0, mate, 50.0), False)
    report.check("5s after it leaves", step(5.0, mate, 50.0), True)

    reset()
    step(0.0, mate, 50.0)
    step(4.5, mate, 50.0)
    report.check("a shot inbound", step(0.5, mate, 50.0, shots=[object()]), False)

    report.section("walking to a distant teammate is not stagnation")
    reset()
    step(0.0, mate, 900.0)
    result = False
    for _ in range(8):
        result = step(1.0, mate, 900.0)
    report.check("a far teammate never triggers it", result, False)

    report.section("alone with nothing happening")
    reset()
    step(0.0, None, None)
    report.check("nothing at 3s", step(3.0, None, None), False)
    report.check("gives up at 6s", step(3.0, None, None), True)

    report.section("the run to the centre commits, then releases")
    reset()
    context["head_to_center"]()
    report.check("committed", context["center_run_active"](), True)
    clock[0] += 7.0
    report.check("released after IDLE_COMMIT", context["center_run_active"](), False)


# rotate_movement is supplied by the engine, not defined in the playstyle.
WALL_FUNCS = {"first_unblocked", "is_blocked", "sidestep_options",
              "normalize_move", "vec_len"}


def check_walls(report):
    """A blocked heading must never survive to the joystick.

    The bot used to walk straight into the sector its own overlay was drawing
    in red. first_unblocked() returned its `fallback` when every offered move
    was blocked, and `fallback` is normally the move that was just rejected.
    """
    blocked = {"north": True}

    def path_blocked(player, move, walls):
        # Solid rock to the north; everything else is walkable.
        length = (move[0] ** 2 + move[1] ** 2) ** 0.5
        return bool(blocked.get("north")) and length > 0 and move[1] / length < -0.6

    def rotate(move, radians):
        import math as m
        c, s = m.cos(radians), m.sin(radians)
        return (move[0] * c - move[1] * s, move[0] * s + move[1] * c)

    context = lift(WALL_FUNCS, {"REGROUP_TILES"},
                   base_context(is_path_blocked=path_blocked, rotate_movement=rotate))

    north = (0.0, -100.0)
    report.check("the test wall is actually blocking north",
                 context["is_blocked"](north), True)

    report.section("a blocked heading must be bent, never returned")
    chosen = context["first_unblocked"]([north], north)
    report.check("first_unblocked no longer hands back the blocked fallback",
                 context["is_blocked"](chosen), False)
    report.check("...and it does not just give up either", chosen != (0, 0), True)

    bent = context["first_unblocked"](context["sidestep_options"](north), north)
    report.check("the wall guard's own call bends out of the wall",
                 context["is_blocked"](bent), False)

    report.section("boxed in on every side")
    blocked["north"] = False

    def all_blocked(player, move, walls):
        return (move[0] ** 2 + move[1] ** 2) ** 0.5 > 0

    context["is_path_blocked"] = all_blocked
    report.check("standing still beats grinding into rock",
                 context["first_unblocked"]([north], north), (0, 0))


BREAKOUT_FUNCS = {"break_out_heading", "normalize_move", "vec_len",
                  "random_safe_movement"}


def check_breakout(report):
    """A wedged bot must try somewhere else, not lean on the same spot.

    Breaking out used to aim straight at the target and never reconsider. With
    the target on the far side of a wall the heading was hopeless from the
    first frame, and nothing in the logic could notice: one session logged
    stuck_for climbing to 44 seconds with the commanded vector unchanged and
    the world not moving a pixel.
    """
    import math

    state = {"stuck_for": 0.0, "blocked": set()}

    def rotate(move, radians):
        cos, sin = math.cos(radians), math.sin(radians)
        return (move[0] * cos - move[1] * sin, move[0] * sin + move[1] * cos)

    def direction_blocked(vector, now=None):
        length = math.hypot(vector[0], vector[1])
        if length < 1e-6:
            return False
        for angle in state["blocked"]:
            dot = (vector[0] / length) * math.cos(angle) + (vector[1] / length) * math.sin(angle)
            if dot > 0.92:
                return True
        return False

    context = base_context(rotate_movement=rotate,
                           is_direction_blocked=direction_blocked)
    context["stuck_for"] = 0.0
    lift(BREAKOUT_FUNCS, {"BREAK_OUT_SWEEP"}, context)

    def heading_after(seconds):
        context["stuck_for"] = seconds
        state["stuck_for"] = seconds
        vector = context["break_out_heading"]((1800.0, 500.0))
        return round(math.degrees(math.atan2(vector[1], vector[0])))

    report.section("a wedged bot must not hold one heading forever")
    # Sampled finely: the sweep advances every BREAK_OUT_SWEEP seconds, so
    # coarse sampling can step straight over one of the headings.
    seen = {heading_after(t / 10.0) for t in range(0, 60)}
    report.at_least("the sweep covers several headings in six seconds",
                    len(seen), 6)
    report.check("straight at the target is tried first", heading_after(0.0), 0)
    report.check("and straight back is reached", 180 in seen or -180 in seen, True)

    report.section("one heading is HELD, not re-picked every frame")
    # This is what made the bot spin on the spot. An earlier version filtered
    # the sweep through the motion monitor's blocked-direction memory, which
    # holds five directions and churns every frame as stalls are recorded and
    # expire - so the surviving offset changed frame to frame, and because
    # breaking out sets sharp_movement, every change snapped instantly.
    state["blocked"] = {0.0, 1.571, -1.571, 2.356, 3.142}
    held = {heading_after(0.30 + i * 0.01) for i in range(30)}
    report.check("the heading does not move within one sweep step",
                 len(held), 1)

    report.section("a churning blocked-direction memory changes nothing")
    before = heading_after(0.3)
    state["blocked"] = {i * math.pi / 8 for i in range(-8, 9)}
    after = heading_after(0.3)
    report.check("same heading with everything marked blocked", after, before)

    report.section("it never gives up and stands still")
    for tenth in range(60):
        vector = context["break_out_heading"]((1800.0, 500.0))
        if round(math.hypot(vector[0], vector[1])) < 1:
            report.check("a zero vector was produced at t=%.1f" % (tenth / 10.0),
                         True, False)
            break
        context["stuck_for"] = tenth / 10.0
    else:
        report.check("it keeps pushing at every point in the sweep", True, True)


def main():
    report = Failures("playstyle")
    for path in PLAYSTYLES:
        check_names(report, path)

    report.section("the light variant must actually switch the tracker off")
    meta = playstyle_meta(PLAYSTYLES[1])
    report.check("unified_light declares dodge off", meta.get("dodge"), False)
    report.check("unified_dodge does not", playstyle_meta(PLAYSTYLES[0]).get("dodge"), None)
    # Checked over the AST, not the raw text: the file's header comment
    # explains what was removed and why, and naming a thing in prose is not
    # the same as still calling it.
    light = ast.parse(playstyle_source(PLAYSTYLES[1]))
    banned = {"projectiles", "solve_dodge", "dodge_enabled", "UNDER_FIRE_SHOTS",
              "DODGE_MIN_CONFIDENCE", "DODGE_BREAKS_SPACING", "ATTACK_WHILE_DODGING"}
    leftovers = sorted({n.id for n in ast.walk(light)
                        if isinstance(n, ast.Name) and n.id in banned})
    report.check("unified_light has no projectile code left", leftovers, [])

    check_walls(report)
    check_breakout(report)
    check_fight(report)
    check_idle(report)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
