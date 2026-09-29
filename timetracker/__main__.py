"""Entry point: ``python -m timetracker``."""

from __future__ import annotations

import argparse
import os

from .config import Settings, default_data_dir
from .store import Store


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="timetracker", description="Networked work time tracker")
    parser.add_argument("--data-dir", default=default_data_dir(),
                        help="where this computer keeps its database and settings (default: %(default)s). "
                             "Use two different folders to simulate two computers on one machine.")
    parser.add_argument("--demo", action="store_true",
                        help="add sample projects and a week of sample entries if there are no projects yet")
    args = parser.parse_args(argv)

    os.makedirs(args.data_dir, exist_ok=True)
    settings_path = os.path.join(args.data_dir, "settings.json")
    settings = Settings.load(settings_path)
    settings.save(settings_path)             # persist the generated device id
    store = Store(os.path.join(args.data_dir, "timetracker.db"), settings.device_id)

    if args.demo:
        from .demo import seed
        seed(store, settings)

    from .gui import run
    try:
        run(store, settings, settings_path, args.data_dir)
    finally:
        store.close()


if __name__ == "__main__":
    main()
