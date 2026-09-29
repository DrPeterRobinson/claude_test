"""Local SQLite storage for one device."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import fields

from .models import Entry, Project, merge_entry, merge_project

_TABLES = {Project: "projects", Entry: "entries"}
_BOOLS = {"archived", "running", "deleted"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    "id" TEXT PRIMARY KEY, "name" TEXT, "client" TEXT, "rate" REAL,
    "keywords" TEXT, "archived" INTEGER, "updated_at" REAL, "updated_by" TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    "id" TEXT PRIMARY KEY, "project_id" TEXT, "user" TEXT, "device" TEXT,
    "start" REAL, "end" REAL, "running" INTEGER, "note" TEXT, "source" TEXT,
    "deleted" INTEGER, "updated_at" REAL, "updated_by" TEXT
);
CREATE INDEX IF NOT EXISTS entries_start ON entries("start");
"""


class Store:
    def __init__(self, path: str, device_id: str):
        self.device_id = device_id
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.seen_snapshots: dict = {}     # sync bookkeeping: snapshot path -> (content hash, device, name)

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # -- low level -------------------------------------------------------

    def _write(self, record, commit: bool = True) -> None:
        names = [f.name for f in fields(record)]
        cols = ", ".join(f'"{n}"' for n in names)
        marks = ", ".join("?" for _ in names)
        values = [int(v) if isinstance(v, bool) else v for v in (getattr(record, n) for n in names)]
        self.conn.execute(f"INSERT OR REPLACE INTO {_TABLES[type(record)]} ({cols}) VALUES ({marks})", values)
        if commit:
            self.conn.commit()

    @staticmethod
    def _load(cls, row):
        data = dict(row)
        for key in _BOOLS & data.keys():
            data[key] = bool(data[key])
        return cls(**data)

    def _get(self, cls, record_id: str):
        row = self.conn.execute(f'SELECT * FROM {_TABLES[cls]} WHERE "id" = ?', (record_id,)).fetchone()
        return self._load(cls, row) if row else None

    # -- public API ------------------------------------------------------

    def save(self, record):
        """Store a local change, stamping it with a new version."""
        old = self._get(type(record), record.id)
        stamp = time.time()
        if old is not None and old.updated_at >= stamp:
            stamp = old.updated_at + 0.001    # a local edit must supersede what it edits
        record.updated_at = stamp
        record.updated_by = self.device_id
        self._write(record)
        return record

    def merge(self, remote, commit: bool = True) -> bool:
        """Merge a record received from another device. Returns True if local state changed."""
        local = self._get(type(remote), remote.id)
        if local is None:
            merged = remote
        elif isinstance(remote, Entry):
            merged = merge_entry(local, remote)
        else:
            merged = merge_project(local, remote)
        if merged == local:
            return False
        self._write(merged, commit=commit)
        return True

    def merge_many(self, cls, records: list) -> int:
        """Merge many remote records in one transaction. Returns how many changed local state."""
        rows = self.conn.execute(f"SELECT * FROM {_TABLES[cls]}").fetchall()
        local = {row["id"]: self._load(cls, row) for row in rows}
        merge = merge_entry if cls is Entry else merge_project
        changed = 0
        for remote in records:
            current = local.get(remote.id)
            if current == remote:
                continue
            merged = remote if current is None else merge(current, remote)
            if merged == current:
                continue
            self._write(merged, commit=False)
            local[remote.id] = merged
            changed += 1
        self.conn.commit()
        return changed

    def get_project(self, project_id: str) -> Project | None:
        return self._get(Project, project_id)

    def get_entry(self, entry_id: str) -> Entry | None:
        return self._get(Entry, entry_id)

    def projects(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects" + ("" if include_archived else ' WHERE "archived" = 0')
        rows = self.conn.execute(sql).fetchall()
        return sorted((self._load(Project, r) for r in rows), key=lambda p: p.name.lower())

    def entries(self, start: float | None = None, end: float | None = None,
                user: str | None = None, include_deleted: bool = False) -> list[Entry]:
        """Entries overlapping the interval [start, end)."""
        where, args = [], []
        if start is not None:
            where.append('"end" > ?')
            args.append(start)
        if end is not None:
            where.append('"start" < ?')
            args.append(end)
        if user is not None:
            where.append('"user" = ?')
            args.append(user)
        if not include_deleted:
            where.append('"deleted" = 0')
        sql = "SELECT * FROM entries" + (" WHERE " + " AND ".join(where) if where else "") + ' ORDER BY "start"'
        return [self._load(Entry, r) for r in self.conn.execute(sql, args).fetchall()]

    def running_entries(self, user: str | None = None) -> list[Entry]:
        return [e for e in self.entries(user=user) if e.running]
