"""The tracking engine.

Keeps the user's effort low by:

* switching project with a single action (``toggle``/``start``);
* pausing automatically when the user is idle and resuming when they return,
  with a one-click option to count the time away;
* starting or switching project automatically when the active window title
  matches a project's keywords (or offering a one-click suggestion);
* handing tracking over automatically when the user starts work on another
  computer (the later start wins).

The engine has no GUI code; the clock and operating-system hooks are
injectable so it can be tested.
"""

from __future__ import annotations

import time

from . import osinfo, timeline
from .models import Entry, new_id
from .timeutil import fmt_duration, fmt_time

MIN_ENTRY_SECONDS = 1.0
RESUME_ACTIVITY_SECONDS = 5.0


class Tracker:
    def __init__(self, store, settings, clock=time.time, idle_fn=osinfo.idle_seconds,
                 window_fn=osinfo.active_window_title, notify=None):
        self.store = store
        self.settings = settings
        self.clock = clock
        self.idle_fn = idle_fn
        self.window_fn = window_fn
        self.notify = notify or (lambda message: None)

        self.current: Entry | None = None
        self.paused_project: str | None = None   # project paused because the user went idle
        self.away_since: float | None = None
        self.idle_gap: tuple[float, float, str] | None = None   # (start, end, project) that can be reclaimed
        self.suggestion: str | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._suppressed: str | None = None
        self._suggestion_since = 0.0
        self._last_stop = 0.0

    @property
    def user(self) -> str:
        return self.settings.user

    @property
    def device(self) -> str:
        return self.settings.device_id

    def project_name(self, project_id: str | None) -> str:
        p = self.store.get_project(project_id) if project_id else None
        return p.name if p else "(unknown project)"

    # -- lifecycle -------------------------------------------------------

    def recover(self) -> None:
        """Close entries left running by a previous session that ended abnormally.

        A running entry's ``end`` is its last heartbeat, so at most one
        heartbeat interval is lost.
        """
        for e in self.store.running_entries():
            if e.device == self.device:
                e.running = False
                self.store.save(e)
        last = self.store.get_project(self.settings.last_project) if self.settings.last_project else None
        if self.settings.resume_on_start and last and not last.archived:
            self.start(last.id, source="auto")

    def shutdown(self) -> None:
        if self.current:
            self.stop(manual=False)

    # -- core actions ----------------------------------------------------

    def start(self, project_id: str, source: str = "manual", at: float | None = None) -> Entry:
        self._refresh_current()
        now = self.clock() if at is None else at
        if self.current:
            if self.current.project_id == project_id:
                return self.current
            self.stop(at=now, manual=False)
        entry = Entry(id=new_id(), project_id=project_id, user=self.user, device=self.device,
                      start=now, end=max(now, self.clock()), running=True, source=source)
        self.store.save(entry)
        self.current = entry
        self.paused_project = None
        self.away_since = None
        self.suggestion = None
        self._candidate = None
        self._suppressed = None
        self.settings.last_project = project_id
        return entry

    def stop(self, at: float | None = None, manual: bool = True) -> Entry | None:
        if not self.current:
            if manual:
                self.paused_project = None
            return None
        entry = self.store.get_entry(self.current.id) or self.current
        project_id = entry.project_id
        self.current = None
        self._last_stop = self.clock() if at is None else at
        if manual:
            self.paused_project = None
            self.away_since = None
            self._suppressed = project_id   # don't immediately auto-restart what the user just stopped
        if not entry.running:
            return entry                    # already closed on another device
        end = self.clock() if at is None else at
        entry.end = max(entry.start, end)
        entry.running = False
        if entry.duration < MIN_ENTRY_SECONDS:
            entry.deleted = True
        return self.store.save(entry)

    def toggle(self, project_id: str) -> None:
        self._refresh_current()
        if self.current and self.current.project_id == project_id:
            self.stop()
        else:
            self.start(project_id)

    def elapsed(self) -> float:
        return self.clock() - self.current.start if self.current else 0.0

    def heartbeat(self) -> None:
        """Record that the current entry is still running (limits loss after a crash)."""
        self._refresh_current()
        if self.current:
            self.current.end = self.clock()
            self.store.save(self.current)

    # -- idle handling ---------------------------------------------------

    def reclaim_idle(self) -> Entry | None:
        """Count the last period away from the computer as work on the paused project."""
        if not self.idle_gap:
            return None
        start, end, project_id = self.idle_gap
        self.idle_gap = None
        entry = Entry(id=new_id(), project_id=project_id, user=self.user, device=self.device,
                      start=start, end=end, source="idle-reclaim")
        return self.store.save(entry)

    def discard_idle(self) -> None:
        self.idle_gap = None

    # -- suggestions -----------------------------------------------------

    def accept_suggestion(self) -> None:
        if self.suggestion:
            self.start(self.suggestion, source="auto", at=self._switch_time(self._suggestion_since))

    def dismiss_suggestion(self) -> None:
        self._suppressed = self.suggestion
        self.suggestion = None

    def _switch_time(self, since: float) -> float:
        """Date a context switch from when the matching window appeared, not from
        when the dwell delay expired, without overlapping time already recorded."""
        floor = max(self._last_stop, self.current.start if self.current else 0.0)
        return min(self.clock(), max(since, floor))

    def match_project(self, title: str) -> str | None:
        title = title.lower()
        best, best_len = None, 0
        for p in self.store.projects():
            for keyword in p.keyword_list():
                if keyword in title and len(keyword) > best_len:
                    best, best_len = p.id, len(keyword)
        return best

    # -- periodic work ---------------------------------------------------

    def tick(self) -> None:
        """Call every few seconds."""
        now = self.clock()
        self._refresh_current()
        idle = self.idle_fn()
        if idle is not None:
            threshold = self.settings.idle_minutes * 60
            if self.current and threshold > 0 and idle >= threshold:
                away = max(self.current.start, now - idle)
                project_id = self.current.project_id
                self.stop(at=away, manual=False)
                self.paused_project, self.away_since = project_id, away
                self.notify(f"Paused {self.project_name(project_id)}: no activity since {fmt_time(away)}.")
                return
            if self.paused_project and idle < RESUME_ACTIVITY_SECONDS:
                back = now - idle
                project_id, away = self.paused_project, self.away_since or back
                self.start(project_id, source="auto", at=back)
                self.idle_gap = (away, back, project_id)
                self.notify(f"Welcome back. Resumed {self.project_name(project_id)}; "
                            f"{fmt_duration(back - away)} away was not counted.")
        self._check_context(now)

    def _check_context(self, now: float) -> None:
        if self.paused_project:
            return
        title = self.window_fn()
        if not title:
            return
        project_id = self.match_project(title)
        if project_id != self._suppressed:
            self._suppressed = None
        if project_id is None or project_id == self._suppressed or \
                (self.current and self.current.project_id == project_id):
            self._candidate = None
            if self.current and self.current.project_id == project_id:
                self.suggestion = None
            return
        if self._candidate != project_id:
            self._candidate, self._candidate_since = project_id, now
            return
        if now - self._candidate_since < self.settings.dwell_seconds:
            return
        if self.settings.auto_switch:
            self.start(project_id, source="auto", at=self._switch_time(self._candidate_since))
            self.notify(f"Switched to {self.project_name(project_id)} (matched the active window).")
        elif self.suggestion != project_id:
            self.suggestion, self._suggestion_since = project_id, self._candidate_since

    def after_sync(self) -> None:
        """Hand tracking over between devices: only the most recently started entry keeps running."""
        running = sorted(self.store.running_entries(user=self.user), key=lambda e: (e.start, e.id))
        if len(running) > 1:
            latest = running[-1]
            for e in running[:-1]:
                changes = timeline.resolve(e, latest)
                if not changes:            # stale entry that no longer overlaps
                    e.running = False
                    changes = [e]
                for record in changes:
                    self.store.save(record)
        self._refresh_current()

    def _refresh_current(self) -> None:
        if not self.current:
            return
        stored = self.store.get_entry(self.current.id)
        if stored and stored.running and not stored.deleted:
            self.current = stored
            return
        # Closed elsewhere. Continue if a split left a running continuation on this device.
        mine = [e for e in self.store.running_entries(user=self.user) if e.device == self.device]
        if mine:
            self.current = max(mine, key=lambda e: e.start)
            return
        self.current = None
        others = self.store.running_entries(user=self.user)
        if others:
            where = self.settings.device_label(others[-1].device)
            self.notify(f"Tracking moved to {where}: {self.project_name(others[-1].project_id)}.")
        else:
            self.notify("Tracking was stopped on another device.")
