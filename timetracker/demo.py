"""Sample data for demonstrations."""

from __future__ import annotations

import random
import time

from .models import Entry, Project, new_id
from .timeutil import day_start

SAMPLE_PROJECTS = [
    ("Website redesign", "Acme Ltd", 45.0, "acme, website"),
    ("Mobile app", "Acme Ltd", 55.0, "acme-app, android studio"),
    ("Data migration", "Northwind", 50.0, "northwind, migration"),
    ("Internal admin", "", 0.0, "outlook, timesheet"),
]


def seed(store, settings, days: int = 5) -> bool:
    """Add sample projects and entries if the database has no projects. Returns True if seeded."""
    if store.projects(include_archived=True):
        return False
    projects = []
    for name, client, rate, keywords in SAMPLE_PROJECTS:
        projects.append(store.save(Project(id=new_id(), name=name, client=client, rate=rate, keywords=keywords)))
    rng = random.Random(42)
    today = day_start(time.time())
    for back in range(days, 0, -1):
        day = today - back * 86400
        t = day + 9 * 3600
        while t < day + 17 * 3600:
            length = rng.choice([30, 45, 60, 90, 120]) * 60
            store.save(Entry(id=new_id(), project_id=rng.choice(projects).id, user=settings.user,
                             device=settings.device_id, start=t, end=t + length, source="manual"))
            t += length + rng.choice([0, 0, 15]) * 60
    return True
