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
                      playstyle_meta, playstyle_source, read_source)

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
    play = read_source("play.py")
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
               "pick_target", "health_of_at", "vec_len", "match_standing"}
FIGHT_CONSTS = {"RETREAT_BELOW_HEALTH", "CAUTIOUS_BELOW_HEALTH",
                "FINISH_BELOW_HEALTH", "FINISH_HEALTH_LEAD",
                "TARGET_WEAKEST_WITHIN", "ENGAGE_RADIUS_TILES", "OUTNUMBERED_BY",
                "UNDER_FIRE_SHOTS", "NO_CHASE_BEYOND", "DODGE_MIN_CONFIDENCE",
                "CAUTIOUS_CEILING", "DECLINE_EVEN_FIGHTS",
                "MATES_MEMORY", "TEAM_SIZE", "MATCH_RESET_GAP", "LATE_AFTER",
                "ENDGAME_AFTER", "GAS_MEANS_ENDGAME",
                "CAUTION_EARLY_TEAM", "CAUTION_EARLY_ALONE",
                "CAUTION_LATE_TEAM", "CAUTION_LATE_ALONE",
                "CAUTION_ENDGAME_TEAM", "CAUTION_ENDGAME_ALONE"}


class Shot:
    def __init__(self, confidence=0.9):
        self.confidence = confidence


def fight_context(clock=None):
    health = {}

    class Clock:
        @staticmethod
        def time():
            return clock[0] if clock else 1000.0

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
        time=Clock,
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


def check_standing(report):
    """Aggression follows the team you have left and how deep the match is."""
    clock = [1000.0]
    context = fight_context(clock)
    stance = lambda: context["assess_fight"](300.0, (1200.0, 500.0))
    standing = lambda: context["match_standing"]()
    GAP = context["MATCH_RESET_GAP"]
    ENDGAME = context["ENDGAME_AFTER"]
    MEMORY = context["MATES_MEMORY"]

    def play(seconds, mates=(), enemies=((1200, 500, 1.0),), mine=0.95):
        """Advance the match one plausible frame gap at a time.

        Stepping the clock in one jump would look like the lobby and silently
        restart the match, which is exactly what the reset is there to catch.
        """
        left = seconds
        while left > 0:
            hop = min(GAP / 2.0, left)
            clock[0] += hop
            left -= hop
            situation(context, mine=mine, enemies=list(enemies), mates=list(mates))
            standing()

    def reset():
        context["persistent_data"] = {}
        clock[0] = 1000.0

    report.section("the team you can see is the team you count on")
    reset()
    play(10.0, mates=[(950, 520, None)])
    report.check("a teammate in sight counts", standing()[0], 1)

    play(MEMORY / 2.0, mates=[])
    report.check("briefly out of sight is not dead", standing()[0], 1)

    play(MEMORY * 2.0, mates=[])
    report.check("gone long enough is gone", standing()[0], 0)

    report.section("the match phase comes from the clock, and from the gas")
    reset()
    play(10.0, mates=[(950, 520, None)])
    report.check("it opens early", standing()[1], "early")

    play(ENDGAME, mates=[(950, 520, None)])
    report.check("and ends in the endgame", standing()[1], "endgame")

    reset()
    play(10.0, mates=[(950, 520, None)])
    context["gas_reading"] = {"up": 900, "down": 0, "left": 0, "right": 0}
    report.check("gas reaching the player means endgame whatever the clock says",
                 standing()[1], "endgame")
    context["gas_reading"] = {"up": 0, "down": 0, "left": 0, "right": 0}

    report.section("top few, team gone: take the placement, not the fight")
    reset()
    play(10.0, mates=[(950, 520, None)])
    situation(context, mine=0.95, enemies=[(1200, 500, 1.0)], mates=[(950, 520, None)])
    report.check("early with the team, an even 1v1 is on", stance(), "fight")

    reset()
    play(10.0, mates=[(950, 520, None)])
    play(ENDGAME, mates=[])
    situation(context, mine=0.95, enemies=[(1200, 500, 1.0)], mates=[])
    report.check("alone at the end, the same fight is declined", stance(), "hold")

    situation(context, mine=0.95, enemies=[(1200, 500, 0.15)], mates=[])
    report.check("but a dying enemy is still worth finishing", stance(), "fight")

    situation(context, mine=0.95, enemies=[(1200, 500, 1.0), (1260, 560, 1.0)], mates=[])
    report.check("and two of them is still a retreat", stance(), "retreat")

    report.section("caution changes which fights are taken, it does not freeze")
    # The first attempt at this pushed the "stop closing" threshold to 90%
    # health, so the bot held position in nearly every exchange. Holding is
    # strafing on the spot, it nets no displacement, and the stall detector
    # then reports being stuck - 24% of in-match frames against 15% before.
    ceiling = context["CAUTIOUS_CEILING"]
    worst = max(context["CAUTION_ENDGAME_ALONE"], context["CAUTION_LATE_ALONE"])
    report.check("even at its most cautious the hold threshold stays sane",
                 min(context["CAUTIOUS_BELOW_HEALTH"] * worst, ceiling) <= 0.65,
                 True)

    reset()
    play(10.0, mates=[(950, 520, None)])
    play(ENDGAME, mates=[])
    situation(context, mine=0.80, enemies=[(1200, 500, 1.0)], mates=[])
    report.check("healthy enough, alone at the end, it still closes",
                 stance() in ("fight", "hold"), True)
    situation(context, mine=0.95, enemies=[(1200, 500, 0.55)], mates=[])
    report.check("and a weaker enemy is still engaged", stance(), "fight")

    report.section("having the team at the end keeps the bot in the fight")
    reset()
    play(10.0, mates=[(950, 520, None)])
    play(ENDGAME, mates=[(950, 520, None)])
    situation(context, mine=0.95, enemies=[(1200, 500, 1.0)], mates=[(950, 520, None)])
    report.check("endgame with a teammate, the even 1v1 is back on", stance(), "fight")

    report.section("a new match does not inherit the last one")
    reset()
    play(10.0, mates=[(950, 520, None)])
    play(ENDGAME, mates=[])
    report.check("ends alone in the endgame", standing()[1], "endgame")

    clock[0] += GAP * 2.0
    situation(context, mine=0.95, enemies=[(1200, 500, 1.0)], mates=[(950, 520, None)])
    report.check("the next match opens early again", standing()[1], "early")
    report.check("with its team counted", standing()[0], 1)

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


