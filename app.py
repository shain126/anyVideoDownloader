#!/usr/bin/env python3
"""Video Downloader — a small desktop app for saving video or audio from a web page.

Backend: yt-dlp (supports YouTube, Vimeo, Reddit, TikTok and
~1800 other sites, plus generic pages with <video> tags / HLS / DASH streams).
If yt-dlp cannot handle a page, the app scans the page HTML for direct media links.

UI: an HTML page served on 127.0.0.1 and shown in a native window (pywebview).
Run with --browser to use your normal web browser instead.
"""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yt_dlp

APP_DIR = Path(__file__).resolve().parent
UI_FILE = APP_DIR / "ui.html"
SETTINGS_FILE = Path.home() / ".video_downloader.json"
DEFAULT_FOLDER = Path.home() / "Downloads" / "Video Downloader"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
MEDIA_EXT = r"(?:mp4|m4v|webm|mov|mkv|m3u8|mpd|mp3|m4a|aac|ogg|oga|opus|wav|flac)"


# ---------------------------------------------------------------- settings --

def load_settings():
    s = {"folder": str(DEFAULT_FOLDER), "cookies": "none"}
    try:
        s.update(json.loads(SETTINGS_FILE.read_text()))
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
    except Exception:
        pass


SETTINGS = load_settings()


def find_ffmpeg():
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    return None


FFMPEG = find_ffmpeg()


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "http_headers": {"User-Agent": UA},
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    if SETTINGS.get("cookies") and SETTINGS["cookies"] != "none":
        opts["cookiesfrombrowser"] = (SETTINGS["cookies"],)
    return opts


# -------------------------------------------------------------- inspection --

def fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return ""
        return r.read(4_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")


def scan_page_for_media(url):
    """Fallback: look through raw HTML for direct audio/video URLs."""
    page = fetch_page(url)
    if not page:
        return []
    page = html.unescape(page).replace("\\/", "/")
    found = []
    patterns = [
        r'<(?:video|audio|source)[^>]+src=["\']([^"\']+)["\']',
        r'<meta[^>]+(?:og:video(?::secure_url|:url)?|og:audio)["\'][^>]+content=["\']([^"\']+)["\']',
        r'["\'](https?://[^"\'\s<>]+?\.' + MEDIA_EXT + r'(?:\?[^"\'\s<>]*)?)["\']',
    ]
    for pat in patterns:
        for m in re.finditer(pat, page, re.I):
            u = urllib.parse.urljoin(url, m.group(1).strip())
            if u.startswith("http") and u not in found:
                found.append(u)
    entries = []
    for u in found[:50]:
        name = urllib.parse.unquote(Path(urllib.parse.urlparse(u).path).name) or u
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        kind = "audio" if ext in ("mp3", "m4a", "aac", "ogg", "oga", "opus", "wav", "flac") else "video"
        entries.append({"url": u, "title": name, "kind": kind})
    return entries


def summarize(info):
    heights = sorted({f.get("height") for f in info.get("formats") or []
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    fmts = info.get("formats") or [info]
    # vcodec None means "unknown" (e.g. a direct .mp4 link) — treat as video.
    has_video = bool(heights) or any(f.get("vcodec") != "none" and
                                     f.get("ext") not in ("mp3", "m4a", "opus", "ogg", "wav", "flac", "aac")
                                     for f in fmts)
    return {
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or info.get("extractor_key"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "site": info.get("extractor_key"),
        "heights": heights,
        "has_video": has_video,
        "url": info.get("webpage_url") or info.get("original_url"),
    }


def inspect(url, playlist=False, _depth=0):
    opts = base_opts() | {"skip_download": True, "noplaylist": not playlist,
                          "extract_flat": "in_playlist"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        info = ydl.sanitize_info(info)
        if info.get("_type") == "playlist":
            items, seen = [], set()
            for e in info.get("entries") or []:
                u = e and (e.get("url") or e.get("webpage_url"))
                if u and u not in seen:
                    seen.add(u)
                    items.append({"url": u, "title": e.get("title") or u, "kind": "video"})
            # A "playlist" of one item: inspect that item directly (once, and never
            # the same URL again, so a page that points at itself can't loop).
            if len(items) == 1 and not playlist and _depth < 2 and items[0]["url"] != url:
                return inspect(items[0]["url"], _depth=_depth + 1) | {"url": items[0]["url"]}
            return {"ok": True, "type": "playlist", "title": info.get("title"),
                    "entries": items, "url": url}
        return {"ok": True, "type": "single", **summarize(info)}
    except Exception as e:
        err = clean_error(e)
    # yt-dlp couldn't do it — scan the page ourselves.
    try:
        entries = scan_page_for_media(url)
    except Exception:
        entries = []
    if entries:
        return {"ok": True, "type": "scan", "title": "Media found on page",
                "entries": entries, "url": url, "note": err}
    return {"ok": False, "error": err}


def clean_error(e):
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    return msg.replace("ERROR: ", "").strip()


# --------------------------------------------------------------- downloads --

JOBS = {}
JOBS_LOCK = threading.Lock()


class Cancelled(Exception):
    pass


def update(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def build_opts(job_id, mode, quality, audio_fmt, video_fmt, folder, playlist, referer):
    job = JOBS[job_id]
    tmpl = "%(title).150B [%(id)s].%(ext)s"
    if playlist:
        tmpl = "%(playlist_title|Playlist).80B/%(playlist_index|0)03d - " + tmpl
    opts = base_opts() | {
        "outtmpl": str(Path(folder) / tmpl),
        "noplaylist": not playlist,
        "windowsfilenames": True,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
    }
    if referer:
        opts["http_headers"] = opts["http_headers"] | {"Referer": referer}

    if mode == "audio":
        opts["format"] = "bestaudio/best"
        if FFMPEG and audio_fmt != "original":
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_fmt,
                "preferredquality": "320" if audio_fmt == "mp3" else "0",
            }]
    else:
        h = "" if quality == "best" else f"[height<={quality}]"
        if FFMPEG:
            opts["format"] = f"bv*{h}+ba/b{h}/bv*+ba/b"
            opts["merge_output_format"] = video_fmt
            if video_fmt == "mp4":
                # Prefer H.264/AAC so the file plays in QuickTime & everywhere.
                opts["format_sort"] = ["res", "vcodec:h264", "acodec:m4a"] if quality == "best" \
                    else [f"res:{quality}", "vcodec:h264", "acodec:m4a"]
        else:
            opts["format"] = f"b{h}/b"  # no ffmpeg: only pre-merged files

    def progress(d):
        # Remember every file this job writes so a cancel can clean them all up.
        for k in ("filename", "tmpfilename"):
            if d.get(k):
                job["temp"].add(d[k])
        if job.get("cancel"):
            raise Cancelled()
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            frag = (d.get("fragment_index"), d.get("fragment_count"))
            pct = done / total * 100 if total else (frag[0] / frag[1] * 100 if all(frag) else None)
            if pct is not None:
                pct = min(pct, 99.9)  # size estimates can undershoot
            info = d.get("info_dict") or {}
            update(job_id, status="downloading", percent=pct, speed=d.get("speed"),
                   eta=d.get("eta"), downloaded=done, total=total,
                   title=info.get("title") or job["title"],
                   item=(info.get("playlist_index"), info.get("n_entries")))
        elif d["status"] == "finished":
            update(job_id, file=d.get("filename"))

    def pp_hook(d):
        if job.get("cancel"):
            raise Cancelled()
        if d["status"] == "started":
            update(job_id, status="processing")
        elif d["status"] == "finished":
            fp = (d.get("info_dict") or {}).get("filepath")
            if fp:
                update(job_id, file=fp)

    opts["progress_hooks"] = [progress]
    opts["postprocessor_hooks"] = [pp_hook]
    return opts


def discard(job_id):
    """Mark a job cancelled and delete its partial and finished files."""
    job = JOBS[job_id]
    paths = set(job["temp"]) | ({job["file"]} if job.get("file") else set())
    for fp in paths:
        # In a playlist, keep items that already finished; only drop partial files.
        keep_finished = job.get("playlist") and not fp.endswith(".part")
        for f in ((fp + ".part", fp + ".ytdl") if keep_finished else (fp, fp + ".part", fp + ".ytdl")):
            try:
                os.remove(f)
            except OSError:
                pass
    update(job_id, status="cancelled", file=None, speed=None, eta=None)


def run_job(job_id, url, **kw):
    try:
        Path(kw["folder"]).mkdir(parents=True, exist_ok=True)
        opts = build_opts(job_id, **kw)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and info.get("_type") != "playlist":
                fp = (info.get("requested_downloads") or [{}])[0].get("filepath")
                if fp:
                    update(job_id, file=fp)
        if JOBS[job_id].get("cancel"):
            # Cancel arrived after the last hook ran — discard the result.
            discard(job_id)
            return
        update(job_id, status="done", percent=100, speed=None, eta=None)
    except Exception as e:
        if isinstance(e, Cancelled) or JOBS[job_id].get("cancel"):
            discard(job_id)
        else:
            update(job_id, status="error", error=clean_error(e), speed=None, eta=None)


def start_job(p):
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "url": p["url"], "title": p.get("title") or p["url"],
                        "mode": p.get("mode", "video"), "status": "queued", "percent": 0,
                        "file": None, "error": None, "cancel": False, "temp": set(),
                        "playlist": bool(p.get("playlist"))}
    kw = dict(mode=p.get("mode", "video"), quality=str(p.get("quality", "best")),
              audio_fmt=p.get("audio_fmt", "mp3"), video_fmt=p.get("video_fmt", "mp4"),
              folder=SETTINGS["folder"], playlist=bool(p.get("playlist")),
              referer=p.get("referer"))
    threading.Thread(target=run_job, args=(job_id, p["url"]), kwargs=kw, daemon=True).start()
    return job_id


# ------------------------------------------------------------------ server --

def choose_folder():
    if sys.platform != "darwin":
        return None
    # The current folder is passed as an argument, not pasted into the script,
    # so quotes or backslashes in the path can't break the AppleScript.
    script = """on run argv
        set p to "Save downloads to:"
        try
            return POSIX path of (choose folder with prompt p default location (POSIX file (item 1 of argv)))
        on error number n
            if n is -128 then return ""
            return POSIX path of (choose folder with prompt p)
        end try
    end run"""
    r = subprocess.run(["osascript", "-e", script, SETTINGS["folder"]],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def reveal(path):
    if sys.platform == "darwin":
        if path and os.path.exists(path):
            subprocess.run(["open", "-R", path])
        else:
            subprocess.run(["open", SETTINGS["folder"]])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = UI_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = [{k: v for k, v in j.items() if k not in ("cancel", "temp")} for j in JOBS.values()]
            self.send_json({"jobs": jobs})
        elif self.path == "/api/settings":
            self.send_json(SETTINGS | {"ffmpeg": bool(FFMPEG),
                                       "version": yt_dlp.version.__version__})
        else:
            self.send_error(404)

    def do_POST(self):
        # Only accept requests from our own page.
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin):
            return self.send_json({"ok": False, "error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        p = json.loads(self.rfile.read(n) or b"{}")
        route = self.path
        if route == "/api/inspect":
            url = (p.get("url") or "").strip()
            if not re.match(r"^https?://", url):
                url = "https://" + url
            self.send_json(inspect(url, playlist=bool(p.get("playlist"))))
        elif route == "/api/download":
            self.send_json({"ok": True, "id": start_job(p)})
        elif route == "/api/cancel":
            with JOBS_LOCK:
                if p.get("id") in JOBS:
                    JOBS[p["id"]]["cancel"] = True
            self.send_json({"ok": True})
        elif route == "/api/clear":
            with JOBS_LOCK:
                for k in [k for k, j in JOBS.items()
                          if j["status"] in ("done", "error", "cancelled")]:
                    del JOBS[k]
            self.send_json({"ok": True})
        elif route == "/api/reveal":
            reveal(p.get("path"))
            self.send_json({"ok": True})
        elif route == "/api/choose-folder":
            f = choose_folder()
            if f:
                SETTINGS["folder"] = f.rstrip("/")
                save_settings(SETTINGS)
            self.send_json({"ok": True, "folder": SETTINGS["folder"]})
        elif route == "/api/settings":
            if p.get("cookies") in ("none", "chrome", "safari", "firefox", "edge", "brave"):
                SETTINGS["cookies"] = p["cookies"]
            save_settings(SETTINGS)
            self.send_json({"ok": True})
        elif route == "/api/update":
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "-q",
                                "yt-dlp[default]"], capture_output=True, text=True)
            self.send_json({"ok": r.returncode == 0,
                            "message": "Updated — restart the app to use the new version."
                            if r.returncode == 0 else r.stderr[-400:]})
        else:
            self.send_error(404)


def active_jobs():
    with JOBS_LOCK:
        return [j for j in JOBS.values() if j["status"] in ("queued", "downloading", "processing")]


def notify(text):
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", "on run argv\ndisplay notification (item 1 of argv) "
                        'with title "Video Downloader"\nend run', text])


def finish_in_background():
    """The window was closed: let running downloads finish instead of killing them."""
    n = len(active_jobs())
    if not n:
        return
    notify(f"Finishing {n} download{'s' if n > 1 else ''} in the background…")
    while active_jobs():
        threading.Event().wait(1)
    with JOBS_LOCK:
        failed = sum(j["status"] == "error" for j in JOBS.values())
    notify("Downloads finished." + (f" {failed} failed." if failed else ""))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Video Downloader running at", url, flush=True)

    if "--browser" not in sys.argv and "--server" not in sys.argv:
        try:
            import webview
            webview.create_window("Video Downloader", url, width=920, height=760,
                                  min_size=(560, 560))
            webview.start()
            finish_in_background()
            return
        except Exception as e:
            print("Native window unavailable, opening browser:", e)
    if "--server" not in sys.argv:
        import webbrowser
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
