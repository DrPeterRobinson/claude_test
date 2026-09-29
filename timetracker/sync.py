"""Synchronisation between computers through a shared folder.

There is no server. Each device writes a snapshot of every record it knows
to ``<sync_dir>/tt-<device_id>.json``, and reads and merges the snapshots of
all other devices. Any file-sync service (OneDrive, Dropbox, Google Drive, a
network share, a USB stick) can carry the folder between machines.

Because each device only ever writes its own file, two devices can never
overwrite each other's data, and because snapshots contain everything a
device knows, records keep spreading even when their original device is
offline. The merge rules in :mod:`timetracker.models` make repeated or
out-of-order syncs safe.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field

from .models import Entry, Project, from_dict, to_dict

FORMAT = 1
PREFIX = "tt-"
REPLACE_ATTEMPTS = 10


@dataclass
class SyncResult:
    files_read: int = 0
    projects_changed: int = 0
    entries_changed: int = 0
    devices: dict = field(default_factory=dict)    # device id -> device name
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.projects_changed or self.entries_changed)


def snapshot_path(sync_dir: str, device_id: str) -> str:
    return os.path.join(sync_dir, f"{PREFIX}{device_id}.json")


def import_snapshots(store, sync_dir: str) -> SyncResult:
    result = SyncResult()
    seen = store.seen_snapshots
    # The glob also picks up "conflicted copy" files some sync services create.
    for path in sorted(glob.glob(os.path.join(sync_dir, PREFIX + "*.json"))):
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            result.errors.append(f"{name}: {exc}")
            continue
        # Keyed on content, not modification time: file times are coarse on some
        # systems and cloud clients may restore an older time on a newer file.
        key = hashlib.blake2b(raw, digest_size=16).digest()
        cached = seen.get(path)
        if cached and cached[0] == key:
            # Merging is idempotent, so an unchanged file cannot change anything.
            result.devices[cached[1]] = cached[2]
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            result.errors.append(f"{name}: {exc}")    # e.g. part-written by the sync client
            continue
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            result.errors.append(f"{name}: unsupported format")
            continue
        result.files_read += 1
        device = data.get("device") or name
        result.devices[device] = data.get("device_name") or device[:8]
        for cls, list_key in ((Project, "projects"), (Entry, "entries")):
            records = []
            for item in data.get(list_key, []):
                try:
                    records.append(from_dict(cls, item))
                except TypeError as exc:
                    result.errors.append(f"{name}: bad record ({exc})")
            changed = store.merge_many(cls, records)
            if cls is Project:
                result.projects_changed += changed
            else:
                result.entries_changed += changed
        seen[path] = (key, device, result.devices[device])
    return result


def export_snapshot(store, sync_dir: str, device_id: str, device_name: str = "") -> str:
    os.makedirs(sync_dir, exist_ok=True)
    data = {
        "format": FORMAT,
        "device": device_id,
        "device_name": device_name,
        "written_at": time.time(),
        "projects": [to_dict(p) for p in store.projects(include_archived=True)],
        "entries": [to_dict(e) for e in store.entries(include_deleted=True)],
    }
    # Write to a temporary file and rename, so readers never see a half-written snapshot.
    raw = json.dumps(data).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=sync_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    target = snapshot_path(sync_dir, device_id)
    # On Windows the rename fails while another process (antivirus, indexer, the
    # cloud sync client) briefly has the target open, so retry for a short while.
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            break
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                os.remove(tmp)
                raise
            time.sleep(0.05 * (attempt + 1))
    key = hashlib.blake2b(raw, digest_size=16).digest()
    store.seen_snapshots[target] = (key, device_id, device_name or device_id[:8])
    return target


def sync(store, sync_dir: str, device_id: str, device_name: str = "") -> SyncResult:
    result = import_snapshots(store, sync_dir)
    export_snapshot(store, sync_dir, device_id, device_name)
    return result
