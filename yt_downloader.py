"""
YT Downloader - a simple Windows front-end for yt-dlp.

Needs only Python (with tkinter, which is included in the normal Windows
installer from python.org) plus yt-dlp.exe and ffmpeg.exe / ffprobe.exe.
Settings are saved in "yt_downloader_config.json" next to this file (or next
to the .exe once you build one with PyInstaller).
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "YT Downloader"
CONFIG_NAME = "yt_downloader_config.json"
HOME = Path.home()
IS_WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hide black console windows

VIDEO_QUALITIES = ["Best", "1080p", "720p", "480p"]
VIDEO_FORMATS = ["MP4", "MKV"]
AUDIO_FORMATS = ["MP3", "M4A", "OPUS", "WAV"]
AUDIO_QUALITIES = {"Best": "0", "Medium": "192K", "Small": "128K"}

DEFAULTS = {
    "ytdlp_path": str(HOME / "yt-dlp.exe"),
    "ffmpeg_dir": str(HOME),
    "output_dir": str(HOME / "Downloads"),
    "mode": "Video",
    "video_quality": "Best",
    "video_format": "MP4",
    "audio_format": "MP3",
    "audio_quality": "Best",
    "playlist": False,
}

# Plain-language explanations for common yt-dlp errors (first match wins).
FRIENDLY_ERRORS = [
    (r"is not a valid URL|Unsupported URL",
     "That link doesn't look right, or this website isn't supported."),
    (r"ffmpeg|ffprobe",
     "ffmpeg is missing - it's needed to convert audio (e.g. MP3) and to "
     "combine video + sound. Check the ffmpeg folder in Settings."),
    (r"Private video", "This video is private."),
    (r"Sign in to confirm your age|age.restricted",
     "This video is age-restricted and needs a signed-in account."),
    (r"not a bot",
     "YouTube is asking for a bot check. Click 'Update yt-dlp', wait a while, then try again."),
    (r"Video unavailable|is not available|has been removed",
     "This video is unavailable (removed, private, or blocked in your country)."),
    (r"Requested format is not available",
     "The chosen quality isn't available for this video. Try 'Best'."),
    (r"HTTP Error 403",
     "The website refused the download (error 403). Click 'Update yt-dlp' and try again."),
    (r"HTTP Error 429",
     "Too many requests - the website is slowing you down. Wait a while and try again."),
    (r"HTTP Error 404", "The video was not found (error 404). Check the link."),
    (r"No space left|Errno 28", "Your disk is full."),
    (r"Permission denied|Errno 13",
     "Can't save to the output folder (permission denied). Choose another folder."),
    (r"getaddrinfo|Failed to resolve|timed out|Connection (refused|reset|aborted)|Unable to download webpage",
     "Couldn't connect. Check your internet connection and the link."),
]

PROGRESS_RE = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%(.*)$")
ITEM_RE = re.compile(r"Downloading (?:item|video) (\d+) of (\d+)")
URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


# ---------------------------------------------------------------- helpers --

def app_dir() -> Path:
    """Folder where the app lives (works both as .py and as PyInstaller .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass  # e.g. app placed in a read-only folder - just don't remember


def resolve_ytdlp(setting: str):
    """Return the full path to yt-dlp(.exe), or None if it can't be found."""
    p = Path(setting.strip().strip('"')) if setting.strip() else None
    if p:
        if p.is_dir():
            p = p / ("yt-dlp.exe" if IS_WINDOWS else "yt-dlp")
        if p.is_file():
            return str(p)
    return shutil.which("yt-dlp")


def resolve_ffmpeg_dir(setting: str):
    """Return the folder containing ffmpeg(.exe), or None if not found."""
    exe = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    p = Path(setting.strip().strip('"')) if setting.strip() else None
    if p:
        if p.is_file():
            p = p.parent
        if (p / exe).is_file():
            return str(p)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


def friendly_error(text: str):
    for pattern, message in FRIENDLY_ERRORS:
        if re.search(pattern, text, re.IGNORECASE):
            return message
    return None


def build_command(ytdlp: str, ffmpeg_dir, job: dict) -> list:
    """Translate the choices in `job` into a yt-dlp command line."""
    cmd = [ytdlp, "--newline", "--encoding", "utf-8", "--no-mtime"]
    if ffmpeg_dir:
        cmd += ["--ffmpeg-location", ffmpeg_dir]
    cmd.append("--yes-playlist" if job["playlist"] else "--no-playlist")

    if job["mode"] == "Audio":
        cmd += ["-x",
                "--audio-format", job["audio_format"].lower(),
                "--audio-quality", AUDIO_QUALITIES[job["audio_quality"]]]
    else:
        q = job["video_quality"]
        if q == "Best":
            fmt = "bv*+ba/b"
        else:
            h = q.rstrip("p")
            fmt = f"bv*[height<={h}]+ba/b[height<={h}]"
        cmd += ["-f", fmt, "--merge-output-format", job["video_format"].lower()]
        if job["video_format"] == "MP4":
            # Prefer MP4/M4A streams so the file plays everywhere on Windows.
            cmd += ["-S", "res,ext:mp4:m4a"]

    cmd += ["-P", job["output_dir"], "-o", "%(title)s.%(ext)s", "--", job["url"]]
    return cmd


def describe(job: dict) -> str:
    if job["mode"] == "Audio":
        text = f"Audio {job['audio_format']} ({job['audio_quality']})"
    else:
        text = f"Video {job['video_quality']} {job['video_format']}"
    return text + (" + playlist" if job["playlist"] else "")


def kill_process(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    if IS_WINDOWS:  # /T also stops ffmpeg started by yt-dlp
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.terminate()


# -------------------------------------------------------------------- app --

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.msgs = queue.Queue()      # messages from worker thread -> GUI
        self.proc = None               # running yt-dlp process
        self.task = None               # "download" or "update" while busy
        self.current_job = None        # iid of job being downloaded
        self.stop_requested = False
        self.jobs = {}                 # iid -> job dict
        self.last_error = None
        self.failed_in_run = []
        self.last_folder = None
        self.item_text = ""

        root.title(APP_TITLE)
        root.minsize(640, 640)
        self._build_ui()
        self._on_mode_change()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

        if not resolve_ytdlp(self.cfg["ytdlp_path"]):
            root.after(300, lambda: (messagebox.showwarning(
                APP_TITLE, "yt-dlp.exe was not found.\n\nPlease set its location in Settings."),
                self.open_settings()))

    # ----------------------------------------------------------- UI layout --

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=8)

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        row = 0

        # 1. Link
        ttk.Label(main, text="Link:").grid(row=row, column=0, sticky="w", pady=4)
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(main, textvariable=self.url_var)
        url_entry.grid(row=row, column=1, sticky="ew", padx=6)
        url_entry.bind("<Return>", lambda e: self.add_to_queue())
        url_entry.focus_set()
        btns = ttk.Frame(main)
        btns.grid(row=row, column=2, sticky="e")
        ttk.Button(btns, text="Paste", command=self.paste).pack(side="left")
        ttk.Button(btns, text="Add to queue", command=self.add_to_queue).pack(side="left", padx=(4, 0))
        row += 1

        # 2. Mode
        ttk.Label(main, text="Mode:").grid(row=row, column=0, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value=self.cfg["mode"])
        mode_frame = ttk.Frame(main)
        mode_frame.grid(row=row, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Radiobutton(mode_frame, text="Video", value="Video", variable=self.mode_var,
                        command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(mode_frame, text="Audio only", value="Audio", variable=self.mode_var,
                        command=self._on_mode_change).pack(side="left", padx=12)
        row += 1

        # 3./4. Options (video or audio)
        self.video_frame = ttk.Frame(main)
        self.vq_var = tk.StringVar(value=self.cfg["video_quality"])
        self.vf_var = tk.StringVar(value=self.cfg["video_format"])
        ttk.Label(self.video_frame, text="Quality:").pack(side="left")
        ttk.Combobox(self.video_frame, textvariable=self.vq_var, values=VIDEO_QUALITIES,
                     state="readonly", width=8).pack(side="left", padx=(4, 16))
        ttk.Label(self.video_frame, text="Format:").pack(side="left")
        ttk.Combobox(self.video_frame, textvariable=self.vf_var, values=VIDEO_FORMATS,
                     state="readonly", width=6).pack(side="left", padx=4)

        self.audio_frame = ttk.Frame(main)
        self.af_var = tk.StringVar(value=self.cfg["audio_format"])
        self.aq_var = tk.StringVar(value=self.cfg["audio_quality"])
        ttk.Label(self.audio_frame, text="Format:").pack(side="left")
        ttk.Combobox(self.audio_frame, textvariable=self.af_var, values=AUDIO_FORMATS,
                     state="readonly", width=6).pack(side="left", padx=(4, 16))
        ttk.Label(self.audio_frame, text="Quality:").pack(side="left")
        ttk.Combobox(self.audio_frame, textvariable=self.aq_var, values=list(AUDIO_QUALITIES),
                     state="readonly", width=8).pack(side="left", padx=4)

        self.options_row = row
        row += 1

        # 5. Playlist
        self.playlist_var = tk.BooleanVar(value=self.cfg["playlist"])
        ttk.Checkbutton(main, text="Download whole playlist",
                        variable=self.playlist_var).grid(row=row, column=1, sticky="w", padx=6, pady=4)
        row += 1

        # 6. Output folder
        ttk.Label(main, text="Save to:").grid(row=row, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar(value=self.cfg["output_dir"])
        ttk.Entry(main, textvariable=self.out_var, state="readonly").grid(
            row=row, column=1, sticky="ew", padx=6)
        ttk.Button(main, text="Browse...", command=self.browse_output).grid(row=row, column=2, sticky="e")
        row += 1

        # Queue
        qframe = ttk.LabelFrame(main, text="Queue", padding=6)
        qframe.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(8, 4))
        qframe.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(qframe, columns=("status", "options", "link"),
                                 show="headings", height=4, selectmode="extended")
        for col, title, width, stretch in (("status", "Status", 100, False),
                                           ("options", "Options", 170, False),
                                           ("link", "Link", 300, True)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(qframe, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        qbtns = ttk.Frame(qframe)
        qbtns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(qbtns, text="Remove selected", command=self.remove_selected).pack(side="left")
        ttk.Button(qbtns, text="Clear finished", command=self.clear_finished).pack(side="left", padx=4)
        row += 1

        # 7. Download / Stop
        dl = ttk.Frame(main)
        dl.grid(row=row, column=0, columnspan=3, sticky="ew", pady=6)
        dl.columnconfigure(0, weight=1)
        self.download_btn = ttk.Button(dl, text="Download", style="Big.TButton", command=self.start)
        self.download_btn.grid(row=0, column=0, sticky="ew")
        self.stop_btn = ttk.Button(dl, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        row += 1

        # 8. Progress + status + log
        pframe = ttk.Frame(main)
        pframe.grid(row=row, column=0, columnspan=3, sticky="ew")
        pframe.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(pframe, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.pct_label = ttk.Label(pframe, text="0%", width=6, anchor="e")
        self.pct_label.grid(row=0, column=1)
        row += 1
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(main, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        self.log = ScrolledText(main, height=9, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.tag_configure("error", foreground="#c00000", font=("Consolas", 9, "bold"))
        self.log.tag_configure("ok", foreground="#007a00", font=("Consolas", 9, "bold"))
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=4)
        main.rowconfigure(row, weight=1)
        row += 1

        # 9./10. Bottom buttons
        bottom = ttk.Frame(main)
        bottom.grid(row=row, column=0, columnspan=3, sticky="ew")
        self.open_btn = ttk.Button(bottom, text="Open folder", command=self.open_folder)
        self.update_btn = ttk.Button(bottom, text="Update yt-dlp", command=self.update_ytdlp)
        self.update_btn.pack(side="right")
        ttk.Button(bottom, text="Settings...", command=self.open_settings).pack(side="right", padx=6)

    def _on_mode_change(self):
        if self.mode_var.get() == "Audio":
            self.video_frame.grid_remove()
            self.audio_frame.grid(row=self.options_row, column=1, columnspan=2, sticky="w", padx=6, pady=4)
        else:
            self.audio_frame.grid_remove()
            self.video_frame.grid(row=self.options_row, column=1, columnspan=2, sticky="w", padx=6, pady=4)

    # ------------------------------------------------------------- actions --

    def paste(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo(APP_TITLE, "The clipboard is empty.")
            return
        self.url_var.set(text)

    def browse_output(self):
        folder = filedialog.askdirectory(initialdir=self.out_var.get() or str(HOME),
                                         title="Choose where to save downloads")
        if folder:
            self.out_var.set(os.path.normpath(folder))
            self.remember()

    def current_options(self) -> dict:
        return {
            "mode": self.mode_var.get(),
            "video_quality": self.vq_var.get(),
            "video_format": self.vf_var.get(),
            "audio_format": self.af_var.get(),
            "audio_quality": self.aq_var.get(),
            "playlist": self.playlist_var.get(),
            "output_dir": self.out_var.get(),
        }

    def remember(self):
        self.cfg.update(self.current_options())
        save_config(self.cfg)

    def add_to_queue(self) -> bool:
        """Add the link(s) in the link field to the queue. Returns True on success."""
        text = self.url_var.get().strip()
        if not text:
            messagebox.showinfo(APP_TITLE, "Please paste a link first.")
            return False
        links = text.split()  # several links pasted at once are fine
        bad = [u for u in links if not URL_RE.match(u)]
        if bad:
            messagebox.showerror(APP_TITLE, "Invalid link:\n\n" + bad[0] +
                                 "\n\nA link should start with http:// or https://")
            return False
        options = self.current_options()
        for url in links:
            job = dict(options, url=url)
            iid = self.tree.insert("", "end", values=("Waiting", describe(job), url))
            self.jobs[iid] = job
        self.url_var.set("")
        self.remember()
        return True

    def remove_selected(self):
        for iid in self.tree.selection():
            if iid != self.current_job:
                self.tree.delete(iid)
                self.jobs.pop(iid, None)

    def clear_finished(self):
        for iid in list(self.jobs):
            if self.tree.set(iid, "status") != "Waiting" and iid != self.current_job:
                self.tree.delete(iid)
                del self.jobs[iid]

    def waiting_jobs(self):
        return [iid for iid in self.tree.get_children() if self.tree.set(iid, "status") == "Waiting"]

    def start(self):
        if self.task:
            if self.url_var.get().strip():
                self.add_to_queue()  # busy: just queue it, it will run after the current one
            return
        if self.url_var.get().strip() and not self.add_to_queue():
            return
        waiting = self.waiting_jobs()
        if not waiting:
            messagebox.showinfo(APP_TITLE, "Please paste a link first.")
            return

        # Checks before we start
        if not resolve_ytdlp(self.cfg["ytdlp_path"]):
            messagebox.showerror(APP_TITLE, "yt-dlp not found.\n\nOpen Settings and choose the "
                                 "location of yt-dlp.exe.")
            return
        if not resolve_ffmpeg_dir(self.cfg["ffmpeg_dir"]):
            jobs = [self.jobs[i] for i in waiting]
            if any(j["mode"] == "Audio" for j in jobs):
                fmt = next(j["audio_format"] for j in jobs if j["mode"] == "Audio")
                messagebox.showerror(APP_TITLE, f"ffmpeg missing - it's needed for {fmt}.\n\n"
                                     "Open Settings and choose the folder that contains ffmpeg.exe.")
                return
            if not messagebox.askyesno(APP_TITLE, "ffmpeg missing - it's needed to combine HD video "
                                       "with sound. Without it only lower quality is possible.\n\n"
                                       "Download anyway?"):
                return
        for iid in waiting:
            out = self.jobs[iid]["output_dir"]
            try:
                os.makedirs(out, exist_ok=True)
            except OSError:
                messagebox.showerror(APP_TITLE, f"Can't use the output folder:\n{out}\n\nChoose another one.")
                return

        self.stop_requested = False
        self.failed_in_run = []
        self.open_btn.pack_forget()
        self.start_next()

    def start_next(self):
        waiting = self.waiting_jobs()
        if self.stop_requested or not waiting:
            self.finish_run()
            return
        iid = waiting[0]
        job = self.jobs[iid]
        self.current_job = iid
        self.last_error = None
        self.item_text = ""
        self.tree.set(iid, "status", "Downloading...")
        self.tree.see(iid)
        self.set_progress(0)
        self.status_var.set("Starting: " + job["url"])
        self.log_line(f"\n=== {job['url']}  ({describe(job)}) ===", "ok")
        cmd = build_command(resolve_ytdlp(self.cfg["ytdlp_path"]),
                            resolve_ffmpeg_dir(self.cfg["ffmpeg_dir"]), job)
        self.run_in_background("download", cmd)

    def finish_run(self):
        self.current_job = None
        self.set_busy(None)
        if self.stop_requested:
            self.status_var.set("Stopped.")
        elif self.failed_in_run:
            n = len(self.failed_in_run)
            self.status_var.set(f"Finished - {n} download(s) failed.")
            messagebox.showwarning(APP_TITLE, f"{n} download(s) failed:\n\n" + "\n\n".join(
                f"{url}\n-> {reason}" for url, reason in self.failed_in_run))
        else:
            self.status_var.set("All downloads finished!")

    def stop(self):
        self.stop_requested = True
        self.status_var.set("Stopping...")
        kill_process(self.proc)

    def update_ytdlp(self):
        if self.task:
            messagebox.showinfo(APP_TITLE, "Please wait until the current download finishes.")
            return
        ytdlp = resolve_ytdlp(self.cfg["ytdlp_path"])
        if not ytdlp:
            messagebox.showerror(APP_TITLE, "yt-dlp not found.\n\nOpen Settings and choose the "
                                 "location of yt-dlp.exe.")
            return
        self.log_line("\n=== Updating yt-dlp ===", "ok")
        self.status_var.set("Updating yt-dlp...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.run_in_background("update", [ytdlp, "-U"])

    def open_folder(self):
        folder = self.last_folder or self.out_var.get()
        if IS_WINDOWS:
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", folder])

    # ---------------------------------------------------- background work --

    def set_busy(self, task):
        self.task = task
        self.stop_btn.configure(state="normal" if task == "download" else "disabled")
        self.update_btn.configure(state="disabled" if task else "normal")

    def run_in_background(self, task, cmd):
        self.set_busy(task)
        threading.Thread(target=self._worker, args=(task, cmd), daemon=True).start()

    def _worker(self, task, cmd):
        """Runs in a separate thread: starts yt-dlp and forwards its output."""
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1, creationflags=NO_WINDOW)
        except OSError as e:
            self.msgs.put(("line", f"ERROR: could not start yt-dlp: {e}"))
            self.msgs.put(("done", task, -1))
            return
        self.proc = proc
        for line in proc.stdout:
            self.msgs.put(("line", line.rstrip()))
        rc = proc.wait()
        self.proc = None
        self.msgs.put(("done", task, rc))

    def poll(self):
        """Runs in the GUI thread every 100 ms and handles worker messages."""
        try:
            while True:
                msg = self.msgs.get_nowait()
                if msg[0] == "line":
                    self.handle_line(msg[1])
                else:
                    self.handle_done(msg[1], msg[2])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def handle_line(self, line: str):
        if not line:
            return
        m = PROGRESS_RE.match(line)
        if m and self.task == "download":
            self.set_progress(float(m.group(1)))
            self.status_var.set(f"{self.item_text}Downloading {m.group(1)}%{m.group(2)}".strip())
            return  # don't flood the log with progress lines
        m = ITEM_RE.search(line)
        if m:
            self.item_text = f"Item {m.group(1)} of {m.group(2)} - "
        if line.startswith("[Merger]"):
            self.status_var.set(self.item_text + "Combining video and sound...")
        elif line.startswith("[ExtractAudio]"):
            self.status_var.set(self.item_text + "Converting audio...")
        if "ERROR" in line:
            self.last_error = friendly_error(line) or self.last_error or line.replace("ERROR:", "").strip()
            self.log_line(line, "error")
        else:
            self.log_line(line)

    def handle_done(self, task, rc):
        if task == "update":
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.set_progress(100 if rc == 0 else 0)
            if rc == 0:
                self.status_var.set("yt-dlp is up to date.")
                self.log_line("yt-dlp update finished.", "ok")
            else:
                self.status_var.set("Update failed - see the log below.")
                self.log_line("Update failed. If yt-dlp is in a protected folder, try running "
                              "this app as administrator.", "error")
            self.set_busy(None)
            return

        iid, job = self.current_job, self.jobs.get(self.current_job)
        if self.stop_requested:
            status = "Cancelled"
            self.log_line("Download stopped.", "error")
        elif rc == 0:
            status = "Done"
            self.set_progress(100)
            self.last_folder = job["output_dir"] if job else None
            self.open_btn.pack(side="left")
            self.log_line("Finished!", "ok")
        else:
            status = "Failed"
            reason = self.last_error or "Unknown error - see the log."
            self.failed_in_run.append((job["url"] if job else "?", reason))
            self.log_line("Problem: " + reason, "error")
        if iid and self.tree.exists(iid):
            self.tree.set(iid, "status", status)
        self.start_next()

    # ------------------------------------------------------------- display --

    def set_progress(self, pct: float):
        self.progress["value"] = pct
        self.pct_label.configure(text=f"{pct:.0f}%")

    def log_line(self, text: str, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        if int(self.log.index("end-1c").split(".")[0]) > 2000:  # keep the log small
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ settings --

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.resizable(True, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ytdlp_var = tk.StringVar(value=self.cfg["ytdlp_path"])
        ffmpeg_var = tk.StringVar(value=self.cfg["ffmpeg_dir"])

        def pick_ytdlp():
            path = filedialog.askopenfilename(parent=win, title="Select yt-dlp.exe",
                                              initialdir=str(HOME),
                                              filetypes=[("yt-dlp", "yt-dlp*.exe"), ("All files", "*.*")])
            if path:
                ytdlp_var.set(os.path.normpath(path))

        def pick_ffmpeg():
            path = filedialog.askdirectory(parent=win, title="Select the folder containing ffmpeg.exe",
                                           initialdir=ffmpeg_var.get() or str(HOME))
            if path:
                ffmpeg_var.set(os.path.normpath(path))

        ttk.Label(frm, text="yt-dlp.exe:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=ytdlp_var, width=55).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=pick_ytdlp).grid(row=0, column=2)

        ttk.Label(frm, text="ffmpeg folder:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=ffmpeg_var, width=55).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Browse...", command=pick_ffmpeg).grid(row=1, column=2)

        ttk.Label(frm, text="(The ffmpeg folder must contain ffmpeg.exe and ffprobe.exe.)",
                  foreground="gray").grid(row=2, column=1, sticky="w", padx=6)

        def save():
            problems = []
            if not resolve_ytdlp(ytdlp_var.get()):
                problems.append("yt-dlp.exe was not found at that location.")
            if not resolve_ffmpeg_dir(ffmpeg_var.get()):
                problems.append("ffmpeg.exe was not found in that folder.")
            if problems and not messagebox.askyesno(
                    "Settings", "\n".join(problems) + "\n\nSave anyway?", parent=win):
                return
            self.cfg["ytdlp_path"] = ytdlp_var.get().strip()
            self.cfg["ffmpeg_dir"] = ffmpeg_var.get().strip()
            save_config(self.cfg)
            win.destroy()

        bf = ttk.Frame(frm)
        bf.grid(row=3, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(bf, text="Save", command=save).pack(side="right", padx=6)
        win.grab_set()

    def on_close(self):
        if self.task == "download":
            if not messagebox.askyesno(APP_TITLE, "A download is still running.\nStop it and quit?"):
                return
            self.stop_requested = True
            kill_process(self.proc)
        self.remember()
        self.root.destroy()


def main():
    if IS_WINDOWS:
        try:  # sharp text on high-resolution screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
