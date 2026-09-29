# Networked Work Time Tracker

A desktop time tracker that needs as little input as possible, tracks time against multiple projects on
multiple computers, and turns the records into reports and invoices. It runs entirely on the local
machine: there is **no server to install or host**.

See [docs/project_brief.md](docs/project_brief.md) for the brief and
[docs/project_definition_document.md](docs/project_definition_document.md) for the project plan.

## Running it

Requires Python 3.10 or later. Only the standard library is used (tkinter, sqlite3), so there is
nothing to install.

```
python -m timetracker            # start the app
python -m timetracker --demo     # start with sample projects and a week of sample data
```

Data is kept in `%APPDATA%\TimeTracker` on Windows (`~/.timetracker` elsewhere). Use
`--data-dir <folder>` to choose another location.

## Using it

1. **Projects tab**: add projects with a client, hourly rate and optional *keywords*.
2. **Track tab**: click a project to start. Click another to switch, or click the same one again to stop.
3. Normally you don't need to do anything else:
   - **Idle detection**: after 5 idle minutes (configurable), tracking pauses at the moment you left
     and resumes when you come back. A banner offers **Count it** if you were working away from the
     keyboard (a meeting, a phone call).
   - **Context matching**: if the active window title contains a project's keywords (such as a
     folder, file or website name) for 30 seconds, the tracker starts or switches to that project.
     It can also just suggest the switch, with a one-click **Switch** button, if you turn off
     automatic switching in Settings.
   - **Crash safety**: the running entry is saved every 30 seconds, so a crash or power cut loses at
     most that much.
4. **Entries tab**: view, add, edit or delete entries. Overlapping entries (for example, from two
   computers) are highlighted; **Resolve overlaps** fixes them, and the entry that started later
   keeps its time.
5. **Reports tab**: totals by project, client, user or day, with values from the hourly rates. You
   can export to CSV and create an HTML invoice for a client, then print it to PDF from the browser.
   Billed hours are rounded up to the billing increment (6 minutes by default).

### Working on several computers

In **Settings**, set **Shared sync folder** on each computer to the same folder in OneDrive, Dropbox,
Google Drive or a network share. The computers sync every minute and when the app closes.

- Each computer writes only its own file (`tt-<device-id>.json`) and reads everyone else's, so
  computers never overwrite each other and work done offline merges in when they reconnect.
- If you start tracking on your laptop while the office desktop is still tracking, the desktop's
  entry is closed at the moment you started on the laptop, and the desktop stops tracking.

### Several users

Each person sets **Your name** in Settings. To share projects, share the sync folder. Project
reports and invoices can include all users' time ("All users" on the Reports tab). You can see
other users' entries but can only edit or delete your own.

## Trying the sync on one machine

Run two copies with different data folders, and set the same sync folder in both:

```
python -m timetracker --data-dir C:\tt\desktop --demo
python -m timetracker --data-dir C:\tt\laptop
```

## Tests

```
python -m unittest discover -s tests
```

The tests cover the merge rules, sync between simulated devices (offline work, concurrent edits,
corrupt files), overlap detection and resolution, reports and invoices, and the tracker's idle,
context-matching, crash-recovery and hand-over behaviour. They use a fake clock.

## Experiments

These scripts reproduce the evaluation figures in [docs/final_report.md](docs/final_report.md):

```
python experiments/scenario.py [--suggest]   # simulated working day: accuracy and interactions
python experiments/convergence.py 200 150    # randomised multi-device convergence (~8 min)
python experiments/performance.py            # sync cost at 1k / 10k / 50k entries
```

## Design overview

| Module | Responsibility |
| --- | --- |
| [models.py](timetracker/models.py) | `Project` and `Entry` records, and the merge rules |
| [store.py](timetracker/store.py) | Local SQLite storage; stamps each local change with a version |
| [sync.py](timetracker/sync.py) | Exchanges snapshot files through the shared folder |
| [timeline.py](timetracker/timeline.py) | Finds and resolves overlapping entries |
| [tracker.py](timetracker/tracker.py) | Tracking engine: start/stop, idle, context matching, hand-over |
| [reports.py](timetracker/reports.py) | Summaries, CSV export, invoices |
| [osinfo.py](timetracker/osinfo.py) | Idle time and active window title (Windows) |
| [gui.py](timetracker/gui.py) | Tkinter interface |

**Why syncing is safe:** every record has a version (a timestamp plus the ID of the device that
changed it). Merging is last-writer-wins, except for two changes that are permanent once made:
deleting an entry, and closing a running entry. The merge gives the same result whatever order
records arrive in and however many times they arrive, so every device ends up with the same data
without a central server. Resolving an overlap always gives the same result, and any new entry it
creates gets an ID derived from the two entries involved. So if two devices resolve the same
conflict, they produce the same records.

## Known limitations

- Idle detection and window matching use the Windows API. On macOS or Linux the app runs without
  them, and you switch projects by clicking.
- Last-writer-wins relies on the computers' clocks being roughly right, which is normal with network
  time.
- Separating users' data is enforced by the app, not by authentication. Anyone with access to the
  shared folder can read its files.
