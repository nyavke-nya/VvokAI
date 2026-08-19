"""Stopping by the clock, without anyone having to be at the keyboard.

Two rules, both off unless configured:

  * a quiet window - stop at a time, start again at another. The bot finishes
    whatever match it is in and then holds; it is a pause, not a kill, so the
    queue and the session survive it.
  * a session cap - hold after so many minutes of running.

The window is allowed to cross midnight, because that is the shape almost
everyone wants: stop at 23:30, resume at 08:00. Handling that is the whole
reason this is a module rather than two lines of `if`.
"""

from datetime import datetime


def parse_clock(value):
    """"HH:MM" as minutes past midnight, or None if it is not a time."""
    text = str(value or "").strip()
    if not text:
        return None
    for separator in (":", "."):
        if separator in text:
            hours, _, minutes = text.partition(separator)
            break
    else:
        return None
    try:
        hours, minutes = int(hours), int(minutes)
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


class Schedule:
    """When the bot should be holding rather than playing."""

    def __init__(self, stop_at=None, resume_at=None, max_session_minutes=0):
        self.stop_at = parse_clock(stop_at)
        self.resume_at = parse_clock(resume_at)
        try:
            self.max_session_minutes = max(0.0, float(max_session_minutes or 0))
        except (TypeError, ValueError):
            self.max_session_minutes = 0.0

    @classmethod
    def from_config(cls, config):
        config = config or {}
        return cls(
            stop_at=config.get("stop_at"),
            resume_at=config.get("resume_at"),
            max_session_minutes=config.get("max_session_minutes", 0),
        )

    @property
    def active(self):
        return self.stop_at is not None or self.max_session_minutes > 0

    def in_quiet_hours(self, now=None):
        if self.stop_at is None:
            return False
        now = now or datetime.now()
        minute = now.hour * 60 + now.minute

        if self.resume_at is None:
            # Stop and stay stopped for the rest of the day. Without a resume
            # time there is nothing to end the window, so it ends at midnight -
            # which is also what someone who set only a stop time expects.
            return minute >= self.stop_at

        if self.stop_at == self.resume_at:
            return False  # A zero-length window is not a window.

        if self.stop_at < self.resume_at:
            return self.stop_at <= minute < self.resume_at
        # Crosses midnight: 23:30 -> 08:00 is late evening OR early morning.
        return minute >= self.stop_at or minute < self.resume_at

    def session_exhausted(self, started_at, now=None):
        if not self.max_session_minutes or not started_at:
            return False
        now = now or datetime.now()
        return (now - started_at).total_seconds() / 60.0 >= self.max_session_minutes

    def holding(self, started_at=None, now=None):
        """(should_hold, reason). The reason is for the log, and for the UI."""
        if self.in_quiet_hours(now):
            return True, "quiet hours"
        if self.session_exhausted(started_at, now):
            return True, f"session limit of {int(self.max_session_minutes)} min reached"
        return False, ""