BREAKOUT_FUNCS = {"break_out_heading", "sweep_from", "normalize_move",
                  "vec_len", "random_safe_movement"}


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
    #
    # Thirteen seconds, not six. The sweep used to turn every 0.7s, which
    # covered the circle quickly and escaped nothing: a session logged 22 and
    # 23 second stalls with all eight headings tried and world displacement at
    # 0.00 throughout. A heading has to be held long enough to walk before
    # trying the next one is worth anything.
    seen = {heading_after(t / 10.0) for t in range(0, 130)}
    report.at_least("the sweep covers the circle within thirteen seconds",
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

    report.section("being pinned at a map edge must not disable the sweep")
    # escape_boundary() returns one fixed heading, and this used to overwrite
    # the sweep with it - which disabled the sweep exactly where it mattered
    # most. A real log shows the consequence: heading 180.0 repeated with
    # efficiency 0.01, because the away-from-edge direction had a wall in it
    # too and nothing ever tried anything else.
    away_from_edge = (-100.0, 0.0)
    swept = set()
    for tenth in range(130):
        context["stuck_for"] = tenth / 10.0
        vector = context["sweep_from"](away_from_edge)
        swept.add(round(math.degrees(math.atan2(vector[1], vector[0]))))
    report.at_least("seeding from the edge still sweeps", len(swept), 6)
    context["stuck_for"] = 0.0
    first = context["sweep_from"](away_from_edge)
    report.check("and it starts by heading away from the edge",
                 round(math.degrees(math.atan2(first[1], first[0]))), 180)

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


AFK_FUNCS = {"watch_for_afk", "is_afk_spot", "playing_teammates", "to_world",
             "vec_len"}
AFK_CONSTS = {"AFK_AFTER", "AFK_IGNORE_FOR", "AFK_SPOT_TILES", "IDLE_MOVE_TILES"}


def check_afk(report):
    """A teammate who never moves must be left behind, fight or no fight."""
    clock = [1000.0]

    class Clock:
        @staticmethod
        def time():
            return clock[0]

    context = lift(AFK_FUNCS, AFK_CONSTS, base_context(time=Clock))
    context["persistent_data"] = {"afk_anchor": None, "afk_since": 0.0,
                                  "afk_spot": None, "afk_spot_until": 0.0}
    AFK_AFTER = context["AFK_AFTER"]

    still = (1200.0, 500.0)

    def hold(seconds, at=still):
        """Let the clock run with the teammate parked where they are."""
        left = seconds
        while left > 0:
            step = min(1.0, left)
            clock[0] += step
            left -= step
            context["watch_for_afk"](at)

    report.section("ten seconds of standing still is enough to give up")
    context["watch_for_afk"](still)
    hold(AFK_AFTER - 2)
    report.check("not yet", context["is_afk_spot"](still), False)
    hold(3)
    report.check("now", context["is_afk_spot"](still), True)

    report.section("a fight nearby changes nothing about it")
    # Nothing in watch_for_afk consults enemies or projectiles - which is the
    # whole point, because the stagnation rule does and therefore never fired.
    context["enemy_data"] = [box(1000, 500), box(1100, 520)]
    context["projectiles"] = [object()]
    report.check("still given up on", context["is_afk_spot"](still), True)
    context["enemy_data"] = []
    context["projectiles"] = []

    report.section("they are dropped from the regroup list, others are not")
    context["teammate_data"] = [box(*still), box(400, 400)]
    kept = context["playing_teammates"]()
    report.check("one of the two survives", len(kept), 1)
    report.check("and it is the one that moves",
                 context["get_entity_pos"](kept[0])[0], 400.0)

    report.section("a teammate who moves is never given up on")
    context["persistent_data"] = {"afk_anchor": None, "afk_since": 0.0,
                                  "afk_spot": None, "afk_spot_until": 0.0}
    tiles = context["IDLE_MOVE_TILES"] * context["TILE_SIZE"]
    walker = [1200.0, 500.0]
    for _ in range(int(AFK_AFTER) + 6):
        clock[0] += 1.0
        walker[0] += tiles + 5
        context["watch_for_afk"](tuple(walker))
    report.check("never flagged", context["is_afk_spot"](tuple(walker)), False)

    report.section("giving up is temporary, not permanent")
    context["persistent_data"] = {"afk_anchor": None, "afk_since": 0.0,
                                  "afk_spot": None, "afk_spot_until": 0.0}
    context["watch_for_afk"](still)
    hold(AFK_AFTER + 1)
    report.check("flagged", context["is_afk_spot"](still), True)
    clock[0] += context["AFK_IGNORE_FOR"] + 1
    report.check("and released again later", context["is_afk_spot"](still), False)

    report.section("somewhere else on the map is not the spot")
    context["persistent_data"] = {"afk_anchor": None, "afk_since": 0.0,
                                  "afk_spot": None, "afk_spot_until": 0.0}
    context["watch_for_afk"](still)
    hold(AFK_AFTER + 1)
    far = (still[0] + context["TILE_SIZE"] * 8, still[1])
    report.check("a distant teammate is unaffected", context["is_afk_spot"](far), False)

    report.section("no teammate at all is not a crash")
    context["watch_for_afk"](None)
    report.check("survives nobody in sight", context["is_afk_spot"](None), False)


def check_two_attacks(report):
    """Nori: a tap that swings now, or a hold that charges a long shot.

    Everyone else has one attack off that button, so the tap branch has to stay
    invisible to them - a brawler who suddenly stops charging is a brawler who
    fires every shot at minimum damage.
    """
    import json as _json
    report.section("a brawler with two attacks uses the right one for the range")

    info = _json.load(open("cfg/brawlers_info.json", encoding="utf-8"))
    names = _json.load(open("cfg/names.json", encoding="utf-8"))
    report.check("nori is in the brawler table", "nori" in info, True)
    nori = info.get("nori", {})
    report.check("with a charged reach", nori.get("attack_range", 0) > 0, True)
    report.check("and a tap reach inside it",
                 0 < nori.get("quick_attack_range", 0) < nori.get("attack_range", 0), True)
    report.check("and a hold time, or the charge would never happen",
                 nori.get("hold_attack", 0) > 0, True)
    report.check("nori has name aliases too - four letters is thin for a fuzzy match",
                 len(names.get("nori", [])) > 0, True)
    report.check("every brawler in the table has a names.json entry",
                 sorted(set(info) - set(names)), [])
    report.check("nobody else grew a second attack by accident",
                 sorted(k for k, v in info.items() if v.get("quick_attack_range", 0)),
                 ["nori"])

    # The queue list draws one of these per brawler, and a missing file is a
    # blank tile rather than an error - so nothing notices until somebody sees
    # a hole in the UI.
    icons = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "assets", "brawler_icons")
    report.check("nori has an icon", os.path.exists(os.path.join(icons, "nori.png")), True)
    report.check("and so does every other brawler in the table",
                 sorted(name for name in info
                        if not os.path.exists(os.path.join(icons, f"{name}.png"))),
                 [])

    # Brawlify's API answers 403 with a Cloudflare page now, which is why new
    # brawlers stopped getting icons at all. The download goes to the CDN by
    # numeric id first, and only falls back to the old route.
    utils_src = read_source("utils.py")
    report.check("icons are fetched from the CDN, not the blocked API",
                 "cdn.brawlify.com/brawlers/borderless" in utils_src, True)
    report.check("with the old route still there for anyone without a token",
                 "api.brawlify.com/v1/brawlers" in utils_src, True)
    report.check("and ids come from Supercell, who publish them",
                 "def brawler_ids(" in read_source("brawl_api.py"), True)

    def fire(distance, hold_range, charging=False, must_hold=True):
        """Run do_attack once and report which button press came out.

        The ranges here are arbitrary - this exercises the branch, not Nori.
        His actual numbers are checked against the table above.
        """
        pressed = []
        context = base_context(
            must_hold=must_hold,
            quick_attack_range=hold_range,
            brawler_info={"attack_range": 448.0, "hold_attack": 2},
            AIMED_SHOTS=False,
            aim_enabled=False,
            persistent_data={"time_since_holding_attack": 1000.0 if charging else None},
            attack=lambda touch_up=True, touch_down=True: pressed.append(
                "tap" if (touch_up and touch_down) else
                ("press" if touch_down else "release")),
            aimed_attack=lambda target: pressed.append("aimed"),
        )
        context["time"] = _FakeClock()
        lift(["do_attack", "tap_attack"], {"QUICK_ATTACK_TAPS"}, context)
        context["do_attack"]((900.0 + distance, 500.0))
        return pressed

    # At knife range the tap is the whole attack, so it goes out several times
    # per tick instead of once - a charged shot is useless with somebody
    # standing on top of you, and one tap a frame is not pressure.
    taps = int(re.search(r"^QUICK_ATTACK_TAPS = (\d+)$",
                         playstyle_source(), re.M).group(1))
    report.at_least("close range taps more than once a tick", taps, 2)
    report.check("point blank, it swings instead of charging",
                 fire(100.0, 192.0), ["tap"] * taps)
    report.check("at range, it charges", fire(400.0, 192.0), ["press"])
    report.check("exactly at the tap reach still swings",
                 fire(192.0, 192.0), ["tap"] * taps)
    report.check("one pixel further charges", fire(193.0, 192.0), ["press"])

    # The regression that matters: Angelo and Hank have no tap, and a tap
    # branch that leaked to them would fire every shot uncharged.
    report.check("a hold brawler with no tap always charges, even point blank",
                 fire(10.0, 0.0), ["press"])
    report.check("and a brawler with no hold at all is untouched",
                 fire(10.0, 0.0, must_hold=False), ["tap"])

    # Switching mid-charge would leave the finger down and the shot unfired.
    report.check("a charge already running is finished, not swapped for a tap",
                 fire(10.0, 192.0, charging=True), ["release"])

    # Only unified_dodge is executed above; the other two are copies and copies
    # drift. These check the branch is actually in all of them.
    for path in PLAYSTYLES:
        if path.endswith("skeleton.py"):
            continue
        label = os.path.basename(path)
        text = playstyle_source(path)
        report.check(f"{label}: has the tap path",
                     "def tap_attack(" in text, True)
        report.check(f"{label}: taps repeatedly at knife range",
                     "for _ in range(QUICK_ATTACK_TAPS):" in text, True)
        report.check(f"{label}: reads the tap reach from the brawler table",
                     'brawler_info.get("quick_attack_range"' in text, True)
        report.check(f"{label}: and scales it like the other ranges",
                     "attack_range / max(float(brawler_info[\"attack_range\"]), 1.0)" in text,
                     True)


