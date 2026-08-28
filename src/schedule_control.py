"""Stopping by the clock, without anyone having to be at the keyboard.

Two shapes, and the difference between them is the whole file.

A stop time WITH a resume time is a recurring window: quiet between 23:30 and
08:00, every day. "After the stop time" and "before the resume time" are the
same window on two different days, which is the only fiddly part.

A stop time WITHOUT one is a deadline: stop the next time the clock reaches it,
once. That distinction was got wrong and the consequence was severe. Treating
it as a range - quiet from the stop time until midnight - means setting 04:00
late in the evening puts you inside the window immediately, so the bot stopped
the moment it was configured, and with the shutdown box ticked it powered the
machine off. A deadline cannot do that: 04:00 set at 23:50 is five hours away,
which is what anyone typing it means.
"""

from datetime import datetime, timedelta


def parse_clock(value):
    """"HH:MM" as minutes past midnight, or None if it is not a time.

    Also accepts the forms people actually type: 400, 0400, 4.00, 4 00. A time
    that fails to parse silently disables the schedule, so being fussy here
    reads to the user as the feature not working.
    """
    text = str(value or "").strip()
    if not text:
        return None

    digits = ""
    for separator in (":", ".", " ", "-"):
        if separator in text:
            left, _, right = text.partition(separator)
            digits = left.strip().zfill(2) + right.strip().zfill(2)
            break
    else:
        digits = text

    if not digits.isdigit():
        return None
    if len(digits) <= 2:
        # "4" or "04" is four o'clock, which is the only sensible reading.
        hours, minutes = int(digits), 0
    elif len(digits) == 3:
        hours, minutes = int(digits[0]), int(digits[1:])
    elif len(digits) == 4:
        hours, minutes = int(digits[:2]), int(digits[2:])
    else:
        return None

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


class Schedule:
    """When the bot should stop rather than keep playing."""

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

    def next_stop_after(self, since):
        """The first moment the clock shows stop_at at or after `since`."""
        if self.stop_at is None or since is None:
            return None
        target = since.replace(hour=self.stop_at // 60, minute=self.stop_at % 60,
                               second=0, microsecond=0)
        if target <= since:
            target += timedelta(days=1)
        return target

    def in_quiet_hours(self, now=None):
        """Only meaningful for a recurring window, i.e. with a resume time."""
        if self.stop_at is None or self.resume_at is None:
            return False
        now = now or datetime.now()
        minute = now.hour * 60 + now.minute

        if self.stop_at == self.resume_at:
            return False  # A zero-length window is not a window.
        if self.stop_at < self.resume_at:
            return self.stop_at <= minute < self.resume_at
        # Crosses midnight: 23:30 -> 08:00 is late evening OR early morning.
        return minute >= self.stop_at or minute < self.resume_at

    def holding(self, now=None, since=None):
        """(should_hold, reason).

        `since` is when the run started. It is what turns a lone stop time into
        "the next 04:00" rather than "any time past 04:00", and without it that
        shape is not evaluated at all - refusing to guess is the safe way to be
        wrong here, because guessing once shut somebody's computer down.
        """
        now = now or datetime.now()

        if self.stop_at is not None and self.resume_at is not None:
            if self.in_quiet_hours(now):
                return True, "quiet hours"
            return False, ""

        if self.stop_at is not None:
            deadline = self.next_stop_after(since)
            if deadline is not None and now >= deadline:
                return True, f"the {deadline.strftime('%H:%M')} stop time"
        return False, ""
