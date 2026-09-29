"""Data model and merge rules.

Every record carries a version, ``(updated_at, updated_by)``, where
``updated_by`` is the id of the device that made the change. Merging two
copies of the same record picks one of them whole, as the maximum under a
fixed total order, so merging is commutative, associative and idempotent:
devices can exchange records in any order, any number of times, and still
converge on the same state.

* Projects: last writer wins.
* Entries: a deleted copy beats any live copy, a closed copy beats any
  running copy, and otherwise the last writer wins. A running entry is
  never re-opened, so a "stop" made on one device cannot be undone by a
  later heartbeat from another device.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, fields


def new_id() -> str:
    return uuid.uuid4().hex


def version(record) -> tuple:
    return (record.updated_at, record.updated_by)


@dataclass
class Project:
    id: str
    name: str
    client: str = ""
    rate: float = 0.0            # charge per hour
    keywords: str = ""           # comma separated; matched against the active window title
    archived: bool = False
    updated_at: float = 0.0
    updated_by: str = ""

    def keyword_list(self) -> list[str]:
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]


@dataclass
class Entry:
    id: str
    project_id: str
    user: str
    device: str
    start: float                 # epoch seconds
    end: float                   # for a running entry: time of the last heartbeat
    running: bool = False
    note: str = ""
    source: str = "manual"       # manual | auto | idle-reclaim | split | edited
    deleted: bool = False
    updated_at: float = 0.0
    updated_by: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def to_dict(record) -> dict:
    return asdict(record)


def from_dict(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def merge_project(a: Project, b: Project) -> Project:
    return a if version(a) >= version(b) else b


def _entry_rank(e: Entry) -> tuple:
    return (e.deleted, not e.running, e.updated_at, e.updated_by)


def merge_entry(a: Entry, b: Entry) -> Entry:
    # Taking the maximum under a fixed total order makes the merge associative
    # as well as commutative and idempotent, whatever order copies arrive in.
    return a if _entry_rank(a) >= _entry_rank(b) else b
