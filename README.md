# Video Downloader

A desktop app for downloading video or audio from a web page link.

## Install (macOS)
Requires Python 3.10+ and, for HD video and MP3 conversion, ffmpeg.

```bash
brew install python ffmpeg
git clone https://github.com/shain126/anyVideoDownloader.git && cd anyVideoDownloader
./setup.sh
```

Then double-click **Video Downloader.app**. If you skip `./setup.sh`, the app runs it automatically on first launch, which takes a few minutes and shows a notification.

On other platforms, run `./setup.sh` (or `pip install -r requirements.txt`), then `python app.py`.

## Use
Double-click **Video Downloader.app**, then:
1. Paste a link and click **Find media**.
2. Choose **Video** (quality: Best/1080p/720p…, format: mp4/mkv/webm) or **Audio only** (MP3, M4A, Opus, WAV, FLAC).
3. Click **Download**. Files go to `~/Downloads/Video Downloader` (change it with **Change…**).

## Supported sites
Works with YouTube, Vimeo, Reddit, TikTok, SoundCloud and ~1,800 other sites via [yt-dlp](https://github.com/yt-dlp/yt-dlp), plus any page with an embedded video.

- Videos with no sound can only be downloaded as video.

## Tips
- **Playlists/channels:** tick "Download whole playlist", then **Download all**.
- **Unsupported sites:** the app scans the page for direct media links (`<video>`, `.mp4`, `.m3u8`, `.mp3`…) and lists them.
- **Private or sign-in-only videos:** set "Use login cookies from" to the browser you're signed in with.
- **A site stopped working?** Click **Update engine** and restart the app (sites change often).

## Files
- `app.py`: the backend (yt-dlp, local server, native window)
- `ui.html`: the interface
- `setup.sh`: one-time install (runs automatically if `.venv` is missing)
- Run in a normal browser instead: `.venv/bin/python app.py --browser`
- Logs: `/tmp/video-downloader.log`

Requires Python 3 and ffmpeg (`brew install ffmpeg`) for merging HD video and converting to MP3.

Only download content you have the right to save. DRM-protected streaming services (Netflix, Disney+, Spotify, etc.) aren't supported.

## License
MIT — see [LICENSE](LICENSE).
