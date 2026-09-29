import os
import tempfile

from timetracker.config import Settings
from timetracker.models import Entry, Project, new_id
from timetracker.store import Store


class Device:
    """A simulated computer: its own data folder, settings and store."""

    def __init__(self, root: str, name: str, user: str = "alice"):
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir)
        self.settings = Settings(user=user, device_name=name)
        self.store = Store(os.path.join(self.dir, "db.sqlite"), self.settings.device_id)

    def close(self):
        self.store.close()


def temp_root(testcase) -> str:
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    return tmp.name


def entry(project_id="p", start=0.0, end=3600.0, user="alice", device="d1", **kw) -> Entry:
    return Entry(id=kw.pop("id", new_id()), project_id=project_id, user=user, device=device,
                 start=start, end=end, **kw)


def project(name="Proj", **kw) -> Project:
    return Project(id=kw.pop("id", new_id()), name=name, **kw)


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
