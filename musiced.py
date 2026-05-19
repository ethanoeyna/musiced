import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QPushButton, QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------------
# Config & paths
# ----------------------------------------------------------------------------

APP_NAME = "Musiced"
APP_DIR = Path(__file__).resolve().parent

# Writable state lives next to the exe when bundled (PyInstaller's _MEIPASS
# is a temp dir that gets wiped on each launch), next to the script otherwise.
if getattr(sys, "frozen", False):
    DATA_DIR = Path(sys.executable).parent / "data"
else:
    DATA_DIR = APP_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
QUEUE_PATH = DATA_DIR / "queue.json"

MAX_PARALLEL = 3
MAX_RETRIES = 2

THUMBNAIL_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Output format catalog. Each entry: yt-dlp codec key, target extension, label
# shown in the UI, and the optional kbps the FFmpegExtractAudio postprocessor
# should pass when encoding a lossy codec. FLAC is lossless so kbps is unused.
#
# Sizes are roughly what we see for a 4-minute YouTube source:
#   flac (lossless container around lossy source) → ~35 MB
#   opus 192                                       → ~5  MB
#   mp3  320                                       → ~9  MB
#   m4a/aac 256                                    → ~7  MB
#
# Default stays "flac" so existing users see no behaviour change on upgrade.
FORMATS = {
    "flac":     {"codec": "flac", "ext": ".flac", "label": "FLAC (lossless)",          "kbps": None},
    "opus_192": {"codec": "opus", "ext": ".opus", "label": "Opus 192 kbps (small)",    "kbps": "192"},
    "opus_256": {"codec": "opus", "ext": ".opus", "label": "Opus 256 kbps (transparent)", "kbps": "256"},
    "mp3_320":  {"codec": "mp3",  "ext": ".mp3",  "label": "MP3 320 kbps (compatible)", "kbps": "320"},
    "m4a_256":  {"codec": "m4a",  "ext": ".m4a",  "label": "AAC 256 kbps (compatible)", "kbps": "256"},
}
DEFAULT_FORMAT_KEY = "flac"
# All audio extensions Musiced may have written — used by the orphan sweeper so
# leftover .flac thumbnails are cleaned up even when the user is now downloading
# as Opus, etc.
AUDIO_EXTS = tuple({fmt["ext"] for fmt in FORMATS.values()})


def _default_music_dir() -> Path:
    return Path.home() / "Downloads" / "musiced"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


# ----------------------------------------------------------------------------
# Bundled binary resolution (ffmpeg + ffprobe)
# ----------------------------------------------------------------------------

def _resource_base() -> Path:
    """Where bundled resources live, regardless of how we're packaged."""
    if hasattr(sys, "_MEIPASS"):                # PyInstaller onefile temp dir
        return Path(sys._MEIPASS)
    if getattr(sys, "frozen", False):            # PyInstaller onedir / py2app
        return Path(sys.executable).parent
    return APP_DIR


def _bin_dir() -> Path:
    return _resource_base() / "bin"


def _exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


def _find_binary(name: str) -> str | None:
    """Look in ./bin first, then system PATH."""
    bundled = _bin_dir() / _exe_name(name)
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name)


def find_ffmpeg() -> str | None:
    return _find_binary("ffmpeg")


def find_ffprobe() -> str | None:
    return _find_binary("ffprobe")


# ----------------------------------------------------------------------------
# Style
# ----------------------------------------------------------------------------

FONT_FAMILY = "Consolas"