class _FakeClock:
    """time.time() that is always past any hold that has started."""

    @staticmethod
    def time():
        return 2000.0


def check_afk_identity(report, path):
    """Giving up on an ally who stands still, while another one walks.

    The bug: the mate being timed was re-chosen every frame as "whoever is
    nearest", which is not an identity. With two teammates on screen the
    nearest one changes as either of them walks, so the clock read the OTHER
    person's coordinates, concluded the watched mate had moved, and started
    again - and an ally standing perfectly still was never noticed for as long
    as somebody else nearby was moving.
    """
    clock = {"t": 0.0}

    def box(x, y):
        return [x - 20, y - 20, x + 20, y + 20]

    context = base_context()
    context["time"] = type("_T", (), {"time": staticmethod(lambda: clock["t"])})
    context["odometer"] = (0.0, 0.0)
    context["player_pos"] = (960.0, 540.0)
    context["walls"] = []
    context["debug"] = False
    context["persistent_data"] = {}

    def closest_teammate(mates, player, walls):
        best, best_gap = None, float("inf")
        for mate in mates or []:
            spot = context["get_entity_pos"](mate)
            gap = context["get_distance"](spot, player)
            if gap < best_gap:
                best, best_gap = spot, gap
        return best, best_gap

    context["find_closest_teammate"] = closest_teammate

    try:
        lifted = lift(["watched_teammate", "watch_for_afk", "to_world",
                       "is_afk_spot", "vec_len"],
                      ["AFK_AFTER", "AFK_IGNORE_FOR", "AFK_SPOT_TILES",
                       "IDLE_MOVE_TILES", "WATCH_REACQUIRE_TILES"], context)
    except AssertionError:
        return          # a playstyle without the AFK watch has nothing to check

    still_at = (700.0, 540.0)
    name = os.path.basename(path)

    def sweep(teammates_for):
        context["persistent_data"] = {}
        step = 0
        while step <= 80:
            clock["t"] = step * 0.25
            context["teammate_data"] = teammates_for(step)
            lifted["watch_for_afk"](lifted["watched_teammate"]())
            step += 1
        return context["persistent_data"].get("afk_spot")

    # One ally never moves; the other paces past it, which is what the
    # nearest-teammate rule kept latching onto.
    spot = sweep(lambda i: [box(still_at[0], still_at[1]),
                            box(640.0 + (i % 12) * 40.0, 540.0)])
    report.check(f"{name}: gives up on a still ally while another one walks",
                 spot is not None, True)

    # The other half. If the fix abandons allies who are playing it costs more
    # than the bug did.
    spot = sweep(lambda i: [box(700.0 + i * 12.0, 540.0), box(1300.0, 700.0)])
    report.check(f"{name}: an ally who is moving is never given up on",
                 spot, None)

    # And the case that always worked still works.
    spot = sweep(lambda i: [box(still_at[0], still_at[1])])
    report.check(f"{name}: a single still ally is still given up on",
                 spot is not None, True)


