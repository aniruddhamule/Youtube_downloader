# downloader.py
import os, re, uuid, threading, time, shutil
from typing import Dict, Optional, List
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Fallback local storage (used if OS Downloads isn't available or user gives a relative path)
STORAGE_DIR = os.path.join(BASE_DIR, "storage")

# Optional cookies support (drop a Netscape cookies file here)
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")
COOKIES_FILE = os.path.join(COOKIES_DIR, "cookies.txt")

# In-memory job registry (single-process only)
JOBS: Dict[str, Dict] = {}

# --------------------- Paths & Defaults ---------------------

def user_downloads_dir() -> str:
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    try:
        os.makedirs(dl, exist_ok=True)
    except Exception:
        dl = STORAGE_DIR
        os.makedirs(dl, exist_ok=True)
    return dl

DEFAULT_DL = user_downloads_dir()
DEFAULT_VID_BUCKET = os.path.join(DEFAULT_DL, "Yt_videos")
DEFAULT_AUD_BUCKET = os.path.join(DEFAULT_DL, "Yt_audios")

def ensure_default_buckets():
    for p in (DEFAULT_VID_BUCKET, DEFAULT_AUD_BUCKET, STORAGE_DIR, COOKIES_DIR):
        try:
            os.makedirs(p, exist_ok=True)
        except Exception:
            pass

ensure_default_buckets()

def _resolve_root_dir(user_dir: Optional[str], media_type: str) -> str:
    if user_dir:
        user_dir = os.path.expanduser(user_dir)
        root = user_dir if os.path.isabs(user_dir) else os.path.join(STORAGE_DIR, user_dir)
    else:
        root = DEFAULT_VID_BUCKET if media_type == "video" else DEFAULT_AUD_BUCKET
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        root = STORAGE_DIR
        os.makedirs(root, exist_ok=True)
    return root

# --------------------- Helpers ---------------------

def safe_folder(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Untitled"

def _list_heights_from_info(info: Dict) -> List[int]:
    heights = set()
    for f in (info or {}).get("formats", []):
        vcodec = f.get("vcodec")
        h = f.get("height")
        if vcodec and vcodec != "none" and isinstance(h, int):
            heights.add(h)
    return sorted(heights, reverse=True)

def strip_ansi(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\x1b\[[0-9;]*m", "", s)

def humanize_bytes(n: float) -> str:
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if n < 1024.0 or unit == "TiB":
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} B"

def humanize_bps(v: Optional[float], fallback: Optional[str]) -> str:
    if isinstance(v, (int, float)) and v > 0:
        return f"{humanize_bytes(float(v))}/s"
    fs = strip_ansi(fallback)
    return fs

def humanize_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    try:
        s = int(max(0, sec))
    except Exception:
        return strip_ansi(str(sec))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h:
        return f"{h}h {m}m {ss}s"
    if m:
        return f"{m}m {ss}s"
    return f"{ss}s"

# -------- Quiet logger to suppress yt-dlp warnings in server console --------
class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

def _yt_opts(base: Dict) -> Dict:
    """Attach shared options + cookies if present."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        **base,
    }
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    opts.setdefault("http_headers", {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
    })
    return opts

# --------------------- Probe ---------------------

def _probe_video_heights(url: str) -> List[int]:
    try:
        with YoutubeDL(_yt_opts({"skip_download": True})) as ydl:
            vi = ydl.extract_info(url, download=False)
        return _list_heights_from_info(vi)
    except Exception:
        return []

def probe_url_meta(url: str) -> Dict:
    """
    Fast probe (works for unlisted when cookies.txt present).
    - Playlists: extract_flat='in_playlist' (fast, no per-item resolves).
    - Singles: read formats for available heights.
    """
    try:
        with YoutubeDL(_yt_opts({
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
        })) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        raise DownloadError("Video or playlist unavailable") from e

    kind = "playlist" if ("entries" in info and info.get("entries")) else "video"
    title = info.get("title") or "Untitled"
    thumb = info.get("thumbnail")

    if kind == "video":
        heights = _list_heights_from_info(info) or _probe_video_heights(info.get("webpage_url") or url)
        default_h = heights[0] if heights else None
        return {
            "kind": "video",
            "title": title,
            "availableHeights": heights,
            "defaultHeight": default_h,
            "canAudio": True,
            "thumbnail": thumb,
        }

    # playlist
    entries = []
    for i, e in enumerate(info.get("entries") or [], start=1):
        if not e:
            continue
        e_url = e.get("url") or e.get("webpage_url")
        e_title = e.get("title") or f"Item {i}"
        e_id = e.get("id") or ""
        entries.append({
            "index": i,
            "id": e_id,
            "title": e_title,
            "url": e_url,
            "duration": e.get("duration") or e.get("duration_string"),
            "thumbnail": None,
        })

    heights: List[int] = []
    if entries:
        heights = _probe_video_heights(entries[0]["url"])
    default_h = heights[0] if heights else None
    return {
        "kind": "playlist",
        "title": title,
        "availableHeights": heights,
        "defaultHeight": default_h,
        "canAudio": True,
        "thumbnail": thumb,
        "entries": entries,
    }

# --------------------- Download engine ---------------------

def _format_string(media_type: str, height: Optional[int]) -> str:
    if media_type == "audio":
        return "bestaudio/best"
    if height is None:
        return "bv*+ba/b"
    return f"bv*[height={height}]+ba/b[height={height}]"

def _progress_hook(job_id: str):
    def hook(d: Dict):
        job = JOBS.get(job_id)
        if not job:
            return
        status = d.get("status")
        if status == "downloading":
            job["status"] = "running"
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                job["progress"] = float(done) / float(total) * 100.0
            job["percent"] = f"{job.get('progress', 0.0):.1f}%"
            job["speed"] = humanize_bps(d.get("speed"), d.get("_speed_str"))
            job["eta"] = humanize_seconds(d.get("eta")) or strip_ansi(d.get("_eta_str"))
        elif status == "finished":
            job["message"] = f"Processing {os.path.basename(d.get('filename',''))}…"
        if job.get("_cancel"):
            # Force-cancel; will be caught in worker
            raise KeyboardInterrupt("Canceled by user")
    return hook

def _try_download_one(url: str, media_type: str, height: Optional[int], postprocessors, job_id: str, work_dir: str):
    fmt = _format_string(media_type, height)
    ydl_opts = _yt_opts({
        "format": fmt,
        "outtmpl": os.path.join(work_dir, "%(title)s.%(ext)s"),
        "noprogress": False,
        "progress_hooks": [_progress_hook(job_id)],
    })
    if postprocessors:
        ydl_opts["postprocessors"] = postprocessors
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return
    except DownloadError as e:
        if "Requested format is not available" not in str(e):
            raise

    # Fallback to Best
    best_fmt = _format_string(media_type, None)
    ydl_opts_fallback = _yt_opts({
        "format": best_fmt,
        "outtmpl": os.path.join(work_dir, "%(title)s.%(ext)s"),
        "noprogress": False,
        "progress_hooks": [_progress_hook(job_id)],
    })
    if postprocessors:
        ydl_opts_fallback["postprocessors"] = postprocessors
    with YoutubeDL(ydl_opts_fallback) as ydl:
        ydl.download([url])

def _unique_dst(dst_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dst_dir, filename)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dst_dir, f"{base} ({n}){ext}")
        n += 1
    return candidate

def _move_completed_to_final(work_dir: str, final_dir: str):
    """
    Move finished files from work_dir → final_dir.
    Skips temporary parts (.part/.ytdl) and then removes the temp folder.
    """
    if not work_dir or not final_dir:
        return
    if not os.path.isdir(work_dir):
        return
    os.makedirs(final_dir, exist_ok=True)
    for root, _, files in os.walk(work_dir):
        for f in files:
            if f.endswith(".part") or f.endswith(".ytdl"):
                continue
            src = os.path.join(root, f)
            dst = _unique_dst(final_dir, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.move(src, dst)
            except Exception:
                shutil.copy2(src, dst)
                try:
                    os.remove(src)
                except Exception:
                    pass
    # Clean entire temp tree (removes .part leftovers)
    shutil.rmtree(work_dir, ignore_errors=True)

def _download_urls(job_id: str, urls: List[str], media_type: str, height: Optional[int], work_dir: str):
    job = JOBS[job_id]
    os.makedirs(work_dir, exist_ok=True)

    postprocessors = None
    if media_type == "audio":
        abitrate = job.get("audioBitrate")
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
        if abitrate and abitrate != "best":
            pp["preferredquality"] = abitrate  # e.g., '192'
        postprocessors = [pp]

    total = len(urls)
    job["totalItems"] = total
    for i, u in enumerate(urls, start=1):
        job["currentItem"] = i
        job["message"] = f"Downloading item {i}/{total}…"
        try:
            with YoutubeDL(_yt_opts({"skip_download": True})) as ydl:
                vi = ydl.extract_info(u, download=False)
            job["currentTitle"] = vi.get("title") or ""
        except Exception:
            job["currentTitle"] = ""
        _try_download_one(u, media_type, height, postprocessors, job_id, work_dir)
        if job.get("_cancel"):
            break

def _download_worker(job_id: str):
    job = JOBS[job_id]
    url = job["url"]
    media_type = job["mediaType"]
    height = job.get("videoHeight")
    root_dir = job.get("rootDir", STORAGE_DIR)

    final_dir = None
    work_dir = None
    try:
        meta = probe_url_meta(url)
        job["kind"] = meta["kind"]
        job["title"] = meta["title"]
        job["status"] = "running"
        job["message"] = "Starting…"

        # Final destination in chosen bucket. Playlist gets a subfolder with playlist title.
        if meta["kind"] == "playlist":
            final_dir = os.path.join(root_dir, safe_folder(meta["title"] or "Playlist"))
        else:
            final_dir = root_dir
        os.makedirs(final_dir, exist_ok=True)
        job["finalDir"] = final_dir

        # Work directory for temporary downloads
        work_dir = os.path.join(root_dir, "_tmp", job_id)
        os.makedirs(work_dir, exist_ok=True)

        # Choose URLs
        selected_urls: List[str] = job.get("selectedUrls") or []
        urls = selected_urls if (meta["kind"] == "playlist" and selected_urls) else [url]

        _download_urls(job_id, urls, media_type, height, work_dir)

        # Normal completion
        job["status"] = "done" if not job.get("_cancel") else "canceled"
        job["message"] = "Completed" if job["status"] == "done" else "Canceled"
        if job["status"] == "done":
            job["progress"] = 100.0
            job["percent"] = "100%"
        job["eta"] = ""
        job["speed"] = ""
    except KeyboardInterrupt:
        # Cancelled during an item — we'll still move finished files in finally
        job["status"] = "canceled"
        job["message"] = "Canceled"
    except DownloadError as e:
        job["status"] = "error"
        job["message"] = f"DownloadError: {e}"
    except Exception as e:
        job["status"] = "error"
        job["message"] = f"Error: {e}"
    finally:
        # ALWAYS move any completed files (no .part) to the final destination
        try:
            _move_completed_to_final(work_dir, final_dir)
        except Exception:
            pass
        # If canceled, make it explicit that completed items were saved
        if job.get("status") == "canceled":
            job["message"] = "Canceled — completed items saved to destination."

def create_job(url: str, media_type: str, video_height: Optional[int],
               audio_bitrate: Optional[str], selected_urls: Optional[List[str]] = None,
               output_dir: Optional[str] = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    root_dir = _resolve_root_dir(output_dir, media_type)
    JOBS[job_id] = {
        "jobId": job_id,
        "url": url,
        "mediaType": media_type,              # 'video' | 'audio'
        "videoHeight": video_height,          # int | None
        "audioBitrate": audio_bitrate or "best",
        "selectedUrls": list(selected_urls or []),
        "rootDir": root_dir,                  # chosen bucket (Videos or Audios by default)
        "finalDir": None,
        "status": "queued",
        "progress": 0.0,
        "percent": "0%",
        "eta": "",
        "speed": "",
        "message": "",
        "_cancel": False,
        "kind": None,
        "title": "",
        "currentItem": 0,
        "totalItems": 0,
        "currentTitle": "",
        "created": int(time.time()),
    }
    t = threading.Thread(target=_download_worker, args=(job_id,), daemon=True)
    t.start()
    return job_id

def cancel_job(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if not job:
        return False
    job["_cancel"] = True
    job["message"] = "Cancel requested…"
    return True

def get_job(job_id: str) -> Optional[Dict]:
    return JOBS.get(job_id)
