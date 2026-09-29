"""Per-device settings, stored as JSON in the data directory."""

from __future__ import annotations

import getpass
import json
import os
import socket
from dataclasses import asdict, dataclass, field, fields

from .models import new_id


def default_data_dir() -> str:
    base = os.environ.get("APPDATA")
    return os.path.join(base, "TimeTracker") if base else os.path.join(os.path.expanduser("~"), ".timetracker")


@dataclass
class Settings:
    user: str = field(default_factory=getpass.getuser)
    device_id: str = field(default_factory=new_id)
    device_name: str = field(default_factory=socket.gethostname)
    sync_dir: str = ""
    sync_interval: int = 60          # seconds
    idle_minutes: float = 5.0        # 0 disables idle detection
    auto_switch: bool = True         # start/switch automatically when the active window matches a project
    dwell_seconds: int = 30          # how long a match must persist before acting on it
    rounding_minutes: int = 6        # billing increment; 0 = bill exact time
    currency: str = "£"
    business_name: str = ""
    resume_on_start: bool = False
    last_project: str = ""
    known_devices: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Settings":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls()
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)

    def device_label(self, device_id: str) -> str:
        if device_id == self.device_id:
            return f"{self.device_name} (this)"
        return self.known_devices.get(device_id, device_id[:8])
