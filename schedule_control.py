"""Stopping by the clock, without anyone having to be at the keyboard.

One rule, off unless configured: a quiet window. The bot finishes whatever
match it is in and then holds, so it is a pause rather than a kill and the
queue survives it.

The window is allowed to cross midnight, because that is the shape almost
everyone wants - stop at 23:30, resume at 08:00. Handling that is the whole
reason this is a module rather than two lines of `if`: "after the stop time"
and "before the resume time" are the same window on two different days.

There was also a session-length cap here. It is gone: a duration and a clock
time answer the same question in two different units, and having both meant
explaining which one wins. The clock time is the one people actually think in.
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

    def __init__(self, stop_at=None, resume_at=None):
        self.stop_at = parse_clock(stop_at)
        self.resume_at = parse_clock(resume_at)

    @classmethod
    def from_config(cls, config):
        config = config or {}
        return cls(stop_at=config.get("stop_at"), resume_at=config.get("resume_at"))

    @property
    def active(self):
        return self.stop_at is not None

    def in_quiet_hours(self, now=None):
        if self.stop_at is None:
            return False
        now = now or datetime.now()
        minute = now.hour * 60 + now.minute

        if self.resume_at is None:
            # Stop and stay stopped for the rest of the day. Without a resume
            # time there is nothing to end the window, so it ends at midnight -
            # which is also what somebody who set only a stop time expects.
            return minute >= self.stop_at

        if self.stop_at == self.resume_at:
            return False  # A zero-length window is not a window.

        if self.stop_at < self.resume_at:
            return self.stop_at <= minute < self.resume_at
        # Crosses midnight: 23:30 -> 08:00 is late evening OR early morning.
        return minute >= self.stop_at or minute < self.resume_at

    def holding(self, now=None):
        """(should_hold, reason). The reason is for the log, and for the UI."""
        if self.in_quiet_hours(now):
            return True, "quiet hours"
        return False, ""