def main():
    report = Failures("playstyle")
    for path in PLAYSTYLES:
        check_names(report, path)
        check_afk_identity(report, path)

    # By name, not by position. These used to index PLAYSTYLES[0] and [1],
    # which silently pointed at different files the moment a third playstyle
    # sorted ahead of them alphabetically.
    def style(name):
        for path in PLAYSTYLES:
            if os.path.basename(path) == name:
                return path
        raise AssertionError(f"{name} is missing from playstyles/")

    report.section("the light variant must actually switch the tracker off")
    meta = playstyle_meta(style("unified_light.pyla"))
    report.check("unified_light declares dodge off", meta.get("dodge"), False)
    report.check("unified_dodge does not",
                 playstyle_meta(style("unified_dodge.pyla")).get("dodge"), None)
    report.check("unified_aggro keeps dodging on",
                 playstyle_meta(style("unified_aggro.pyla")).get("dodge"), None)
    # Checked over the AST, not the raw text: the file's header comment
    # explains what was removed and why, and naming a thing in prose is not
    # the same as still calling it.
    light = ast.parse(playstyle_source(style("unified_light.pyla")))
    banned = {"projectiles", "solve_dodge", "dodge_enabled", "UNDER_FIRE_SHOTS",
              "DODGE_MIN_CONFIDENCE", "DODGE_BREAKS_SPACING", "ATTACK_WHILE_DODGING"}
    leftovers = sorted({n.id for n in ast.walk(light)
                        if isinstance(n, ast.Name) and n.id in banned})
    report.check("unified_light has no projectile code left", leftovers, [])

    report.section("the aggressive variant presses where the careful one folds")
    import re as _re
    aggro = playstyle_source(style("unified_aggro.pyla"))
    careful = playstyle_source(style("unified_dodge.pyla"))

    def value(text, name):
        found = _re.search(rf"^{name} = (.+)$", text, _re.M)
        return found.group(1).strip() if found else None

    report.check("it never declines an even fight",
                 value(aggro, "DECLINE_EVEN_FIGHTS"), "False")
    report.check("while the careful one still does",
                 value(careful, "DECLINE_EVEN_FIGHTS"), "True")
    report.check("it chases further",
                 float(value(aggro, "NO_CHASE_BEYOND")) > float(value(careful, "NO_CHASE_BEYOND")),
                 True)
    report.check("it tolerates worse odds",
                 int(value(aggro, "OUTNUMBERED_BY")) > int(value(careful, "OUTNUMBERED_BY")),
                 True)
    report.check("it fights at lower health",
                 float(value(aggro, "RETREAT_BELOW_HEALTH")) < float(value(careful, "RETREAT_BELOW_HEALTH")),
                 True)
    report.check("every caution step is lower than the careful one",
                 all(float(value(aggro, key)) <= float(value(careful, key))
                     for key in ("CAUTION_EARLY_TEAM", "CAUTION_EARLY_ALONE",
                                 "CAUTION_LATE_TEAM", "CAUTION_LATE_ALONE",
                                 "CAUTION_ENDGAME_TEAM", "CAUTION_ENDGAME_ALONE")),
                 True)
    # Aggression without dodging is just dying faster, and the whole point of
    # keeping the tracker on here is that this style takes far more fire.
    report.check("it keeps the dodge layer", "solve_dodge" in aggro, True)
    report.check("and it still gives up on a fight it cannot win",
                 "return \"retreat\"" in aggro, True)

    report.section("melee brawlers stop closing once they are already shooting")
    # The attack fires at 1.0 of attack_range. Every step taken below that is
    # walking further into the enemy after the shots are landing - which is how
    # a Mortis arrives nose to nose having absorbed the entire approach.
    for name in ("unified_dodge.pyla", "unified_light.pyla", "unified_aggro.pyla"):
        text = playstyle_source(style(name))
        close = float(value(text, "ASSASSIN_CLOSE_TO"))
        tank = float(value(text, "TANK_CLOSE_TO"))
        report.at_least(f"{name}: assassins settle inside their reach, not inside the enemy",
                        close, 0.5)
        report.at_most(f"{name}: but still inside it, so the attack connects", close, 0.95)
        report.at_least(f"{name}: tanks too", tank, 0.5)
        report.at_most(f"{name}: and still closer than an assassin", tank, close)
    report.check("the aggressive style still commits harder than the careful one",
                 float(value(aggro, "ASSASSIN_CLOSE_TO")) < float(value(careful, "ASSASSIN_CLOSE_TO")),
                 True)

    check_two_attacks(report)
    check_walls(report)
    check_breakout(report)
    check_fight(report)
    check_standing(report)
    check_afk(report)
    check_idle(report)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
