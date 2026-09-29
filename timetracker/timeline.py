"""Detecting and resolving overlapping time entries.

A person can only work on one thing at a time, so two entries for the same
user that overlap indicate a conflict, typically from working on two
computers. Resolution uses "latest start wins": the entry that started
later is assumed to be what the user switched to, and the earlier entry is
trimmed (or split around it). The rule is deterministic, so two devices
resolving the same conflict produce the same result.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from itertools import groupby

from .models import Entry

TOLERANCE = 1.0   # seconds of overlap ignored (clock jitter between devices)


def find_overlaps(entries: list[Entry]) -> list[tuple[Entry, Entry]]:
    live = sorted((e for e in entries if not e.deleted), key=lambda e: (e.user, e.start, e.id))
    pairs = []
    for _, group in groupby(live, key=lambda e: e.user):
        active: list[Entry] = []
        for e in group:
            active = [a for a in active if a.end > e.start + TOLERANCE]
            pairs.extend((a, e) for a in active)
            active.append(e)
    return pairs


def overlapping_ids(entries: list[Entry]) -> set[str]:
    return {e.id for pair in find_overlaps(entries) for e in pair}


def resolve(a: Entry, b: Entry) -> list[Entry]:
    """Return the records to save so that ``a`` and ``b`` no longer overlap."""
    earlier, later = sorted((replace(a), replace(b)), key=lambda e: (e.start, e.id))
    if earlier.end <= later.start + TOLERANCE:
        return []

    if earlier.start >= later.start:
        # Same start time: the other entry keeps the slot.
        if earlier.end > later.end and not later.running:
            earlier.start = later.end
        else:
            earlier.deleted = True
            earlier.running = False
        return [earlier]

    original_end, was_running = earlier.end, earlier.running
    earlier.end = later.start
    earlier.running = False
    changes = [earlier]
    if original_end > later.end and not later.running:
        # ``later`` sits inside ``earlier``: keep the part after it as a new entry.
        # The id is derived from both entries so every device creates the same one.
        tail_id = uuid.uuid5(uuid.NAMESPACE_OID, f"{earlier.id}/{later.id}").hex
        changes.append(replace(earlier, id=tail_id, start=later.end, end=original_end,
                               running=was_running, source="split"))
    return changes
