# MEDPRO — FFmpeg Media Processor

A full-stack media processing web app. Paste a video URL, choose an operation, and get results instantly — powered by **FastAPI + FFmpeg** on the backend.

🚀 **Live Demo**: https://medpro-production.up.railway.app

---

## Features

- 🖼 **Thumbnail** — Extract a JPEG frame at 2 seconds
- 📦 **Compress** — Reduce video size using H.264 encoding
- 🎵 **Extract Audio** — Export MP3 audio at 192kbps
- ⚡ Async job queue — non-blocking processing with live progress UI
- 🛡 Injection-safe — FFmpeg called via subprocess list args, never shell strings
- 🐳 Docker ready — one command full-stack launch

---

## Architecture

```
Browser (HTML/JS)
      │
      │  POST /process  →  job_id
      │  GET  /status/:id  (polled every 2s)
      ▼
FastAPI Backend (Python)
  ├── Background task downloads media (httpx)
  ├── subprocess.run(["ffmpeg", ...])   ← mandatory subprocess usage
  └── Serves output via /files/:filename
```

---

## Quick Start

### Option A — Docker (Recommended)

```bash
git clone https://github.com/Saksham6349/medpro.git
cd medpro
docker compose up --build
```

Visit → http://localhost:8000

### Option B — Run Locally

**Requirements**: Python 3.10+, FFmpeg installed

```bash
# Install FFmpeg
winget install ffmpeg          # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Ubuntu/Debian

# Run backend (serves frontend too)
cd backend
pip install -r requirements.txt
python start.py
```

Visit → http://localhost:8000

---

## API Reference

### `POST /process`

**Request**
```json
{
  "url": "https://example.com/video.mp4",
  "operation": "thumbnail"
}
```

| Field | Type | Values |
|-------|------|--------|
| `url` | string | Any public HTTP/HTTPS media URL |
| `operation` | string | `thumbnail` · `compress` · `audio` |

**Response**
```json
{
  "status": "pending",
  "job_id": "a3f9c1d2e8b04f7a9c3e5d1b7f2a0e8c",
  "operation": "thumbnail"
}
```

---

### `GET /status/{job_id}`

**Success**
```json
{
  "status": "success",
  "output": "/files/a3f9c1d2...jpg",
  "job_id": "...",
  "operation": "thumbnail"
}
```

**Error**
```json
{ "status": "error", "message": "FFmpeg failed: ...", "job_id": "..." }
```

---

### `GET /health`

```json
{ "status": "ok", "ffmpeg": "available" }
```

---

## FFmpeg Commands Used

| Operation | Command |
|-----------|---------|
| Thumbnail | `ffmpeg -y -i input -ss 00:00:02 -vframes 1 -q:v 2 output.jpg` |
| Compress | `ffmpeg -y -i input -vcodec libx264 -crf 28 -preset fast -acodec aac -b:a 128k output.mp4` |
| Audio | `ffmpeg -y -i input -vn -acodec libmp3lame -ab 192k -ar 44100 output.mp3` |

All commands use **`subprocess.run(list, ...)`** — no shell interpolation, no injection risk.

---

## Sample Test URLs

```
https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4
https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4
https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ElephantsDream.mp4
```

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| Invalid URL | Pydantic validator rejects before download |
| HTTP error | Returns status code in error message |
| Not a media file | ffprobe validation fails before FFmpeg |
| File > 500 MB | Streaming download aborts |
| FFmpeg failure | Captures stderr, returns meaningful message |
| Download timeout (60s) | httpx timeout caught gracefully |
| FFmpeg timeout (120s) | subprocess.TimeoutExpired caught |
| Shell injection | URL validated; command built as list |

---

## Security

- URLs validated to start with `http://` or `https://`
- Shell metacharacters rejected in URLs
- FFmpeg always called via list args (`shell=False`)
- Temp files deleted after processing regardless of outcome
- File size capped at 500 MB

---

## Project Structure

```
medpro/
├── backend/
│   ├── main.py            # FastAPI app + FFmpeg pipeline
│   ├── start.py           # Entry point (reads PORT from env)
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── temp/              # Downloaded inputs (auto-cleaned)
│   └── outputs/           # Processed output files
├── frontend/
│   ├── index.html         # Single-file UI (HTML/CSS/JS)
│   ├── Dockerfile
│   └── nginx.conf
├── Dockerfile             # Unified single-service Dockerfile
├── docker-compose.yml
├── railway.toml           # Railway deployment config
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, FastAPI, uvicorn |
| Media Processing | FFmpeg via subprocess |
| Frontend | HTML, CSS, JavaScript |
| Containerization | Docker, Docker Compose |
| Deployment | Railway |
| HTTP Client | httpx (async streaming) |

---

## Assumptions

1. **Async polling** — Jobs run in background tasks; client polls `/status/:id` every 2s. For production, replace with Celery + Redis.
2. **In-memory job store** — Jobs stored in a Python dict. Use Redis/DB for multi-worker production.
3. **URL-only input** — Only public HTTP/HTTPS URLs supported.
4. **No auth** — Demo build; production would need API keys or OAuth.

---

## License

MIT