def _make_font(size: int) -> QFont:
    f = QFont(FONT_FAMILY, size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


COLOR_BG_WINDOW = QColor(28, 29, 32)
COLOR_BG_BASE = QColor(40, 41, 45)
COLOR_BG_RAISED = QColor(48, 49, 53)
COLOR_BG_BORDER = QColor(58, 59, 63)
COLOR_TEXT_PRIMARY = QColor(220, 220, 220)
COLOR_TEXT_SECONDARY = QColor(153, 153, 153)
COLOR_ACCENT = QColor(34, 211, 238)
COLOR_ACCENT_HOVER = QColor(103, 224, 240)
COLOR_TEXT_ON_ACCENT = QColor(10, 10, 10)
COLOR_STATUS_OK = QColor(120, 200, 120)
COLOR_STATUS_ERR = QColor(224, 96, 96)
COLOR_STATUS_INFO = QColor(180, 180, 180)
COLOR_STATUS_SKIP = QColor(160, 160, 200)


def _apply_style(app: QApplication):
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, COLOR_BG_WINDOW)
    p.setColor(QPalette.ColorRole.Base, COLOR_BG_BASE)
    p.setColor(QPalette.ColorRole.AlternateBase, COLOR_BG_RAISED)
    p.setColor(QPalette.ColorRole.WindowText, COLOR_TEXT_PRIMARY)
    p.setColor(QPalette.ColorRole.Text, COLOR_TEXT_PRIMARY)
    p.setColor(QPalette.ColorRole.ButtonText, COLOR_TEXT_PRIMARY)
    p.setColor(QPalette.ColorRole.Button, COLOR_BG_RAISED)
    p.setColor(QPalette.ColorRole.Highlight, COLOR_ACCENT)
    p.setColor(QPalette.ColorRole.HighlightedText, COLOR_TEXT_ON_ACCENT)
    p.setColor(QPalette.ColorRole.ToolTipBase, COLOR_BG_RAISED)
    p.setColor(QPalette.ColorRole.ToolTipText, COLOR_TEXT_PRIMARY)
    app.setPalette(p)
    app.setFont(_make_font(10))

    app.setStyleSheet(f"""
        QPushButton {{
            background-color: {COLOR_BG_RAISED.name()};
            color: {COLOR_TEXT_PRIMARY.name()};
            border: 1px solid {COLOR_BG_BORDER.name()};
            border-radius: 4px;
            padding: 6px 14px;
            min-width: 110px;
        }}
        QPushButton:hover {{ background-color: #54555a; }}
        QPushButton:pressed {{ background-color: #45464a; }}
        QPushButton:disabled {{ color: {COLOR_TEXT_SECONDARY.name()}; }}
        QPushButton[accent="true"] {{
            background-color: {COLOR_ACCENT.name()};
            color: {COLOR_TEXT_ON_ACCENT.name()};
            font-weight: bold;
            border: 1px solid {COLOR_ACCENT.name()};
        }}
        QPushButton[accent="true"]:hover {{
            background-color: {COLOR_ACCENT_HOVER.name()};
            border-color: {COLOR_ACCENT_HOVER.name()};
        }}
        QPushButton[accent="true"]:disabled {{
            background-color: {COLOR_BG_RAISED.name()};
            color: {COLOR_TEXT_SECONDARY.name()};
            border-color: {COLOR_BG_BORDER.name()};
            font-weight: normal;
        }}
        QLineEdit {{
            background-color: {COLOR_BG_BASE.name()};
            border: 1px solid {COLOR_BG_BORDER.name()};
            border-radius: 4px;
            padding: 6px 10px;
        }}
        QListWidget {{
            background-color: {COLOR_BG_BASE.name()};
            border: 1px solid {COLOR_BG_BORDER.name()};
            border-radius: 4px;
        }}
        QMenu {{
            background-color: {COLOR_BG_RAISED.name()};
            border: 1px solid {COLOR_BG_BORDER.name()};
            padding: 4px 0;
        }}
        QMenu::item {{
            padding: 6px 18px;
            color: {COLOR_TEXT_PRIMARY.name()};
            background-color: transparent;
        }}
        QMenu::item:selected {{
            background-color: {COLOR_ACCENT.name()};
            color: {COLOR_TEXT_ON_ACCENT.name()};
        }}
    """)


# ----------------------------------------------------------------------------
# yt-dlp helpers
# ----------------------------------------------------------------------------

