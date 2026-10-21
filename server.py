"""FastAPI workbench for MEDUSA line-level HTR.

Serves the single page UI, scans a workspace of ALTO + bitmap pairs, and runs
batch recognition on the local Qwen3.5-VL weights (inside WSL).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
from pydantic import BaseModel

from alto import AltoPage, IMAGE_EXTS, parse_alto
from inference import DEFAULT_PROMPT, PROMPT_PRESETS, Recognizer

Image.MAX_IMAGE_PIXELS = None

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
WORKSPACE = Path(os.environ.get("MEDUSA_WORKSPACE", str(BASE / "workspace"))).expanduser().resolve()
CACHE = WORKSPACE / ".cache"
RESULTS = WORKSPACE / ".results"
for _d in (CACHE / "img", CACHE / "thumb", CACHE / "line", RESULTS, WORKSPACE / "uploads"):
    _d.mkdir(parents=True, exist_ok=True)

DISPLAY_EDGE = int(os.environ.get("MEDUSA_DISPLAY_EDGE", 3000))
THUMB_WIDTH = int(os.environ.get("MEDUSA_THUMB_WIDTH", 260))
ALTO_SUFFIXES = {".xml", ".alto"}
IMAGE_SUFFIXES = {s.lower() for s in IMAGE_EXTS}

MODELS_DIR = Path(os.environ.get("MEDUSA_MODELS_DIR", str(BASE / "models"))).expanduser().resolve()
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_CATALOG = [
    {
        "id": "Medusa0.2Line-4B",
        "repo": "ENC-PSL/Medusa0.2Line-4B",
        "label": "MEDUSA 0.2 · 4B",
        "short": "4B",
        "params": "4.5B",
        "approxBytes": 9_079_166_568,
        "loadMode": "bf16",
        "note": "当前主力模型，约 9 GB，12 GB 显存可直接以 bf16 运行。",
    },
    {
        "id": "Medusa0.2Line-9B",
        "repo": "ENC-PSL/Medusa0.2Line-9B",
        "label": "MEDUSA 0.2 · 9B",
        "short": "9B",
        "params": "~9B",
        "approxBytes": 18_820_520_592,
        "loadMode": "4bit",
        "note": "更准确的大模型，约 18.8 GB；12 GB 显存下自动使用 4-bit 量化，速度较慢。",
    },
]
MODEL_BY_ID = {entry["id"]: entry for entry in MODEL_CATALOG}

FONT_SUFFIXES = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}
FONT_MIME = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_FONT_DIR_ENV = os.environ.get("MEDUSA_FONT_DIR", "").strip()
FONT_DIRS = [Path(_FONT_DIR_ENV)] if _FONT_DIR_ENV else []
FONT_DIRS += [BASE / "fonts", WORKSPACE / "fonts", WORKSPACE]


def _font_files() -> dict[str, Path]:
    """Junicode (and friends) placed next to the model or in the workspace."""
    found: dict[str, Path] = {}
    for directory in FONT_DIRS:
        try:
            if not directory.is_dir():
                continue
            entries = directory.iterdir()
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file() or entry.suffix.lower() not in FONT_SUFFIXES:
                continue
            if "junicode" not in entry.name.lower():
                continue
            found.setdefault(entry.name, entry)
    return dict(sorted(found.items()))


# --------------------------------------------------------------------------- #
# Model catalog / downloader
# --------------------------------------------------------------------------- #
def _model_dir(entry: dict) -> Path:
    return MODELS_DIR / entry["id"]


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _model_installed(entry: dict) -> bool:
    path = _model_dir(entry)
    if not (path / "config.json").is_file():
        return False
    size = _dir_size(path)
    return size >= int(entry["approxBytes"] * 0.8)


class DownloadCancelled(Exception):
    pass


class ModelDownloader:
    """Streaming downloader for the two MEDUSA repos (hf-mirror by default)."""

    def __init__(self) -> None:
        self.endpoint = os.environ.get("MEDUSA_HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
        self._lock = threading.Lock()
        self._cancel = False
        self.speed = 0.0
        self._last_done = 0
        self._last_time = 0.0
        self._reset("idle")

    def _reset(self, state: str) -> None:
        self.state = state
        self.model_id: Optional[str] = None
        self.repo: Optional[str] = None
        self.target: Optional[str] = None
        self.total = 0
        self.done = 0
        self.files_done = 0
        self.files_total = 0
        self.current_file = ""
        self.error = ""
        self.started = 0.0
        self.finished = 0.0
        self._last_done = 0
        self._last_time = 0.0
        self.speed = 0.0

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            if self.state == "downloading" and self._last_time and now - self._last_time >= 0.5:
                inst = max(0, self.done - self._last_done) / (now - self._last_time)
                self.speed = inst if self.speed <= 0 else self.speed * 0.7 + inst * 0.3
                self._last_done = self.done
                self._last_time = now
            done = min(self.done, self.total) if self.total else self.done
            return {
                "state": self.state,
                "modelId": self.model_id,
                "repo": self.repo,
                "target": self.target,
                "total": self.total,
                "done": done,
                "filesDone": self.files_done,
                "filesTotal": self.files_total,
                "currentFile": self.current_file,
                "speed": round(self.speed, 1),
                "error": self.error,
                "started": self.started,
                "finished": self.finished,
            }

    def start(self, entry: dict) -> dict:
        with self._lock:
            if self.state in ("starting", "downloading", "cancelling"):
                raise HTTPException(409, "已有模型正在下载")
            self._reset("starting")
            self.model_id = entry["id"]
            self.repo = entry["repo"]
            self.target = str(_model_dir(entry))
            self.total = int(entry["approxBytes"])
            self.started = time.time()
            self._cancel = False
            self._last_time = time.time()
        threading.Thread(
            target=self._run, args=(entry,), daemon=True, name="model-download"
        ).start()
        return self.snapshot()

    def cancel(self) -> dict:
        with self._lock:
            if self.state in ("starting", "downloading"):
                self._cancel = True
                self.state = "cancelling"
        return self.snapshot()

    # ------------------------------------------------------------------ #
    def _metadata(self, repo: str) -> list[tuple[str, int]]:
        url = f"{self.endpoint}/api/models/{repo}?blobs=true"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "medusa-workbench/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        files: list[tuple[str, int]] = []
        for sibling in data.get("siblings", []):
            name = sibling.get("rfilename")
            if not name:
                continue
            files.append((name, int(sibling.get("size") or 0)))
        if not files:
            raise RuntimeError("下载源返回的文件列表为空")
        return files

    def _monitor(self, target: Path) -> None:
        while True:
            with self._lock:
                if self.state not in ("starting", "downloading", "cancelling"):
                    return
            size = _dir_size(target)
            with self._lock:
                if self.state in ("starting", "downloading", "cancelling"):
                    self.done = size
            time.sleep(1.0)

    def _run(self, entry: dict) -> None:
        target = Path(self.target or _model_dir(entry))
        try:
            files = self._metadata(entry["repo"])
            if self._cancel:
                raise DownloadCancelled()
            total = sum(size for _name, size in files)
            free = shutil.disk_usage(MODELS_DIR).free
            if total and free < total + 1_500_000_000:
                raise RuntimeError(
                    f"磁盘空间不足：需要约 {total / 1e9:.1f} GB，剩余 {free / 1e9:.1f} GB"
                )
            target.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self.total = total
                self.files_total = len(files)
                self.state = "downloading"
                self._last_done = self.done
                self._last_time = time.time()
            threading.Thread(target=self._monitor, args=(target,), daemon=True).start()

            for index, (name, size) in enumerate(files):
                if self._cancel:
                    raise DownloadCancelled()
                with self._lock:
                    self.current_file = name
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not (size and dest.is_file() and dest.stat().st_size == size):
                    self._download_file(entry["repo"], name, size, dest)
                with self._lock:
                    self.files_done = index + 1

            with self._lock:
                self.done = self.total or _dir_size(target)
                self.state = "done"
                self.finished = time.time()
                self.current_file = ""
        except DownloadCancelled:
            with self._lock:
                self.state = "cancelled"
                self.finished = time.time()
        except Exception as exc:
            with self._lock:
                self.state = "error"
                self.error = str(exc) or exc.__class__.__name__
                self.finished = time.time()

    def _download_file(self, repo: str, name: str, size: int, dest: Path) -> None:
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.is_file() else 0
        if size and have >= size:
            if have > size:
                part.unlink()
                have = 0
            else:
                part.replace(dest)
                return

        url = f"{self.endpoint}/{repo}/resolve/main/{quote(name)}"
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                headers = {"User-Agent": "medusa-workbench/1.0"}
                if have:
                    headers["Range"] = f"bytes={have}-"
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=120) as response:
                    if response.status == 206 and have:
                        mode = "ab"
                    else:
                        mode = "wb"
                        have = 0
                    with part.open(mode) as handle:
                        while True:
                            if self._cancel:
                                raise DownloadCancelled()
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            have += len(chunk)
                if size and have != size:
                    raise RuntimeError(f"下载不完整（{have}/{size} 字节）")
                part.replace(dest)
                return
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    break
                time.sleep(2 + attempt * 3)
                have = part.stat().st_size if part.is_file() else 0
        raise RuntimeError(f"{name}: {last_error}")


downloader = ModelDownloader()


# --------------------------------------------------------------------------- #
# Workspace registry
# --------------------------------------------------------------------------- #
@dataclass
class Doc:
    id: str
    alto: Path
    page: AltoPage
    mtime: float
    size: int
    error: str = ""


def _empty_page(path: Path) -> AltoPage:
    return AltoPage(
        path=path,
        image=None,
        page_width=0,
        page_height=0,
        unit="pixel",
        image_width=None,
        image_height=None,
        lines=[],
        warnings=[],
    )


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._docs: dict[str, Doc] = {}
        self._lock = threading.RLock()

    def _id(self, path: Path) -> str:
        try:
            rel = str(path.relative_to(self.root))
        except ValueError:
            rel = str(path)
        return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]

    def scan(self) -> list[Doc]:
        with self._lock:
            found: dict[str, Doc] = {}
            for path in sorted(self.root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in ALTO_SUFFIXES:
                    continue
                try:
                    parts = path.relative_to(self.root).parts
                except ValueError:
                    parts = path.parts
                if any(part.startswith(".") for part in parts):
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                did = self._id(path)
                old = self._docs.get(did)
                if old and old.mtime == st.st_mtime and old.size == st.st_size:
                    found[did] = old
                    continue
                try:
                    page = parse_alto(path)
                    found[did] = Doc(did, path, page, st.st_mtime, st.st_size)
                except Exception as exc:
                    found[did] = Doc(
                        did, path, _empty_page(path), st.st_mtime, st.st_size, error=str(exc)
                    )
            self._docs = found
            return list(found.values())

    def get(self, doc_id: str) -> Optional[Doc]:
        with self._lock:
            doc = self._docs.get(doc_id)
        if doc is None:
            self.scan()
            with self._lock:
                doc = self._docs.get(doc_id)
        return doc

    def all(self) -> list[Doc]:
        with self._lock:
            if not self._docs:
                self.scan()
            return list(self._docs.values())


registry = Registry(WORKSPACE)


# --------------------------------------------------------------------------- #
# Results on disk
# --------------------------------------------------------------------------- #
def results_path(doc_id: str) -> Path:
    return RESULTS / f"{doc_id}.json"


def load_results(doc_id: str) -> dict:
    path = results_path(doc_id)
    if not path.is_file():
        return {"lines": {}, "prompt": None, "backend": None, "updated": None}
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {"lines": {}, "prompt": None, "backend": None, "updated": None}
    if not isinstance(data.get("lines"), dict):
        data["lines"] = {}
    return data


def save_results(doc_id: str, data: dict) -> None:
    path = results_path(doc_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Bitmap helpers
# --------------------------------------------------------------------------- #
def _rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("RGB", "L"):
        return im.convert("RGB")
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")


def display_image(doc: Doc) -> tuple[Path, int, int]:
    src = doc.page.image
    if src is None:
        raise HTTPException(404, "该页面没有关联图片")
    w, h = doc.page.image_width or 0, doc.page.image_height or 0
    native = src.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    if native and max(w, h) <= DISPLAY_EDGE:
        return src, w, h

    dest = CACHE / "img" / f"{doc.id}_{int(doc.mtime)}_{DISPLAY_EDGE}.jpg"
    if not dest.is_file() or dest.stat().st_size == 0:
        tmp = dest.with_suffix(".jpg.tmp")
        with Image.open(src) as im:
            im = _rgb(im)
            if max(im.size) > DISPLAY_EDGE:
                scale = DISPLAY_EDGE / float(max(im.size))
                im = im.resize(
                    (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                    Image.LANCZOS,
                )
            im.save(tmp, "JPEG", quality=88, optimize=True)
        tmp.replace(dest)
    with Image.open(dest) as im:
        size = im.size
    return dest, size[0], size[1]


def thumbnail(doc: Doc) -> Path:
    dest = CACHE / "thumb" / f"{doc.id}.jpg"
    if dest.is_file() and dest.stat().st_mtime >= doc.mtime:
        return dest
    src = doc.page.image
    if src is None:
        raise HTTPException(404, "该页面没有关联图片")
    tmp = dest.with_suffix(".jpg.tmp")
    with Image.open(src) as im:
        im = _rgb(im)
        scale = THUMB_WIDTH / float(max(1, im.width))
        im = im.resize((THUMB_WIDTH, max(1, int(im.height * scale))), Image.LANCZOS)
        im.save(tmp, "JPEG", quality=82, optimize=True)
    tmp.replace(dest)
    return dest


# --------------------------------------------------------------------------- #
# Recognition jobs (one GPU -> one worker, FIFO)
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    id: str
    doc_ids: list[str]
    prompt: str
    max_new_tokens: int
    force: bool
    status: str = "queued"
    total: int = 0
    done: int = 0
    message: str = "排队中"
    error: str = ""
    updates: dict[str, dict[str, str]] = field(default_factory=dict)
    started: float = 0.0
    finished: float = 0.0
    backend: str = ""
    cancel: bool = False


recognizer = Recognizer()


def _restore_active_model() -> None:
    """Respect models/.active when no explicit MEDUSA_MODEL_PATH is given."""
    if os.environ.get("MEDUSA_MODEL_PATH"):
        return
    marker = MODELS_DIR / ".active"
    try:
        model_id = marker.read_text("utf-8").strip()
    except OSError:
        return
    entry = MODEL_BY_ID.get(model_id)
    if entry and _model_installed(entry):
        recognizer.set_model_path(str(_model_dir(entry)))


_restore_active_model()
_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_job_queue: "queue.Queue[str]" = queue.Queue()


def _job_snapshot(job: Job) -> dict:
    updates: dict[str, dict[str, str]] = {}
    for doc_id, lines in list(job.updates.items()):
        try:
            updates[doc_id] = dict(lines)
        except Exception:
            updates[doc_id] = {}
    return {
        "id": job.id,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "message": job.message,
        "error": job.error,
        "docIds": list(job.doc_ids),
        "updates": updates,
        "started": job.started,
        "finished": job.finished,
        "backend": job.backend,
        "force": job.force,
        "prompt": job.prompt,
        "maxNewTokens": job.max_new_tokens,
    }


def _jobs_busy() -> bool:
    with _jobs_lock:
        return any(job.status in ("queued", "running") for job in _jobs.values())


def _run_job(job: Job) -> None:
    job.status = "running"
    job.started = time.time()
    job.backend = recognizer.backend
    saved: list[tuple[str, dict]] = []
    try:
        docs: list[Doc] = []
        for doc_id in job.doc_ids:
            doc = registry.get(doc_id)
            if doc is None:
                raise HTTPException(404, f"页面不存在: {doc_id}")
            docs.append(doc)

        plan: list[tuple[Doc, list, dict]] = []
        total = 0
        for doc in docs:
            if doc.error:
                raise HTTPException(400, f"{doc.alto.name}: {doc.error}")
            if not doc.page.lines:
                continue
            if doc.page.image is None:
                raise HTTPException(400, f"{doc.alto.name}: 找不到关联图片")
            res = load_results(doc.id)
            have = res.get("lines", {})
            targets = [
                line
                for line in doc.page.lines
                if job.force or not str((have.get(line.id) or {}).get("text", "")).strip()
            ]
            plan.append((doc, targets, res))
            total += len(targets)

        job.total = total
        if total == 0:
            job.status = "done"
            job.message = "全部行都已有转写（勾选“重跑”可强制识别）"
            job.finished = time.time()
            return

        job.message = "加载模型…"
        recognizer.ensure_loaded(lambda msg: setattr(job, "message", msg))
        job.message = "识别中…"

        for doc, targets, res in plan:
            if job.cancel or not targets:
                continue
            flush_images: list[Image.Image] = []
            flush_lines: list = []
            flush_gts: list[Optional[str]] = []

            def flush() -> None:
                if not flush_images:
                    return
                t0 = time.time()
                texts = recognizer.recognize(
                    flush_images,
                    prompt=job.prompt,
                    ground_truth=flush_gts,
                    max_new_tokens=job.max_new_tokens,
                )
                per = int((time.time() - t0) * 1000 / max(1, len(texts)))
                bucket = res.setdefault("lines", {})
                doc_updates = job.updates.setdefault(doc.id, {})
                for line, text in zip(flush_lines, texts):
                    bucket[line.id] = {"text": text, "ms": per}
                    doc_updates[line.id] = text
                    job.done += 1
                res["prompt"] = job.prompt
                res["backend"] = recognizer.backend
                res["updated"] = time.time()
                res["image"] = str(doc.page.image)
                res["alto"] = str(doc.alto)
                save_results(doc.id, res)
                saved.append((doc.id, res))
                flush_images.clear()
                flush_lines.clear()
                flush_gts.clear()
                job.message = f"识别中… {job.done}/{job.total}"

            with Image.open(doc.page.image) as im:
                im.load()
                iw, ih = im.size
                for line in targets:
                    if job.cancel:
                        break
                    x, y, w, h = line.box
                    pad = max(2, int(round(h * 0.10)))
                    left = max(0, int(x) - pad)
                    top = max(0, int(y) - pad)
                    right = min(iw, int(x + w) + pad)
                    bottom = min(ih, int(y + h) + pad)
                    if right - left < 4 or bottom - top < 4:
                        continue
                    crop = im.crop((left, top, right, bottom)).convert("RGB")
                    if line.polygon and len(line.polygon) >= 3:
                        # keep only the ink inside the ALTO polygon; the rest
                        # becomes paper so neighbouring fragments do not leak in
                        mask = Image.new("L", crop.size, 0)
                        points = [(px - left, py - top) for px, py in line.polygon]
                        draw_mask = ImageDraw.Draw(mask)
                        draw_mask.polygon(points, fill=255)
                        # grow the mask by ~1px so strokes at the polygon edge
                        # are not clipped away
                        draw_mask.line(points + [points[0]], fill=255, width=2, joint="curve")
                        paper = Image.new("RGB", crop.size, (255, 255, 255))
                        crop = Image.composite(crop, paper, mask)
                    flush_images.append(crop)
                    flush_lines.append(line)
                    flush_gts.append(line.gt or None)
                    if len(flush_images) >= recognizer.batch_size:
                        flush()
                flush()

        job.status = "cancelled" if job.cancel else "done"
        job.message = "已取消" if job.cancel else "完成"
    except HTTPException as exc:
        job.status = "error"
        job.error = str(exc.detail)
        job.message = "失败"
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        job.message = "失败"
    finally:
        job.finished = time.time()
        for doc_id, res in saved:
            try:
                save_results(doc_id, res)
            except Exception:
                pass


def _worker() -> None:
    while True:
        job_id = _job_queue.get()
        if job_id is None:
            _job_queue.task_done()
            break
        try:
            with _jobs_lock:
                job = _jobs.get(job_id)
            if job is not None and not job.cancel:
                _run_job(job)
        except Exception:
            pass
        finally:
            _job_queue.task_done()


threading.Thread(target=_worker, daemon=True, name="medusa-worker").start()
threading.Thread(target=recognizer.probe, daemon=True, name="medusa-probe").start()


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
app = FastAPI(title="MEDUSA HTR Workbench")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.middleware("http")
async def _no_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/api/jobs") or path.startswith("/api/backend"):
        response.headers["Cache-Control"] = "no-store"
    return response


class RecognizeBody(BaseModel):
    docIds: Optional[list[str]] = None
    prompt: Optional[str] = None
    maxNewTokens: Optional[int] = None
    force: bool = False


class LineTextBody(BaseModel):
    text: str


class ModelActionBody(BaseModel):
    id: str


def _rel(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.relative_to(WORKSPACE))
    except ValueError:
        return str(path)


def _doc_or_404(doc_id: str) -> Doc:
    doc = registry.get(doc_id)
    if doc is None:
        raise HTTPException(404, "页面不存在")
    return doc


def _doc_status(doc: Doc, res: dict) -> tuple[int, str]:
    lines = doc.page.lines
    done = sum(1 for line in lines if str((res.get("lines", {}).get(line.id) or {}).get("text", "")).strip())
    if doc.error:
        return done, "error"
    if not lines:
        return done, "empty"
    if done >= len(lines):
        return done, "done"
    if done > 0:
        return done, "partial"
    return done, "todo"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/fonts.css")
def fonts_css() -> Response:
    rules: list[str] = []
    for name, path in _font_files().items():
        lower = name.lower()
        fmt = FONT_SUFFIXES[path.suffix.lower()]
        style = "italic" if ("italic" in lower or "oblique" in lower) else "normal"
        weight = "700" if "bold" in lower else "400"
        rules.append(
            "@font-face{font-family:'Junicode Local';src:url('/fonts/%s') format('%s');"
            "font-style:%s;font-weight:%s;font-display:swap;}"
            % (quote(name), fmt, style, weight)
        )
    return Response(
        "\n".join(rules), media_type="text/css", headers={"Cache-Control": "no-cache"}
    )


@app.get("/fonts/{name}")
def font_file(name: str) -> FileResponse:
    safe = Path(name).name
    path = _font_files().get(safe)
    if path is None:
        raise HTTPException(404, "字体不存在")
    return FileResponse(
        path,
        media_type=FONT_MIME.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/api/config")
def config() -> dict:
    return {
        "workspace": str(WORKSPACE),
        "modelsDir": str(MODELS_DIR),
        "defaultPrompt": DEFAULT_PROMPT,
        "promptPresets": PROMPT_PRESETS,
        "displayEdge": DISPLAY_EDGE,
        "fonts": list(_font_files()),
        "backend": recognizer.status(),
    }


@app.get("/api/models")
def list_models() -> dict:
    active_path = Path(recognizer.model_path).resolve()
    models = []
    for entry in MODEL_CATALOG:
        path = _model_dir(entry)
        installed = _model_installed(entry)
        item = {key: entry[key] for key in ("id", "repo", "label", "short", "params", "approxBytes", "loadMode", "note")}
        item.update(
            {
                "path": str(path),
                "installed": installed,
                "size": _dir_size(path) if path.is_dir() else 0,
                "active": path.resolve() == active_path,
            }
        )
        models.append(item)
    active = next((model for model in models if model["active"]), None)
    return {
        "models": models,
        "activeId": active["id"] if active else None,
        "modelsDir": str(MODELS_DIR),
        "diskFree": shutil.disk_usage(MODELS_DIR).free,
        "endpoint": downloader.endpoint,
        "download": downloader.snapshot(),
        "busy": _jobs_busy(),
        "backend": recognizer.status(),
    }


@app.post("/api/models/download")
def start_model_download(body: ModelActionBody) -> dict:
    entry = MODEL_BY_ID.get(body.id)
    if entry is None:
        raise HTTPException(404, "未知模型")
    if _model_installed(entry):
        raise HTTPException(409, "该模型已经下载完成")
    return downloader.start(entry)


@app.post("/api/models/download/cancel")
def cancel_model_download() -> dict:
    return downloader.cancel()


@app.post("/api/models/activate")
def activate_model(body: ModelActionBody) -> dict:
    entry = MODEL_BY_ID.get(body.id)
    if entry is None:
        raise HTTPException(404, "未知模型")
    if not _model_installed(entry):
        raise HTTPException(409, "该模型还没有下载完成")
    if _jobs_busy():
        raise HTTPException(409, "识别任务进行中，完成后再切换模型")
    snapshot = downloader.snapshot()
    if snapshot["state"] in ("starting", "downloading", "cancelling"):
        raise HTTPException(409, "模型正在下载，稍后再切换")
    recognizer.set_model_path(str(_model_dir(entry)))
    try:
        (MODELS_DIR / ".active").write_text(entry["id"], "utf-8")
    except OSError:
        pass
    return {"ok": True, "activeId": entry["id"], "backend": recognizer.status()}


@app.get("/api/backend")
def backend() -> dict:
    return recognizer.status()


@app.get("/health")
def health() -> dict:
    status = recognizer.status()
    return {"ok": True, "backend": status["backend"], "state": status["state"]}


@app.get("/api/docs")
def list_docs() -> dict:
    docs = registry.scan()
    items = []
    for doc in sorted(docs, key=lambda d: str(d.alto).lower()):
        res = load_results(doc.id)
        done, status = _doc_status(doc, res)
        items.append(
            {
                "id": doc.id,
                "name": doc.alto.stem,
                "alto": _rel(doc.alto),
                "image": _rel(doc.page.image),
                "lines": len(doc.page.lines),
                "done": done,
                "status": status,
                "error": doc.error,
                "warnings": doc.page.warnings,
                "thumb": f"/api/docs/{doc.id}/thumb?v={int(doc.mtime)}",
            }
        )
    return {"docs": items, "workspace": str(WORKSPACE), "backend": recognizer.status()}


@app.get("/api/docs/{doc_id}")
def doc_detail(doc_id: str) -> dict:
    doc = _doc_or_404(doc_id)
    if doc.error:
        raise HTTPException(400, doc.error)
    if doc.page.image is None:
        raise HTTPException(400, "该 ALTO 没有关联图片")

    _, disp_w, disp_h = display_image(doc)
    iw = doc.page.image_width or disp_w
    ih = doc.page.image_height or disp_h
    res = load_results(doc_id)
    bucket = res.get("lines", {})
    done, status = _doc_status(doc, res)

    lines = []
    for line in doc.page.lines:
        x, y, w, h = line.box
        entry = bucket.get(line.id) or {}
        lines.append(
            {
                "id": line.id,
                "order": line.order,
                "box": {"x": x / iw, "y": y / ih, "w": w / iw, "h": h / ih},
                "px": {"x": x, "y": y, "w": w, "h": h},
                "text": entry.get("text", ""),
                "ms": entry.get("ms"),
                "gt": line.gt,
                "polygon": [[px / iw, py / ih] for px, py in line.polygon] if line.polygon else None,
            }
        )

    return {
        "id": doc.id,
        "name": doc.alto.stem,
        "alto": _rel(doc.alto),
        "image": _rel(doc.page.image),
        "imageUrl": f"/api/docs/{doc.id}/image?v={int(doc.mtime)}",
        "width": iw,
        "height": ih,
        "displayWidth": disp_w,
        "displayHeight": disp_h,
        "unit": doc.page.unit,
        "pageWidth": doc.page.page_width,
        "pageHeight": doc.page.page_height,
        "lines": lines,
        "done": done,
        "status": status,
        "prompt": res.get("prompt"),
        "backend": res.get("backend"),
        "updated": res.get("updated"),
        "warnings": doc.page.warnings,
    }


@app.get("/api/docs/{doc_id}/image")
def doc_image(doc_id: str) -> FileResponse:
    doc = _doc_or_404(doc_id)
    path, _, _ = display_image(doc)
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/docs/{doc_id}/thumb")
def doc_thumb(doc_id: str) -> FileResponse:
    doc = _doc_or_404(doc_id)
    path = thumbnail(doc)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/docs/{doc_id}/lines/{line_id}/crop")
def line_crop(doc_id: str, line_id: str) -> FileResponse:
    """Enlarged crop for the floating line editor."""
    doc = _doc_or_404(doc_id)
    if doc.page.image is None:
        raise HTTPException(404, "该页面没有关联图片")
    line = next((item for item in doc.page.lines if item.id == line_id), None)
    if line is None:
        raise HTTPException(404, "该行不存在")

    key = hashlib.sha1(
        json.dumps(["v2", doc.mtime, line.id, line.box, line.polygon], ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    dest = CACHE / "line" / f"{doc.id}_{key}.jpg"
    if not dest.is_file() or dest.stat().st_size == 0:
        x, y, w, h = line.box
        pad = max(2, int(round(h * 0.15)))
        left, top = int(x) - pad, int(y) - pad
        right, bottom = int(x + w) + pad, int(y + h) + pad
        if line.polygon:
            xs = [point[0] for point in line.polygon]
            ys = [point[1] for point in line.polygon]
            left = min(left, int(min(xs)) - pad)
            top = min(top, int(min(ys)) - pad)
            right = max(right, int(max(xs)) + pad)
            bottom = max(bottom, int(max(ys)) + pad)

        with Image.open(doc.page.image) as im:
            im.load()
            left, top = max(0, left), max(0, top)
            right, bottom = min(im.width, right), min(im.height, bottom)
            if right - left < 2 or bottom - top < 2:
                raise HTTPException(400, "行区域太小")
            crop = im.crop((left, top, right, bottom)).convert("RGB")
            scale = max(1.0, min(8.0, 1400.0 / max(1, crop.width)))
            if scale > 1.05:
                crop = crop.resize(
                    (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                    Image.LANCZOS,
                )
            tmp = dest.with_suffix(".jpg.tmp")
            crop.save(tmp, "JPEG", quality=92, optimize=True)
            tmp.replace(dest)
    return FileResponse(
        dest, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.patch("/api/docs/{doc_id}/lines/{line_id}")
def update_line(doc_id: str, line_id: str, body: LineTextBody) -> dict:
    doc = _doc_or_404(doc_id)
    if not any(item.id == line_id for item in doc.page.lines):
        raise HTTPException(404, "该行不存在")
    results = load_results(doc_id)
    lines = results.setdefault("lines", {})
    entry = dict(lines.get(line_id) or {})
    entry["text"] = body.text
    entry["edited"] = True
    lines[line_id] = entry
    results["updated"] = time.time()
    save_results(doc_id, results)
    done, status = _doc_status(doc, results)
    return {"ok": True, "text": body.text, "done": done, "status": status}


@app.post("/api/jobs")
def create_job(body: RecognizeBody) -> dict:
    doc_ids = body.docIds or [d.id for d in registry.all()]
    if not doc_ids:
        raise HTTPException(400, "工作区里没有 ALTO 页面")
    for doc_id in doc_ids:
        _doc_or_404(doc_id)
    job = Job(
        id=uuid.uuid4().hex[:12],
        doc_ids=list(doc_ids),
        prompt=(body.prompt or DEFAULT_PROMPT).strip(),
        max_new_tokens=int(body.maxNewTokens or recognizer.max_new_tokens),
        force=bool(body.force),
    )
    with _jobs_lock:
        _jobs[job.id] = job
    _job_queue.put(job.id)
    return _job_snapshot(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _job_snapshot(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    job.cancel = True
    if job.status == "queued":
        job.status = "cancelled"
        job.message = "已取消"
    return _job_snapshot(job)


@app.post("/api/rescan")
def rescan() -> dict:
    docs = registry.scan()
    return {"count": len(docs)}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    folder = WORKSPACE / "uploads" / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for upload_file in files:
        raw_name = Path(upload_file.filename or "file").name
        name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", raw_name).strip() or "file"
        dest = folder / name
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = folder / f"{stem}_{counter}{suffix}"
            counter += 1
        with dest.open("wb") as handle:
            shutil.copyfileobj(upload_file.file, handle)
        saved.append(dest.name)
    registry.scan()
    return {"saved": saved, "folder": _rel(folder)}
