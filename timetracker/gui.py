"""Tkinter user interface."""

from __future__ import annotations

import os
import time
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import reports, sync, timeline
from .models import Entry, Project, new_id
from .timeutil import (day_start, fmt_date, fmt_duration, fmt_hours, fmt_time, next_day, parse_date,
                       parse_datetime, week_start)
from .tracker import Tracker

TICK_MS = 2000
HEARTBEAT_SECONDS = 30


class App:
    def __init__(self, root: tk.Tk, store, settings, settings_path: str, data_dir: str):
        self.root = root
        self.store = store
        self.settings = settings
        self.settings_path = settings_path
        self.data_dir = data_dir
        self.tracker = Tracker(store, settings, notify=self.show_message)
        self._last_heartbeat = time.time()
        self._last_sync = 0.0
        self._project_buttons_key = None

        root.title("Time Tracker")
        root.geometry("820x600")
        root.minsize(640, 480)
        style = ttk.Style(root)
        style.configure("Status.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Current.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Banner.TFrame", background="#fff4ce")
        style.configure("Banner.TLabel", background="#fff4ce")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._build_track_tab()
        self._build_projects_tab()
        self._build_entries_tab()
        self._build_reports_tab()
        self._build_settings_tab()
        self.statusbar = ttk.Label(root, anchor="w", padding=(10, 4))
        self.statusbar.pack(fill="x")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_all())

        self.tracker.recover()
        self.refresh_all()
        self._update_sync_status("Sync not configured" if not settings.sync_dir else "Waiting for first sync")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, self._tick_loop)
        root.after(1000, self._clock_loop)

    # ------------------------------------------------------------------ track tab

    def _build_track_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Track")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        self.stop_button = ttk.Button(top, text="Stop", command=self.on_stop)
        self.stop_button.pack(side="right")

        self.suggest_frame = ttk.Frame(tab, style="Banner.TFrame", padding=6)
        self.suggest_var = tk.StringVar()
        ttk.Label(self.suggest_frame, textvariable=self.suggest_var, style="Banner.TLabel").pack(side="left")
        ttk.Button(self.suggest_frame, text="Dismiss", command=self._dismiss_suggestion).pack(side="right")
        ttk.Button(self.suggest_frame, text="Switch", command=self._accept_suggestion).pack(side="right", padx=4)

        self.idle_frame = ttk.Frame(tab, style="Banner.TFrame", padding=6)
        self.idle_var = tk.StringVar()
        ttk.Label(self.idle_frame, textvariable=self.idle_var, style="Banner.TLabel").pack(side="left")
        ttk.Button(self.idle_frame, text="Discard", command=self._discard_idle).pack(side="right")
        ttk.Button(self.idle_frame, text="Count it", command=self._reclaim_idle).pack(side="right", padx=4)

        self.banner_anchor = ttk.Frame(tab)
        self.banner_anchor.pack(fill="x", pady=(6, 0))
        self.message_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.message_var, foreground="#555").pack(fill="x", pady=(4, 8))

        ttk.Label(tab, text="Click a project to start or switch (click again to stop):").pack(anchor="w")
        self.project_grid = ttk.Frame(tab)
        self.project_grid.pack(fill="x", pady=6)

        ttk.Label(tab, text="Today").pack(anchor="w", pady=(10, 0))
        self.today_tree = ttk.Treeview(tab, columns=("time",), height=6)
        self.today_tree.heading("#0", text="Project")
        self.today_tree.heading("time", text="Time (h:mm)")
        self.today_tree.column("time", width=120, anchor="e", stretch=False)
        self.today_tree.pack(fill="both", expand=True)

    def refresh_track(self):
        current = self.tracker.current
        if current:
            name = self.tracker.project_name(current.project_id)
            self.status_var.set(f"● {name}   {fmt_duration(self.tracker.elapsed(), with_seconds=True)}")
            self.stop_button.state(["!disabled"])
        elif self.tracker.paused_project:
            self.status_var.set(f"Paused (away): {self.tracker.project_name(self.tracker.paused_project)}")
            self.stop_button.state(["!disabled"])
        else:
            self.status_var.set("Not tracking")
            self.stop_button.state(["disabled"])

        if self.tracker.suggestion:
            self.suggest_var.set(f"Looks like you're working on {self.tracker.project_name(self.tracker.suggestion)}.")
            self.suggest_frame.pack(fill="x", after=self.banner_anchor, pady=2)
        else:
            self.suggest_frame.pack_forget()
        if self.tracker.idle_gap:
            start, end, pid = self.tracker.idle_gap
            self.idle_var.set(f"You were away {fmt_time(start)}–{fmt_time(end)} ({fmt_duration(end - start)}). "
                              f"Count it as {self.tracker.project_name(pid)}?")
            self.idle_frame.pack(fill="x", after=self.banner_anchor, pady=2)
        else:
            self.idle_frame.pack_forget()

        projects = self.store.projects()
        key = (tuple((p.id, p.name) for p in projects), current.project_id if current else None)
        if key != self._project_buttons_key:
            self._project_buttons_key = key
            for child in self.project_grid.winfo_children():
                child.destroy()
            if not projects:
                ttk.Label(self.project_grid, text="No projects yet. Add one on the Projects tab.").grid()
            for i, p in enumerate(projects):
                is_current = current is not None and current.project_id == p.id
                label = f"● {p.name}" if is_current else p.name
                ttk.Button(self.project_grid, text=label, width=22,
                           style="Current.TButton" if is_current else "TButton",
                           command=lambda pid=p.id: self.on_toggle(pid)).grid(row=i // 3, column=i % 3, padx=3, pady=3,
                                                                              sticky="ew")
            for c in range(3):
                self.project_grid.columnconfigure(c, weight=1)

    def refresh_today(self):
        start = day_start(time.time())
        rows = reports.summarise(self.store.entries(start, next_day(start), user=self.settings.user),
                                 self._project_map(), start, next_day(start))
        self.today_tree.delete(*self.today_tree.get_children())
        for r in rows:
            self.today_tree.insert("", "end", text=r.label, values=(fmt_duration(r.seconds),))
        if rows:
            self.today_tree.insert("", "end", text="Total", values=(fmt_duration(sum(r.seconds for r in rows)),))

    def on_toggle(self, project_id):
        self.tracker.toggle(project_id)
        self.refresh_track()
        self.refresh_today()

    def on_stop(self):
        self.tracker.stop()
        self.refresh_track()
        self.refresh_today()

    def _accept_suggestion(self):
        self.tracker.accept_suggestion()
        self.refresh_track()

    def _dismiss_suggestion(self):
        self.tracker.dismiss_suggestion()
        self.refresh_track()

    def _reclaim_idle(self):
        self.tracker.reclaim_idle()
        self.refresh_track()
        self.refresh_today()

    def _discard_idle(self):
        self.tracker.discard_idle()
        self.refresh_track()

    def show_message(self, text: str):
        self.message_var.set(f"{time.strftime('%H:%M')}  {text}")

    # ------------------------------------------------------------------ projects tab

    def _build_projects_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Projects")

        cols = ("client", "rate", "keywords", "status")
        self.project_tree = ttk.Treeview(tab, columns=cols, selectmode="browse", height=10)
        self.project_tree.heading("#0", text="Project")
        for col, title, width in (("client", "Client", 140), ("rate", "Rate/h", 70),
                                  ("keywords", "Window keywords", 220), ("status", "Status", 70)):
            self.project_tree.heading(col, text=title)
            self.project_tree.column(col, width=width, anchor="e" if col == "rate" else "w")
        self.project_tree.pack(fill="both", expand=True)
        self.project_tree.bind("<<TreeviewSelect>>", self._on_project_select)

        self.show_archived = tk.BooleanVar(value=False)
        ttk.Checkbutton(tab, text="Show archived", variable=self.show_archived,
                        command=self.refresh_projects).pack(anchor="w", pady=4)

        form = ttk.LabelFrame(tab, text="Project details", padding=8)
        form.pack(fill="x")
        self.p_name, self.p_client, self.p_rate, self.p_keywords = (tk.StringVar() for _ in range(4))
        for row, (label, var) in enumerate((("Name", self.p_name), ("Client", self.p_client),
                                            ("Hourly rate", self.p_rate), ("Keywords", self.p_keywords))):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=var, width=50).grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Label(form, text="Keywords: comma-separated words found in window titles of your work on this project "
                             "(e.g. a folder, file or site name).", foreground="#666", wraplength=560
                  ).grid(row=4, column=1, sticky="w")
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(form)
        buttons.grid(row=5, column=1, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="New", command=self._new_project).pack(side="left")
        ttk.Button(buttons, text="Save", command=self._save_project).pack(side="left", padx=4)
        self.archive_button = ttk.Button(buttons, text="Archive", command=self._toggle_archive)
        self.archive_button.pack(side="left")
        self._editing_project: str | None = None

    def refresh_projects(self):
        self.project_tree.delete(*self.project_tree.get_children())
        for p in self.store.projects(include_archived=self.show_archived.get()):
            self.project_tree.insert("", "end", iid=p.id, text=p.name,
                                     values=(p.client, f"{p.rate:.2f}", p.keywords, "archived" if p.archived else ""))
        if self._editing_project and self.project_tree.exists(self._editing_project):
            self.project_tree.selection_set(self._editing_project)

    def _on_project_select(self, _event=None):
        sel = self.project_tree.selection()
        p = self.store.get_project(sel[0]) if sel else None
        if not p:
            return
        self._editing_project = p.id
        self.p_name.set(p.name)
        self.p_client.set(p.client)
        self.p_rate.set(f"{p.rate:g}")
        self.p_keywords.set(p.keywords)
        self.archive_button.configure(text="Unarchive" if p.archived else "Archive")

    def _new_project(self):
        self._editing_project = None
        self.project_tree.selection_remove(self.project_tree.selection())
        for var in (self.p_name, self.p_client, self.p_rate, self.p_keywords):
            var.set("")
        self.archive_button.configure(text="Archive")

    def _save_project(self):
        name = self.p_name.get().strip()
        if not name:
            messagebox.showerror("Project", "Please enter a project name.")
            return
        try:
            rate = float(self.p_rate.get() or 0)
            if rate < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Project", "The hourly rate must be a number of 0 or more.")
            return
        p = self.store.get_project(self._editing_project) if self._editing_project else None
        if p is None:
            p = Project(id=new_id(), name=name)
        p.name, p.client, p.rate, p.keywords = name, self.p_client.get().strip(), rate, self.p_keywords.get().strip()
        self.store.save(p)
        self._editing_project = p.id
        self.refresh_all()

    def _toggle_archive(self):
        p = self.store.get_project(self._editing_project) if self._editing_project else None
        if not p:
            return
        if not p.archived and self.tracker.current and self.tracker.current.project_id == p.id:
            self.tracker.stop()
        p.archived = not p.archived
        self.store.save(p)
        self._on_project_select()
        self.refresh_all()

    # ------------------------------------------------------------------ entries tab

    def _build_entries_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Entries")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.e_from = tk.StringVar(value=fmt_date(week_start(time.time())))
        self.e_to = tk.StringVar(value=fmt_date(time.time()))
        self.e_all_users = tk.BooleanVar(value=False)
        ttk.Label(bar, text="From").pack(side="left")
        ttk.Entry(bar, textvariable=self.e_from, width=11).pack(side="left", padx=4)
        ttk.Label(bar, text="To").pack(side="left")
        ttk.Entry(bar, textvariable=self.e_to, width=11).pack(side="left", padx=4)
        ttk.Checkbutton(bar, text="All users (read only)", variable=self.e_all_users,
                        command=self.refresh_entries).pack(side="left", padx=8)
        ttk.Button(bar, text="Show", command=self.refresh_entries).pack(side="left")

        cols = ("date", "start", "end", "duration", "project", "user", "device", "source", "note")
        self.entry_tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="extended")
        for col, width in zip(cols, (85, 50, 50, 65, 140, 80, 110, 80, 160)):
            self.entry_tree.heading(col, text=col.capitalize())
            self.entry_tree.column(col, width=width, anchor="w")
        self.entry_tree.tag_configure("overlap", background="#fde2e1")
        self.entry_tree.tag_configure("running", foreground="#0a7a2f")
        self.entry_tree.pack(fill="both", expand=True, pady=6)
        self.entry_tree.bind("<Double-1>", lambda _e: self._edit_entry())

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Add…", command=lambda: self._edit_entry(new=True)).pack(side="left")
        ttk.Button(buttons, text="Edit…", command=self._edit_entry).pack(side="left", padx=4)
        ttk.Button(buttons, text="Delete", command=self._delete_entries).pack(side="left")
        ttk.Button(buttons, text="Resolve overlaps", command=self._resolve_overlaps).pack(side="left", padx=4)
        self.overlap_var = tk.StringVar()
        ttk.Label(buttons, textvariable=self.overlap_var, foreground="#b3261e").pack(side="left", padx=8)

    def _entry_range(self, from_var, to_var):
        try:
            start = parse_date(from_var.get())
            end = next_day(parse_date(to_var.get()))
        except ValueError:
            messagebox.showerror("Dates", "Dates must be in the form YYYY-MM-DD.")
            return None
        return start, end

    def refresh_entries(self):
        rng = self._entry_range(self.e_from, self.e_to)
        if not rng:
            return
        user = None if self.e_all_users.get() else self.settings.user
        entries = self.store.entries(*rng, user=user)
        overlaps = timeline.overlapping_ids(entries)
        projects = self._project_map()
        self.entry_tree.delete(*self.entry_tree.get_children())
        for e in entries:
            p = projects.get(e.project_id)
            tags = (["overlap"] if e.id in overlaps else []) + (["running"] if e.running else [])
            self.entry_tree.insert("", "end", iid=e.id, tags=tags, values=(
                fmt_date(e.start), fmt_time(e.start), "running" if e.running else fmt_time(e.end),
                fmt_duration(e.duration), p.name if p else "(deleted)", e.user,
                self.settings.device_label(e.device), e.source, e.note))
        count = len(timeline.find_overlaps(entries))
        self.overlap_var.set(f"{count} overlapping pair(s) highlighted" if count else "")

    def _own_entry(self, entry: Entry) -> bool:
        if entry.user != self.settings.user:
            messagebox.showerror("Entries", f"This entry belongs to {entry.user}; you can only change your own.")
            return False
        return True

    def _edit_entry(self, new: bool = False):
        entry = None
        if not new:
            sel = self.entry_tree.selection()
            if not sel:
                return
            entry = self.store.get_entry(sel[0])
            if not entry or not self._own_entry(entry):
                return
            if entry.running:
                messagebox.showinfo("Entries", "Stop tracking before editing the running entry.")
                return
        projects = self.store.projects()
        if not projects:
            messagebox.showinfo("Entries", "Add a project first.")
            return
        EntryDialog(self.root, self, projects, entry)

    def _delete_entries(self):
        entries = [e for e in (self.store.get_entry(i) for i in self.entry_tree.selection()) if e]
        if not entries or not all(self._own_entry(e) for e in entries):
            return
        if not messagebox.askyesno("Delete", f"Delete {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}?"):
            return
        for e in entries:
            if self.tracker.current and self.tracker.current.id == e.id:
                self.tracker.stop()
                e = self.store.get_entry(e.id)
            e.deleted, e.running = True, False
            self.store.save(e)
        self.refresh_all()

    def _resolve_overlaps(self):
        rng = self._entry_range(self.e_from, self.e_to)
        if not rng:
            return
        fixed = 0
        # Resolve one pair at a time, re-reading, because each fix can change other pairs.
        for _ in range(500):
            pairs = timeline.find_overlaps(self.store.entries(*rng, user=self.settings.user))
            if not pairs:
                break
            for record in timeline.resolve(*pairs[0]):
                self.store.save(record)
            fixed += 1
        self.tracker.after_sync()
        self.show_message(f"Resolved {fixed} overlap(s): the later-started entry kept its time.")
        self.refresh_all()

    # ------------------------------------------------------------------ reports tab

    def _build_reports_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Reports")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.r_from = tk.StringVar(value=fmt_date(week_start(time.time())))
        self.r_to = tk.StringVar(value=fmt_date(time.time()))
        self.r_group = tk.StringVar(value="project")
        self.r_all_users = tk.BooleanVar(value=True)
        ttk.Label(bar, text="From").pack(side="left")
        ttk.Entry(bar, textvariable=self.r_from, width=11).pack(side="left", padx=4)
        ttk.Label(bar, text="To").pack(side="left")
        ttk.Entry(bar, textvariable=self.r_to, width=11).pack(side="left", padx=4)
        ttk.Label(bar, text="Group by").pack(side="left", padx=(8, 0))
        ttk.Combobox(bar, textvariable=self.r_group, values=reports.GROUPINGS, width=9,
                     state="readonly").pack(side="left", padx=4)
        ttk.Checkbutton(bar, text="All users", variable=self.r_all_users).pack(side="left", padx=4)
        ttk.Button(bar, text="Run", command=self.refresh_reports).pack(side="left", padx=4)
        ttk.Button(bar, text="Export CSV…", command=self._export_csv).pack(side="left")

        self.report_tree = ttk.Treeview(tab, columns=("time", "hours", "amount"), height=10)
        self.report_tree.heading("#0", text="Group")
        for col, title in (("time", "Time (h:mm)"), ("hours", "Hours"), ("amount", "Value")):
            self.report_tree.heading(col, text=title)
            self.report_tree.column(col, width=110, anchor="e", stretch=False)
        self.report_tree.pack(fill="both", expand=True, pady=6)

        inv = ttk.LabelFrame(tab, text="Invoice", padding=8)
        inv.pack(fill="x")
        self.inv_client = tk.StringVar()
        self.inv_number = tk.StringVar(value=f"INV-{time.strftime('%Y%m%d')}")
        ttk.Label(inv, text="Client").pack(side="left")
        self.inv_client_box = ttk.Combobox(inv, textvariable=self.inv_client, width=24, state="readonly")
        self.inv_client_box.pack(side="left", padx=4)
        ttk.Label(inv, text="Number").pack(side="left")
        ttk.Entry(inv, textvariable=self.inv_number, width=16).pack(side="left", padx=4)
        ttk.Button(inv, text="Create invoice", command=self._create_invoice).pack(side="left", padx=4)

    def _report_entries(self, rng):
        user = None if self.r_all_users.get() else self.settings.user
        return self.store.entries(*rng, user=user)

    def refresh_reports(self):
        clients = sorted({p.client for p in self.store.projects(include_archived=True) if p.client})
        self.inv_client_box.configure(values=clients)
        if not self.inv_client.get() and clients:
            self.inv_client.set(clients[0])
        rng = self._entry_range(self.r_from, self.r_to)
        if not rng:
            return
        rows = reports.summarise(self._report_entries(rng), self._project_map(True), *rng, group_by=self.r_group.get())
        cur = self.settings.currency
        self.report_tree.delete(*self.report_tree.get_children())
        for r in rows:
            self.report_tree.insert("", "end", text=r.label,
                                    values=(fmt_duration(r.seconds), fmt_hours(r.seconds), f"{cur}{r.amount:,.2f}"))
        if rows:
            total_s, total_a = sum(r.seconds for r in rows), sum(r.amount for r in rows)
            self.report_tree.insert("", "end", text="Total",
                                    values=(fmt_duration(total_s), fmt_hours(total_s), f"{cur}{total_a:,.2f}"))

    def _export_csv(self):
        rng = self._entry_range(self.r_from, self.r_to)
        if not rng:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"timesheet-{self.r_from.get()}-to-{self.r_to.get()}.csv")
        if path:
            reports.write_entries_csv(path, self._report_entries(rng), self._project_map(True),
                                      self.settings.device_label)
            self.show_message(f"Exported {os.path.basename(path)}")

    def _create_invoice(self):
        rng = self._entry_range(self.r_from, self.r_to)
        client = self.inv_client.get()
        if not rng or not client:
            if not client:
                messagebox.showinfo("Invoice", "Choose a client (set clients on the Projects tab).")
            return
        number = self.inv_number.get().strip() or f"INV-{time.strftime('%Y%m%d')}"
        invoice = reports.build_invoice(self._report_entries(rng), self._project_map(True), client, *rng, number,
                                        self.settings.rounding_minutes, self.settings.currency,
                                        self.settings.business_name)
        folder = os.path.join(self.data_dir, "invoices")
        os.makedirs(folder, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in number)
        path = os.path.join(folder, f"{safe}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(reports.invoice_html(invoice))
        webbrowser.open(f"file:///{os.path.abspath(path)}")
        self.show_message(f"Invoice saved to {path} (print it from the browser to make a PDF).")

    # ------------------------------------------------------------------ settings tab

    def _build_settings_tab(self):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Settings")
        s = self.settings
        self.s_vars = {
            "user": tk.StringVar(value=s.user),
            "device_name": tk.StringVar(value=s.device_name),
            "sync_dir": tk.StringVar(value=s.sync_dir),
            "idle_minutes": tk.StringVar(value=f"{s.idle_minutes:g}"),
            "dwell_seconds": tk.StringVar(value=str(s.dwell_seconds)),
            "rounding_minutes": tk.StringVar(value=str(s.rounding_minutes)),
            "currency": tk.StringVar(value=s.currency),
            "business_name": tk.StringVar(value=s.business_name),
        }
        self.s_auto = tk.BooleanVar(value=s.auto_switch)
        self.s_resume = tk.BooleanVar(value=s.resume_on_start)
        labels = [("user", "Your name"), ("device_name", "This computer's name"), ("sync_dir", "Shared sync folder"),
                  ("idle_minutes", "Pause after idle (minutes, 0 = never)"),
                  ("dwell_seconds", "Window match delay (seconds)"),
                  ("rounding_minutes", "Billing increment (minutes, 0 = exact)"),
                  ("currency", "Currency symbol"), ("business_name", "Your business name (on invoices)")]
        for row, (key, label) in enumerate(labels):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(tab, textvariable=self.s_vars[key], width=50).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(tab, text="Browse…", command=self._browse_sync).grid(row=2, column=2, padx=4)
        ttk.Checkbutton(tab, text="Start/switch project automatically when the active window matches its keywords "
                                  "(otherwise just suggest)", variable=self.s_auto).grid(row=8, column=1, sticky="w")
        ttk.Checkbutton(tab, text="Resume the last project when the app starts",
                        variable=self.s_resume).grid(row=9, column=1, sticky="w")
        buttons = ttk.Frame(tab)
        buttons.grid(row=10, column=1, sticky="w", pady=10)
        ttk.Button(buttons, text="Save settings", command=self._save_settings).pack(side="left")
        ttk.Button(buttons, text="Sync now", command=self.do_sync).pack(side="left", padx=6)
        ttk.Label(tab, text="To track across computers, point every computer at the same folder in OneDrive, "
                            "Dropbox, Google Drive or a network share. Each computer writes only its own file there.",
                  foreground="#666", wraplength=560).grid(row=11, column=1, sticky="w")
        ttk.Label(tab, text=f"Data folder: {self.data_dir}", foreground="#666").grid(row=12, column=1, sticky="w",
                                                                                     pady=(10, 0))
        tab.columnconfigure(1, weight=1)

    def _browse_sync(self):
        path = filedialog.askdirectory(title="Choose the shared sync folder")
        if path:
            self.s_vars["sync_dir"].set(path)

    def _save_settings(self):
        v = self.s_vars
        try:
            idle = float(v["idle_minutes"].get())
            dwell = int(v["dwell_seconds"].get())
            rounding = int(v["rounding_minutes"].get())
            if idle < 0 or dwell < 0 or rounding < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Settings", "Idle minutes, match delay and billing increment must be numbers ≥ 0.")
            return
        user = v["user"].get().strip()
        if not user:
            messagebox.showerror("Settings", "Please enter your name.")
            return
        if user != self.settings.user and self.tracker.current:
            self.tracker.stop()
        s = self.settings
        s.user, s.device_name, s.sync_dir = user, v["device_name"].get().strip() or s.device_name, v["sync_dir"].get().strip()
        s.idle_minutes, s.dwell_seconds, s.rounding_minutes = idle, dwell, rounding
        s.currency, s.business_name = v["currency"].get(), v["business_name"].get().strip()
        s.auto_switch, s.resume_on_start = self.s_auto.get(), self.s_resume.get()
        s.save(self.settings_path)
        self.show_message("Settings saved.")
        self._update_sync_status("Sync not configured" if not s.sync_dir else "Sync folder set")
        self.refresh_all()

    # ------------------------------------------------------------------ sync and timers

    def do_sync(self):
        self._last_sync = time.time()
        if not self.settings.sync_dir:
            self._update_sync_status("Sync not configured")
            return
        self.tracker.heartbeat()
        try:
            result = sync.sync(self.store, self.settings.sync_dir, self.settings.device_id, self.settings.device_name)
        except OSError as exc:
            self._update_sync_status(f"Sync failed: {exc}")
            return
        result.devices.pop(self.settings.device_id, None)
        if any(self.settings.known_devices.get(k) != v for k, v in result.devices.items()):
            self.settings.known_devices.update(result.devices)
            self.settings.save(self.settings_path)
        self.tracker.after_sync()
        if self.tracker.current or result.changed:
            sync.export_snapshot(self.store, self.settings.sync_dir, self.settings.device_id,
                                 self.settings.device_name)
        status = f"Synced {time.strftime('%H:%M:%S')} with {len(result.devices)} other device(s)"
        if result.changed:
            status += f", {result.entries_changed} entr(ies) and {result.projects_changed} project(s) updated"
        if result.errors:
            status += f" — {len(result.errors)} file(s) skipped"
        self._update_sync_status(status)
        if result.changed:
            self.refresh_all()

    def _update_sync_status(self, text):
        self.statusbar.configure(text=f"{self.settings.user} on {self.settings.device_name}  ·  {text}")

    def _tick_loop(self):
        try:
            self.tracker.tick()
            now = time.time()
            if now - self._last_heartbeat >= HEARTBEAT_SECONDS:
                self._last_heartbeat = now
                self.tracker.heartbeat()
            if self.settings.sync_dir and now - self._last_sync >= self.settings.sync_interval:
                self.do_sync()
        except Exception:     # keep the timer alive whatever happens
            traceback.print_exc()
        self.refresh_track()
        self.root.after(TICK_MS, self._tick_loop)

    def _clock_loop(self):
        self.refresh_track()
        if int(time.time()) % 10 == 0 and self._current_tab() == "Track":
            self.refresh_today()
        self.root.after(1000, self._clock_loop)

    def _current_tab(self) -> str:
        return self.notebook.tab(self.notebook.select(), "text")

    def refresh_all(self):
        self.refresh_track()
        self.refresh_today()
        tab = self._current_tab()
        if tab == "Projects":
            self.refresh_projects()
        elif tab == "Entries":
            self.refresh_entries()
        elif tab == "Reports":
            self.refresh_reports()

    def _project_map(self, include_archived: bool = True) -> dict:
        return {p.id: p for p in self.store.projects(include_archived=include_archived)}

    def on_close(self):
        try:
            self.tracker.shutdown()
            self.settings.save(self.settings_path)
            if self.settings.sync_dir:
                self.do_sync()
        finally:
            self.root.destroy()


class EntryDialog(tk.Toplevel):
    """Add or edit a completed time entry."""

    def __init__(self, parent, app: App, projects: list[Project], entry: Entry | None):
        super().__init__(parent)
        self.app, self.entry, self.projects = app, entry, projects
        self.title("Edit entry" if entry else "Add entry")
        self.transient(parent)
        self.resizable(False, False)

        names = [p.name for p in projects]
        current = next((p.name for p in projects if entry and p.id == entry.project_id), names[0])
        ref = entry.start if entry else time.time()
        self.v_project = tk.StringVar(value=current)
        self.v_date = tk.StringVar(value=fmt_date(ref))
        self.v_start = tk.StringVar(value=fmt_time(ref) if entry else "09:00")
        self.v_end = tk.StringVar(value=fmt_time(entry.end) if entry else "10:00")
        self.v_note = tk.StringVar(value=entry.note if entry else "")

        frame = ttk.Frame(self, padding=12)
        frame.pack()
        ttk.Label(frame, text="Project").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.v_project, values=names, state="readonly", width=28).grid(row=0, column=1)
        for row, (label, var) in enumerate((("Date (YYYY-MM-DD)", self.v_date), ("Start (HH:MM)", self.v_start),
                                            ("End (HH:MM)", self.v_end), ("Note", self.v_note)), start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=var, width=30).grid(row=row, column=1, pady=2)
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=1, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right", padx=4)
        self.grab_set()

    def _ok(self):
        try:
            start = parse_datetime(self.v_date.get(), self.v_start.get())
            end = parse_datetime(self.v_date.get(), self.v_end.get())
        except ValueError:
            messagebox.showerror("Entry", "Use YYYY-MM-DD for the date and HH:MM for times.", parent=self)
            return
        if end <= start:
            end += 24 * 3600      # an end time before the start means the entry ran past midnight
        project = next(p for p in self.projects if p.name == self.v_project.get())
        s = self.app.settings
        entry = self.entry or Entry(id=new_id(), project_id=project.id, user=s.user, device=s.device_id,
                                    start=start, end=end)
        entry.project_id, entry.start, entry.end, entry.note = project.id, start, end, self.v_note.get().strip()
        entry.source = "edited" if self.entry else "manual"
        self.app.store.save(entry)
        self.destroy()
        self.app.refresh_all()


def run(store, settings, settings_path: str, data_dir: str) -> None:
    root = tk.Tk()
    App(root, store, settings, settings_path, data_dir)
    root.mainloop()