INVALID_FNAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\([B0]|\[\d+;\d+m|\[\d+m')


def _strip_ansi(s: str) -> str:
    return ANSI_ESCAPE.sub("", s).strip()


def _safe_filename(name: str) -> str:
    return INVALID_FNAME.sub("_", name).strip(" .")


def _is_youtube_playlist(url: str) -> bool:
    return ("youtube.com" in url or "youtu.be" in url) and (
        "list=" in url or "/playlist" in url
    )


def _expand_youtube_playlist(url: str) -> list[dict]:
    from yt_dlp import YoutubeDL
    opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    out = []
    for e in entries:
        if not e:
            continue
        u = e.get("url") or e.get("webpage_url")
        if u and not u.startswith("http"):
            u = f"https://www.youtube.com/watch?v={u}"
        if u:
            out.append({"url": u, "title": e.get("title") or u})
    return out


def _fetch_title(url: str) -> str | None:
    from yt_dlp import YoutubeDL
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return info.get("title")
    except Exception:
        return None


def _output_path_for(out_dir: Path, title: str, ext: str = ".flac") -> Path:
    if not ext.startswith("."):
        ext = "." + ext
    return out_dir / f"{_safe_filename(title)}{ext}"


def _existing_audio_for(out_dir: Path, title: str) -> Path | None:
    """Return an existing audio file for `title` regardless of extension.

    Lets us skip re-downloading a song the user already has in any format —
    e.g. they downloaded as FLAC last week and switched to Opus, we still
    skip instead of re-encoding the same source twice.
    """
    safe = _safe_filename(title)
    for ext in AUDIO_EXTS:
        candidate = out_dir / f"{safe}{ext}"
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _cleanup_thumbnails(out_dir: Path, title: str):
    safe = _safe_filename(title)
    for ext in THUMBNAIL_EXTS:
        for candidate in out_dir.glob(f"{safe}*{ext}"):
            try:
                candidate.unlink()
            except OSError:
                pass
    for ext in (".part", ".ytdl", ".temp"):
        for candidate in out_dir.glob(f"{safe}*{ext}"):
            try:
                candidate.unlink()
            except OSError:
                pass


def _cleanup_all_orphans(out_dir: Path):
    if not out_dir.exists():
        return
    # Build the set of "real" audio stems we want to keep.
    audio_stems: set[str] = set()
    for ext in AUDIO_EXTS:
        audio_stems.update(p.stem for p in out_dir.glob(f"*{ext}"))
    for ext in THUMBNAIL_EXTS + (".part", ".ytdl", ".temp"):
        for candidate in out_dir.glob(f"*{ext}"):
            if (
                candidate.stem in audio_stems
                or candidate.stem.rsplit(".", 1)[0] in audio_stems
            ):
                try:
                    candidate.unlink()
                except OSError:
                    pass


# ----------------------------------------------------------------------------
# Job model
# ----------------------------------------------------------------------------

class DownloadJob:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"

    def __init__(self, url: str, out_dir: Path, display: str | None = None):
        self.url = url
        self.out_dir = out_dir
        self.status = self.QUEUED
        self.title = display or url
        self.progress = 0
        self.error = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "out_dir": str(self.out_dir),
            "status": self.status,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadJob":
        j = cls(d["url"], Path(d["out_dir"]), display=d.get("title"))
        j.status = d.get("status", cls.QUEUED)
        if j.status == cls.DOWNLOADING:
            j.status = cls.QUEUED
        return j


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------

class DownloadWorker(QObject):
    job_started = pyqtSignal(int)
    job_progress = pyqtSignal(int, str, int)
    job_finished = pyqtSignal(int, str, str)
    all_done = pyqtSignal()

    def __init__(self, jobs: list[DownloadJob], indices: list[int],
                 ffmpeg_location: str | None,
                 format_key: str = DEFAULT_FORMAT_KEY):
        super().__init__()
        self.jobs = jobs
        self.indices = indices
        self.ffmpeg_location = ffmpeg_location
        # Snapshot the format at worker construction. If the user changes the
        # dropdown mid-batch, in-flight jobs keep the format they started with.
        self.format_spec = FORMATS.get(format_key) or FORMATS[DEFAULT_FORMAT_KEY]

    def run(self):
        try:
            from yt_dlp import YoutubeDL  # noqa: F401
        except ImportError:
            for i in self.indices:
                self.job_finished.emit(
                    i, DownloadJob.FAILED,
                    "yt-dlp not installed. Run: uv add yt-dlp"
                )
            self.all_done.emit()
            return

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            futures: dict[Future, int] = {}
            for job, list_i in zip(self.jobs, self.indices):
                fut = pool.submit(self._run_one, job, list_i)
                futures[fut] = list_i

            for fut in futures:
                try:
                    fut.result()
                except Exception:
                    pass

        self.all_done.emit()

    def _run_one(self, job: DownloadJob, list_i: int):
        from yt_dlp import YoutubeDL

        # Skip if a file with this title already exists in ANY of our known
        # output formats — not just the currently-selected one. Switching
        # the dropdown shouldn't cause us to re-download tracks the user has.
        existing = _existing_audio_for(job.out_dir, job.title)
        if existing is not None:
            _cleanup_thumbnails(job.out_dir, job.title)
            self.job_finished.emit(
                list_i, DownloadJob.SKIPPED,
                f"Already exists: {existing.name}",
            )
            return

        self.job_started.emit(list_i)

        def hook(d, idx=list_i):
            if d.get("status") == "downloading":
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                pct_str = _strip_ansi(pct_str)
                try:
                    pct = int(float(pct_str))
                except ValueError:
                    pct = 0
                title = d.get("info_dict", {}).get("title") or job.title
                self.job_progress.emit(idx, title, pct)
            elif d.get("status") == "finished":
                title = d.get("info_dict", {}).get("title") or job.title
                self.job_progress.emit(idx, title, 100)

        # Build the FFmpegExtractAudio postprocessor block. FLAC is lossless,
        # so we omit the quality field; for lossy codecs we pass preferredquality
        # which yt-dlp surfaces to ffmpeg as the target bitrate.
        extract_pp: dict = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": self.format_spec["codec"],
        }
        if self.format_spec["kbps"]:
            extract_pp["preferredquality"] = self.format_spec["kbps"]

        opts = {
            "format": (
                "bestaudio[ext=m4a]/bestaudio[ext=webm]/"
                "bestaudio[ext=opus]/bestaudio/best"
            ),
            "outtmpl": str(job.out_dir / "%(title)s.%(ext)s"),
            "postprocessors": [
                extract_pp,
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "noprogress": True,
            "noplaylist": True,
            "retries": 3,
            "fragment_retries": 3,
            "keepvideo": False,
        }

        # Point yt-dlp at our bundled ffmpeg/ffprobe (covers both binaries)
        if self.ffmpeg_location:
            opts["ffmpeg_location"] = self.ffmpeg_location

        last_err = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                job.out_dir.mkdir(parents=True, exist_ok=True)
                with YoutubeDL(opts) as ydl:
                    ydl.download([job.url])
                _cleanup_thumbnails(job.out_dir, job.title)
                self.job_finished.emit(list_i, DownloadJob.DONE, "")
                return
            except Exception as e:
                last_err = str(e)
                if attempt < MAX_RETRIES:
                    time.sleep(1.5 * (attempt + 1))
                    continue

        self.job_finished.emit(list_i, DownloadJob.FAILED, _strip_ansi(last_err))


# ----------------------------------------------------------------------------
# Title pre-fetch worker
# ----------------------------------------------------------------------------

class TitleFetcher(QObject):
    fetched = pyqtSignal(int, str)

    def __init__(self, list_i: int, url: str):
        super().__init__()
        self.list_i = list_i
        self.url = url

    def run(self):
        t = _fetch_title(self.url)
        if t:
            self.fetched.emit(self.list_i, t)


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------

class Musiced(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 600)
        self.setMinimumSize(640, 420)

        self._config = _load_json(CONFIG_PATH, {})
        self._out_dir = Path(
            self._config.get("out_dir") or _default_music_dir()
        )
        self._out_dir.mkdir(parents=True, exist_ok=True)
        # Output format. Stored in config so the choice survives restarts.
        # Validate against the catalog — unknown values fall back to default.
        saved_fmt = self._config.get("format")
        self._format_key = saved_fmt if saved_fmt in FORMATS else DEFAULT_FORMAT_KEY
        self._jobs: list[DownloadJob] = []
        self._worker_thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._title_threads: list[QThread] = []

        # Resolve ffmpeg/ffprobe once at startup
        self._ffmpeg_path = find_ffmpeg()
        self._ffprobe_path = find_ffprobe()
        # yt-dlp's ffmpeg_location can be a file OR the directory containing both.
        # Passing the directory is most reliable — it auto-discovers ffmpeg + ffprobe.
        self._ffmpeg_dir: str | None = None
        if self._ffmpeg_path:
            bundled_dir = _bin_dir()
            if Path(self._ffmpeg_path).parent == bundled_dir:
                self._ffmpeg_dir = str(bundled_dir)
            else:
                self._ffmpeg_dir = str(Path(self._ffmpeg_path).parent)

        self.setFont(_make_font(10))
        self._build_ui()
        self._restore_queue()
        self._refresh_out_label()
        self._refresh_ffmpeg_status()
        self._update_start_btn()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.out_label = QLabel("")
        self.out_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY.name()};")
        top.addWidget(self.out_label)
        top.addStretch(1)
        # Format selector. Order in the dropdown follows insertion order in
        # FORMATS — FLAC first to preserve the historical default at the top
        # of the list, then smaller-file options below.
        format_label = QLabel("Format:")
        format_label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY.name()};")
        top.addWidget(format_label)
        self.format_combo = QComboBox()
        for key, spec in FORMATS.items():
            self.format_combo.addItem(spec["label"], userData=key)
        # Select the persisted choice
        for i in range(self.format_combo.count()):
            if self.format_combo.itemData(i) == self._format_key:
                self.format_combo.setCurrentIndex(i)
                break
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        top.addWidget(self.format_combo)
        self.change_dir_btn = QPushButton("Change folder")
        self.change_dir_btn.clicked.connect(self._on_change_dir)
        top.addWidget(self.change_dir_btn)
        self.open_dir_btn = QPushButton("Open folder")
        self.open_dir_btn.clicked.connect(self._on_open_dir)
        top.addWidget(self.open_dir_btn)
        main.addLayout(top)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Paste a URL — YouTube (track/playlist), SoundCloud, etc."
        )
        self.url_input.returnPressed.connect(self._on_add_url)
        url_row.addWidget(self.url_input, stretch=1)
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._on_add_url)
        url_row.addWidget(self.add_btn)
        main.addLayout(url_row)

        self.queue_list = QListWidget()
        self.queue_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.queue_list.customContextMenuRequested.connect(
            self._on_queue_context_menu
        )
        main.addWidget(self.queue_list, stretch=1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY.name()};"
        )
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self._on_clear)
        bottom.addWidget(self.clear_btn)
        self.start_btn = QPushButton("Download")
        self.start_btn.setProperty("accent", True)
        self.start_btn.clicked.connect(self._on_start)
        self.start_btn.setEnabled(False)
        bottom.addWidget(self.start_btn)
        main.addLayout(bottom)

    def _refresh_ffmpeg_status(self):
        missing = []
        if not self._ffmpeg_path:
            missing.append("ffmpeg")
        if not self._ffprobe_path:
            missing.append("ffprobe")
        if missing:
            joined = " and ".join(missing)
            self._set_status(
                f"Missing: {joined}. "
                f"Place in ./bin/ or install on PATH.",
                COLOR_STATUS_ERR,
            )

    def _restore_queue(self):
        data = _load_json(QUEUE_PATH, [])
        for d in data:
            try:
                job = DownloadJob.from_dict(d)
                if job.status in (DownloadJob.DONE, DownloadJob.SKIPPED):
                    continue
                self._jobs.append(job)
                self._render_job_row(job, len(self._jobs) - 1)
            except Exception:
                continue

    def _save_queue(self):
        _save_json(QUEUE_PATH, [j.to_dict() for j in self._jobs])

    def _render_job_row(self, job: DownloadJob, list_i: int):
        if job.status == DownloadJob.QUEUED:
            text = f"[queued]  {job.title}"
            color = COLOR_TEXT_PRIMARY
        elif job.status == DownloadJob.DOWNLOADING:
            text = f"[{job.progress:>3}%]    {job.title}"
            color = COLOR_TEXT_PRIMARY
        elif job.status == DownloadJob.DONE:
            text = f"[done]    {job.title}"
            color = COLOR_STATUS_OK
        elif job.status == DownloadJob.SKIPPED:
            text = f"[skip]    {job.title}"
            color = COLOR_STATUS_SKIP
        else:
            err = _strip_ansi(job.error) if job.error else ""
            short = err.splitlines()[0][:120] if err else "(unknown)"
            text = f"[error]   {job.title}  —  {short}"
            color = COLOR_STATUS_ERR

        if list_i < self.queue_list.count():
            item = self.queue_list.item(list_i)
        else:
            item = QListWidgetItem()
            self.queue_list.addItem(item)
        item.setText(text)
        item.setForeground(color)

    def _refresh_out_label(self):
        self.out_label.setText(f"Saving to:  {self._out_dir}")

    def _set_status(self, text: str, color: QColor = COLOR_STATUS_INFO):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color.name()};")

    def _update_start_btn(self):
        has_pending = any(
            j.status in (DownloadJob.QUEUED, DownloadJob.FAILED)
            for j in self._jobs
        )
        ready = (
            self._worker_thread is None
            and has_pending
            and self._ffmpeg_path is not None
            and self._ffprobe_path is not None
        )
        self.start_btn.setEnabled(ready)

    def _on_format_changed(self, index: int):
        # Persist the new format choice immediately. Any *in-flight* worker
        # keeps its captured format_spec — only the next batch picks up the
        # new value. This keeps a running job's output extension consistent
        # with what was selected when the user hit Download.
        key = self.format_combo.itemData(index)
        if key not in FORMATS:
            return
        self._format_key = key
        self._config["format"] = key
        _save_json(CONFIG_PATH, self._config)

    def _on_change_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choose music folder", str(self._out_dir)
        )
        if path:
            self._out_dir = Path(path)
            self._out_dir.mkdir(parents=True, exist_ok=True)
            self._config["out_dir"] = str(self._out_dir)
            _save_json(CONFIG_PATH, self._config)
            self._refresh_out_label()

    def _on_open_dir(self):
        self._out_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(self._out_dir)
        elif sys.platform == "darwin":
            os.system(f'open "{self._out_dir}"')
        else:
            os.system(f'xdg-open "{self._out_dir}" >/dev/null 2>&1 &')

    def _on_add_url(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self.url_input.clear()
        try:
            if _is_youtube_playlist(url):
                self._add_youtube_playlist(url)
            else:
                self._add_single(url)
        except Exception as e:
            self._set_status(f"Add failed: {e}", COLOR_STATUS_ERR)
            return
        self._save_queue()
        self._update_start_btn()

    def _add_single(self, url: str):
        job = DownloadJob(url, self._out_dir, display=url)
        self._jobs.append(job)
        list_i = len(self._jobs) - 1
        self._render_job_row(job, list_i)
        self._prefetch_title(list_i, url)

    def _add_youtube_playlist(self, url: str):
        self._set_status("Expanding YouTube playlist…")
        QApplication.processEvents()
        entries = _expand_youtube_playlist(url)
        if not entries:
            self._set_status("No videos found in playlist.", COLOR_STATUS_ERR)
            return
        for e in entries:
            job = DownloadJob(e["url"], self._out_dir, display=e["title"])
            self._jobs.append(job)
            self._render_job_row(job, len(self._jobs) - 1)
        self._set_status(f"Added {len(entries)} video(s) from playlist.")

    def _prefetch_title(self, list_i: int, url: str):
        thread = QThread()
        fetcher = TitleFetcher(list_i, url)
        fetcher.moveToThread(thread)
        thread.started.connect(fetcher.run)
        fetcher.fetched.connect(self._on_title_fetched)
        fetcher.fetched.connect(thread.quit)
        thread.finished.connect(lambda: self._title_threads.remove(thread))
        self._title_threads.append(thread)
        thread.start()

    def _on_title_fetched(self, list_i: int, title: str):
        if list_i >= len(self._jobs):
            return
        job = self._jobs[list_i]
        if job.status != DownloadJob.QUEUED:
            return
        job.title = title
        self._render_job_row(job, list_i)
        self._save_queue()

    def _on_queue_context_menu(self, pos):
        item = self.queue_list.itemAt(pos)
        if item is None:
            return
        list_i = self.queue_list.row(item)
        job = self._jobs[list_i]

        menu = QMenu(self)
        if job.status == DownloadJob.FAILED:
            retry = menu.addAction("Retry this item")
            retry.triggered.connect(lambda: self._retry_one(list_i))
        if job.status != DownloadJob.DOWNLOADING:
            remove = menu.addAction("Remove from queue")
            remove.triggered.connect(lambda: self._remove_one(list_i))
        if not menu.actions():
            return
        menu.exec(self.queue_list.viewport().mapToGlobal(pos))

    def _remove_one(self, list_i: int):
        if self._worker_thread is not None:
            return
        del self._jobs[list_i]
        self.queue_list.takeItem(list_i)
        self._save_queue()
        self._update_start_btn()

    def _retry_one(self, list_i: int):
        if self._worker_thread is not None:
            return
        job = self._jobs[list_i]
        job.status = DownloadJob.QUEUED
        job.progress = 0
        job.error = ""
        self._render_job_row(job, list_i)
        self._save_queue()
        self._update_start_btn()

    def _on_clear(self):
        if self._worker_thread is not None:
            return
        self._jobs.clear()
        self.queue_list.clear()
        self._save_queue()
        self._update_start_btn()
        self._set_status("Ready.")

    def _on_start(self):
        if not self._ffmpeg_path or not self._ffprobe_path:
            self._refresh_ffmpeg_status()
            return

        pending_idx = [
            i for i, j in enumerate(self._jobs)
            if j.status in (DownloadJob.QUEUED, DownloadJob.FAILED)
        ]
        if not pending_idx:
            return

        for i in pending_idx:
            self._jobs[i].status = DownloadJob.QUEUED
            self._jobs[i].error = ""
            self._jobs[i].progress = 0
            self._render_job_row(self._jobs[i], i)

        self.start_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.url_input.setEnabled(False)
        self.format_combo.setEnabled(False)
        self._set_status(
            f"Downloading {len(pending_idx)} item(s) "
            f"({MAX_PARALLEL} at a time)…"
        )

        jobs_to_run = [self._jobs[i] for i in pending_idx]

        self._worker_thread = QThread()
        self._worker = DownloadWorker(
            jobs_to_run, pending_idx, self._ffmpeg_dir,
            format_key=self._format_key,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.job_started.connect(self._on_job_started)
        self._worker.job_progress.connect(self._on_job_progress)
        self._worker.job_finished.connect(self._on_job_finished)
        self._worker.all_done.connect(self._on_all_done)
        self._worker_thread.start()

    def _on_job_started(self, list_i: int):
        job = self._jobs[list_i]
        job.status = DownloadJob.DOWNLOADING
        self._render_job_row(job, list_i)

    def _on_job_progress(self, list_i: int, title: str, pct: int):
        job = self._jobs[list_i]
        if job.title == job.url or not job.title:
            job.title = title
        job.progress = pct
        self._render_job_row(job, list_i)

    def _on_job_finished(self, list_i: int, status: str, msg: str):
        job = self._jobs[list_i]
        if status == DownloadJob.SKIPPED:
            job.status = DownloadJob.SKIPPED
        elif status == DownloadJob.DONE:
            job.status = DownloadJob.DONE
        else:
            job.status = DownloadJob.FAILED
            job.error = msg
        self._render_job_row(job, list_i)
        self._save_queue()

    def _on_all_done(self):
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
        self._worker_thread = None
        self._worker = None
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.url_input.setEnabled(True)
        self.format_combo.setEnabled(True)

        _cleanup_all_orphans(self._out_dir)
        self._update_start_btn()

        done = sum(1 for j in self._jobs if j.status == DownloadJob.DONE)
        skipped = sum(1 for j in self._jobs if j.status == DownloadJob.SKIPPED)
        failed = sum(1 for j in self._jobs if j.status == DownloadJob.FAILED)
        parts = [f"{done} ok"]
        if skipped:
            parts.append(f"{skipped} skipped")
        if failed:
            parts.append(f"{failed} failed")
        color = (
            COLOR_STATUS_ERR if failed else
            COLOR_STATUS_OK if done else
            COLOR_STATUS_INFO
        )
        self._set_status("Done. " + ", ".join(parts) + ".", color)
        self._save_queue()


def main():
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"
    app = QApplication(sys.argv)
    _apply_style(app)
    window = Musiced()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()