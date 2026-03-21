# MEDPRO — FFmpeg Media Processor

A full-stack media processing system. Submit a video URL, choose an operation (thumbnail, compress, or extract audio), and get results in your browser — powered by **FastAPI** on the backend and **FFmpeg via Python subprocess** for media processing.

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
  └── Serves output via /files/:filename (StaticFiles)
```

---

## Features

- **3 operations**: thumbnail (JPEG frame), compress (H.264 MP4), extract audio (MP3)
- **Async job queue** — non-blocking; poll `/status/:id` for results
- **Live progress UI** — stage labels, progress bar, log console
- **Injection-safe** — all FFmpeg commands built as lists, never shell strings
- **Size & timeout guards** — 500 MB cap, 60s download, 120s FFmpeg timeout
- **Docker Compose** — one-command full-stack launch

---

## Quick Start

### Option A — Docker (Recommended)

```bash
git clone https://github.com/<your-handle>/medpro.git
cd medpro
docker compose up --build
```

- **Frontend** → http://localhost:3000
- **API docs** → http://localhost:8000/docs

### Option B — Run Locally

**Requirements**: Python 3.10+, FFmpeg installed (`brew install ffmpeg` / `apt install ffmpeg`)

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npx serve .        # or: python -m http.server 3000
```

Open http://localhost:3000 in your browser.

---

## API Reference

### `POST /process`

Submit a new media processing job.

**Request**
```json
{
  "url": "https://example.com/video.mp4",
  "operation": "thumbnail"
}
```

| Field       | Type   | Values                          |
|-------------|--------|---------------------------------|
| `url`       | string | Any public HTTP/HTTPS media URL |
| `operation` | string | `thumbnail` · `compress` · `audio` |

**Response (202 Accepted)**
```json
{
  "status": "pending",
  "job_id": "a3f9c1d2e8b04f7a9c3e5d1b7f2a0e8c",
  "operation": "thumbnail"
}
```

---

### `GET /status/{job_id}`

Poll for job status.

**Pending**
```json
{ "status": "pending", "message": "downloading", "job_id": "..." }
```

**Success**
```json
{
  "status": "success",
  "output": "/files/a3f9c1d2...jpg",
  "job_id": "...",
  "operation": "thumbnail"
}
```

**Failure**
```json
{ "status": "error", "message": "FFmpeg failed: ...", "job_id": "..." }
```

---

### `GET /health`

Health-check endpoint (used by Docker healthcheck).

```json
{ "status": "ok", "ffmpeg": "available" }
```

---

### `DELETE /jobs/{job_id}`

Clean up a completed job and its output file.

---

## FFmpeg Commands Used

| Operation | FFmpeg Command |
|-----------|----------------|
| Thumbnail | `ffmpeg -y -i input -ss 00:00:02 -vframes 1 -q:v 2 output.jpg` |
| Compress  | `ffmpeg -y -i input -vcodec libx264 -crf 28 -preset fast -acodec aac -b:a 128k -movflags +faststart output.mp4` |
| Audio     | `ffmpeg -y -i input -vn -acodec libmp3lame -ab 192k -ar 44100 output.mp3` |

All commands are executed via **`subprocess.run(list, ...)`** — no shell interpolation.

---

## Sample Inputs

| URL | Operation | Expected Output |
|-----|-----------|-----------------|
| `https://www.w3schools.com/html/mov_bbb.mp4` | thumbnail | JPEG frame at 2s |
| `https://www.w3schools.com/html/mov_bbb.mp4` | compress  | Smaller MP4 |
| `https://www.w3schools.com/html/mov_bbb.mp4` | audio     | MP3 audio track |

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| Invalid / non-HTTP URL | Pydantic validator rejects before download |
| URL returns non-200 | Download raises `ValueError` with HTTP status |
| HTML page (not media) | Content-type check raises `ValueError` |
| File > 500 MB | Streaming download aborts with size error |
| Invalid media (not a video) | `ffprobe` validation fails before FFmpeg |
| FFmpeg non-zero exit | Captures stderr, returns meaningful message |
| Download timeout (60s) | `httpx` timeout raises; returned to frontend |
| FFmpeg timeout (120s) | `subprocess.TimeoutExpired` caught gracefully |
| Shell injection attempt | URL validated; command built as list, no `shell=True` |

---

## Security Notes

- URLs are validated to start with `http://` or `https://`
- Shell meta-characters (`;`, `|`, `&`, `` ` ``, `$`, etc.) are rejected in URLs
- FFmpeg is always called via **list arguments** (`subprocess.run(cmd_list)`) — `shell=False` by default — preventing command injection
- Temporary files are deleted after processing regardless of success/failure
- File size is capped at 500 MB to prevent resource exhaustion

---

## Project Structure

```
medpro/
├── backend/
│   ├── main.py            # FastAPI app + FFmpeg pipeline
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── temp/              # Downloaded input files (auto-cleaned)
│   └── outputs/           # Processed output files
├── frontend/
│   ├── index.html         # Single-file UI (HTML/CSS/JS)
│   ├── Dockerfile
│   └── nginx.conf
├── docker-compose.yml
└── README.md
```

---

## Assumptions & Design Decisions

1. **Async job queue with polling** — Rather than blocking the HTTP request for potentially 60+ second FFmpeg jobs, jobs are processed in FastAPI background tasks and the client polls `/status/:id` every 2 seconds. For production, this would be replaced with Celery + Redis.

2. **In-memory job store** — Jobs are stored in a Python dict. This is intentionally simple; in production, use Redis or a database for persistence across restarts and multi-worker deployments.

3. **URL-only input** — File uploads were out of scope. Only public HTTP/HTTPS URLs are supported.

4. **No authentication** — This is a demo/assignment build. A production deployment would require API keys or OAuth.

5. **Output file retention** — Files in `outputs/` are not automatically purged. A cron job or TTL-based cleanup task would be added in production.

---

## License

MIT
