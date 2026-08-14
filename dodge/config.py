"""Typed access to cfg/dodge_config.toml.

Values in the toml are written for a 1920x1080 frame. Everything that is a
length in pixels gets multiplied by the window controller's scale factor here,
once, so no other module has to remember to do it.
"""

import math


REFERENCE_TILE = 54.0


def _f(section, key, default):
    try:
        return float(section.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(section, key, default):
    try:
        return int(section.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _b(section, key, default):
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _pair(section, key, default):
    value = section.get(key, default)
    try:
        low, high = int(value[0]), int(value[1])
    except (TypeError, ValueError, IndexError):
        low, high = int(default[0]), int(default[1])
    return (min(low, high), max(low, high))


class DodgeConfig:
    """Flat, already-scaled view of the dodge configuration."""

    def __init__(self, raw=None, scale_factor=1.0, tile_size=REFERENCE_TILE):
        raw = raw or {}
        scale = float(scale_factor or 1.0)
        if scale <= 0:
            scale = 1.0

        self.scale = scale
        # Tile size in real screen pixels, used everywhere a distance is
        # expressed "in tiles" so the tuning stays resolution independent.
        self.tile = float(tile_size or REFERENCE_TILE) * scale
        area_scale = scale * scale

        general = raw.get("general", {})
        self.enabled = _b(general, "enabled", True)
        self.threaded = _b(general, "threaded", True)
        self.debug_overlay = _b(general, "debug_overlay", True)
        self.debug_show_candidates = _b(general, "debug_show_candidates", True)
        self.log_stats = _b(general, "log_stats", False)
        self.tracker_max_fps = max(0.0, _f(general, "tracker_max_fps", 60.0))

        logging = raw.get("logging", {})
        self.log_enabled = _b(logging, "enabled", False)
        self.log_path = str(logging.get("path", "debug_frames/dodge_log.jsonl"))
        self.log_candidates = _b(logging, "include_candidates", True)
        self.log_max_bytes = int(_f(logging, "max_megabytes", 32) * 1024 * 1024)

        vision = raw.get("vision", {})
        # 0 means "pick a divisor that lands near target_width", so the tracker
        # keeps its working resolution when the capture resolution changes.
        self.downscale = max(0, _i(vision, "downscale", 0))
        self.target_width = max(160, _i(vision, "target_width", 640))
        self.motion_threshold = max(1, _i(vision, "motion_threshold", 20))
        self.min_blob_area = _f(vision, "min_blob_area", 250.0) * area_scale
        self.max_blob_area = _f(vision, "max_blob_area", 9000.0) * area_scale
        self.max_blob_aspect = _f(vision, "max_blob_aspect", 4.5)
        self.morph_open = _b(vision, "morph_open", True)

        camera = raw.get("camera", {})
        self.camera_enabled = _b(camera, "enabled", True)
        # Patch geometry lives in tracker.CAMERA_PATCHES, not here: it encodes
        # where the player and the HUD sit, which is a property of the game
        # rather than something worth exposing as a knob.
        self.camera_estimate_downscale = max(1, _i(camera, "estimate_downscale", 1))
        self.camera_min_response = _f(camera, "min_response", 0.03)
        self.camera_max_shift = _f(camera, "max_shift", 400.0) * scale

        palette = raw.get("palette", {})
        self.palette_mode = str(palette.get("mode", "soft")).strip().lower()
        if self.palette_mode not in {"off", "soft", "hard"}:
            self.palette_mode = "soft"
        self.palette_adaptive = _b(palette, "adaptive", True)
        self.palette_top_bins = max(1, _i(palette, "adaptive_top_bins", 6))
        self.palette_smoothing = min(max(_f(palette, "adaptive_smoothing", 0.9), 0.0), 0.999)
        self.warm_hue = _pair(palette, "warm_h", (0, 27))
        self.green_hue = _pair(palette, "green_h", (30, 76))
        self.water_hue = _pair(palette, "water_h", (92, 114))
        self.terrain_min_saturation = _i(palette, "terrain_min_saturation", 55)
        self.glow_min_value = _i(palette, "glow_min_value", 232)
        self.glow_max_saturation = _i(palette, "glow_max_saturation", 45)

        origin = raw.get("origin", {})
        self.require_enemy_origin = _b(origin, "require_enemy_origin", True)
        self.spawn_radius = _f(origin, "spawn_radius_tiles", 3.2) * self.tile
        self.min_outward_cosine = _f(origin, "min_outward_cosine", 0.35)
        self.allow_unknown_origin = _b(origin, "allow_unknown_origin", True)
        self.unknown_confirm_hits = max(2, _i(origin, "unknown_origin_confirm_hits", 5))
        self.unknown_max_aim_error = math.radians(
            _f(origin, "unknown_origin_max_aim_error_deg", 26.0)
        )
        self.entity_mask_inflate = _f(origin, "entity_mask_inflate_tiles", 0.55) * self.tile
        self.wall_mask_inflate = _f(origin, "wall_mask_inflate_tiles", 0.15) * self.tile

        tracking = raw.get("tracking", {})
        self.min_confirm_hits = max(2, _i(tracking, "min_confirm_hits", 3))
        self.max_missed_frames = max(1, _i(tracking, "max_missed_frames", 3))
        self.min_speed = _f(tracking, "min_speed", 260.0) * scale
        self.max_speed = _f(tracking, "max_speed", 4200.0) * scale
        self.gate_base = _f(tracking, "gate_base_tiles", 0.9) * self.tile
        self.gate_slack = _f(tracking, "gate_slack", 0.6)
        # How far a brand-new track is allowed to search on its second frame,
        # expressed as a speed because that is what it physically is: the
        # fastest shot we are willing to pick up. A one-sample track has no
        # measured velocity, so this is the only thing sizing its gate.
        self.seed_speed = _f(tracking, "seed_speed", 2600.0) * scale
        self.merge_radius = _f(tracking, "merge_radius_tiles", 0.55) * self.tile
        self.seed_agreement = _f(tracking, "seed_agreement", 0.65)
        self.velocity_window = max(2, _i(tracking, "velocity_window", 5))
        self.min_straightness = _f(tracking, "min_straightness", 0.90)
        self.min_radius = _f(tracking, "min_radius", 14.0) * scale
        self.max_radius = _f(tracking, "max_radius", 46.0) * scale

        dodge = raw.get("dodge", {})
        self.horizon = max(0.05, _f(dodge, "horizon", 0.55))
        self.critical_time = max(0.02, _f(dodge, "critical_time", 0.22))
        self.candidate_directions = max(4, _i(dodge, "candidate_directions", 16))
        self.safety_margin = _f(dodge, "safety_margin", 12.0) * scale
        self.default_player_speed = _f(dodge, "default_player_speed", 330.0) * scale
        self.tactical_weight = max(0.0, _f(dodge, "tactical_weight", 0.35))
        self.wall_penalty = max(0.0, _f(dodge, "wall_penalty", 2.5))
        self.commit_time = max(0.0, _f(dodge, "commit_time", 0.14))
        # Total round-trip lag: device encode + stream + decode + our analysis,
        # plus the joystick event travelling back. Shots are extrapolated by
        # this much before the escape is solved.
        self.reaction_latency = max(0.0, _f(dodge, "reaction_latency", 0.12))
        self.escape_hold_extra = max(0.0, _f(dodge, "escape_hold_extra", 0.25))

        aim = raw.get("aim", {})
        self.aim_enabled = _b(aim, "enabled", True)
        self.aim_projectile_speed = _f(aim, "projectile_speed", 2600.0) * scale
        self.aim_min_lead = max(0.0, _f(aim, "min_lead", 0.0))
        self.aim_max_lead = max(self.aim_min_lead, _f(aim, "max_lead", 0.45))
        self.aim_min_samples = max(2, _i(aim, "min_samples", 3))
        self.aim_max_enemy_speed = _f(aim, "max_enemy_speed", 900.0) * scale
        self.aim_match_gate = _f(aim, "match_gate_tiles", 2.0) * self.tile
        self.aim_max_missed = max(1, _i(aim, "max_missed", 3))
        self.aim_swipe_radius = _f(aim, "swipe_radius", 130.0) * scale
        self.aim_swipe_hold = max(0.0, _f(aim, "swipe_hold", 0.02))
        self.aim_min_lead_distance = _f(aim, "min_lead_distance", 18.0) * scale

        health = raw.get("health", {})
        self.health_enabled = _b(health, "enabled", True)
        # Search band above the box, as fractions of the box height.
        self.health_band_top = _f(health, "band_top", 0.55)
        self.health_band_bottom = _f(health, "band_bottom", 0.05)
        self.health_search_pad = _f(health, "search_pad", 0.25)
        # Bars are wider than this many pixels; anything shorter is scenery.
        self.health_min_bar_pixels = max(4, int(_f(health, "min_bar_pixels", 14) * scale))
        self.health_expected_width = _f(health, "expected_width", 0.9)
        self.health_min_confidence = _f(health, "min_confidence", 0.35)
        self.health_recover_blend = min(max(_f(health, "recover_blend", 0.3), 0.0), 1.0)
        self.health_green_hue = _pair(health, "green_hue", (38, 92))
        self.health_red_hue = _i(health, "red_hue", 12)
        self.health_sat_min = _i(health, "saturation_min", 120)
        self.health_val_min = _i(health, "value_min", 110)
        self.health_spent_sat_min = _i(health, "spent_saturation_min", 40)
        self.health_spent_val_min = _i(health, "spent_value_min", 20)
        self.health_spent_val_max = _i(health, "spent_value_max", 110)
        # Structure, not colour. These are what stop a patch of grass being
        # read as a full health bar.
        self.health_outline_value_max = _i(health, "outline_value_max", 70)
        self.health_outline_coverage = _f(health, "outline_coverage", 0.5)
        self.health_max_thickness = _f(health, "max_thickness", 0.22)
        self.health_min_thickness_px = max(4, int(_f(health, "min_thickness_px", 14) * scale))
        self.health_row_candidates = max(1, _i(health, "row_candidates", 8))

        movement = raw.get("movement", {})
        self.smoothing_enabled = _b(movement, "smoothing_enabled", True)
        self.smooth_tau = max(0.001, _f(movement, "smooth_tau", 0.16))
        self.max_turn_rate = math.radians(max(1.0, _f(movement, "max_turn_rate", 520.0)))
        self.sharp_on_dodge = _b(movement, "sharp_on_dodge", True)
        self.recover_time = max(0.0, _f(movement, "recover_time", 0.18))

    @classmethod
    def load(cls, scale_factor=1.0, tile_size=REFERENCE_TILE, path="cfg/dodge_config.toml"):
        from utils import load_toml_as_dict

        return cls(load_toml_as_dict(path), scale_factor=scale_factor, tile_size=tile_size)
