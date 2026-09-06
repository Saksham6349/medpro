import os
import uuid
import asyncio
import subprocess
import tempfile
import logging
import re
import json
from pathlib import Path
from typing import Optional, Any

import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("media-processor")

# ── Directories ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "outputs"
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Config ──────────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB
DOWNLOAD_TIMEOUT = 60          # seconds for HTTP download
FFMPEG_TIMEOUT = 120           # seconds for FFmpeg subprocess
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v", ".wmv"}

# ── App ─────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Media Processor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve processed output files
app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")

# Frontend HTML path — works both locally and in Docker/Railway
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = BASE_DIR / "../frontend"

# ── Models ──────────────────────────────────────────────────────────────────────
class ProcessRequest(BaseModel):
    url: str
    operation: str   # thumbnail | compress | audio | analyze

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        # Basic injection guard – no shell meta-characters in URL
        if any(c in v for c in [";", "|", "&", "`", "$", "(", ")", "<", ">"]):
            raise ValueError("URL contains invalid characters")
        return v

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, v: str) -> str:
        allowed = {"thumbnail", "compress", "audio", "analyze"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"Operation must be one of: {allowed}")
        return v

class ProcessResponse(BaseModel):
    status: str
    output: Optional[str] = None
    message: Optional[str] = None
    job_id: Optional[str] = None
    operation: Optional[str] = None
    analysis: Optional[dict[str, Any]] = None

# ── In-memory job store (replace with Redis / DB for production) ─────────────────
jobs: dict[str, dict] = {}

# ── Helpers ─────────────────────────────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    """Remove anything that is not alphanumeric, dash, dot, or underscore."""
    return re.sub(r"[^\w.\-]", "_", name)

def cleanup_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass

async def download_media(url: str, dest: Path) -> None:
    """Download media to *dest*, enforcing size limit and timeout."""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code} when fetching URL")

            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type and "video" not in content_type:
                raise ValueError("URL does not appear to point to a media file")

            total = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_FILE_SIZE_BYTES:
                        raise ValueError(
                            f"File exceeds maximum allowed size ({MAX_FILE_SIZE_BYTES // (1024*1024)} MB)"
                        )
                    f.write(chunk)

    if total == 0:
        raise ValueError("Downloaded file is empty")

def run_ffmpeg(cmd: list[str], job_id: str) -> subprocess.CompletedProcess:
    """
    Run FFmpeg via subprocess with a hard timeout.
    Uses a list (not a shell string) to prevent injection.
    """
    logger.info("FFmpeg cmd [%s]: %s", job_id, " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,   # hard wall-clock timeout
    )
    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr[-2000:])
        raise RuntimeError(f"FFmpeg failed (rc={result.returncode}): {result.stderr[-500:]}")
    return result

def build_ffmpeg_cmd(operation: str, input_path: str, output_path: str) -> list[str]:
    """Return a safe FFmpeg command list (no shell interpolation)."""
    base = ["ffmpeg", "-y", "-i", input_path]

    if operation == "thumbnail":
        return base + [
            "-ss", "00:00:02",
            "-vframes", "1",
            "-q:v", "2",
            output_path,
        ]

    if operation == "compress":
        return base + [
            "-vcodec", "libx264",
            "-crf", "28",          # lower quality → smaller file
            "-preset", "fast",
            "-acodec", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

    if operation == "audio":
        return base + [
            "-vn",                 # no video
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "44100",
            output_path,
        ]

    raise ValueError(f"Unknown operation: {operation}")


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_fps(rate: str) -> Optional[float]:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        num_f = _safe_float(num)
        den_f = _safe_float(den)
        if not num_f or not den_f:
            return None
        return round(num_f / den_f, 3)
    value = _safe_float(rate)
    return round(value, 3) if value else None


def extract_media_metadata(input_path: Path) -> dict[str, Any]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(input_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if probe.returncode != 0:
        raise RuntimeError("Failed to inspect media metadata")

    payload = json.loads(probe.stdout or "{}")
    streams = payload.get("streams", [])
    fmt = payload.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return {
        "container": fmt.get("format_name"),
        "duration_seconds": _safe_float(fmt.get("duration")),
        "size_bytes": _safe_int(fmt.get("size")),
        "bitrate_kbps": round((_safe_float(fmt.get("bit_rate")) or 0) / 1000, 2) if fmt.get("bit_rate") else None,
        "video": {
            "codec": video_stream.get("codec_name") if video_stream else None,
            "width": _safe_int(video_stream.get("width")) if video_stream else None,
            "height": _safe_int(video_stream.get("height")) if video_stream else None,
            "fps": _parse_fps(video_stream.get("r_frame_rate", "")) if video_stream else None,
        },
        "audio": {
            "codec": audio_stream.get("codec_name") if audio_stream else None,
            "channels": _safe_int(audio_stream.get("channels")) if audio_stream else None,
            "sample_rate_hz": _safe_int(audio_stream.get("sample_rate")) if audio_stream else None,
        },
    }

async def process_job(job_id: str, url: str, operation: str) -> None:
    """Full processing pipeline – runs in a background task."""
    input_path = TEMP_DIR / f"{job_id}_input"
    ext_map = {"thumbnail": ".jpg", "compress": ".mp4", "audio": ".mp3"}
    output_filename = f"{job_id}{ext_map[operation]}" if operation in ext_map else None
    output_path = OUTPUT_DIR / output_filename if output_filename else None

    try:
        # 1 – Download
        jobs[job_id]["stage"] = "downloading"
        logger.info("[%s] Downloading %s", job_id, url)
        await download_media(url, input_path)

        # 2 – Probe for video duration (validates it's a real media file)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "default=nw=1:nk=1", str(input_path)],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            raise RuntimeError("File does not appear to be valid media (ffprobe failed)")

        if operation == "analyze":
            jobs[job_id]["stage"] = "processing"
            analysis = extract_media_metadata(input_path)
            jobs[job_id].update({
                "status": "done",
                "stage": "complete",
                "operation": operation,
                "analysis": analysis,
            })
            logger.info("[%s] Analysis complete", job_id)
            return

        # 3 – Process
        jobs[job_id]["stage"] = "processing"
        cmd = build_ffmpeg_cmd(operation, str(input_path), str(output_path))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: run_ffmpeg(cmd, job_id))

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced no output")

        # 4 – Done
        jobs[job_id].update({
            "status": "done",
            "stage": "complete",
            "output_url": f"/files/{output_filename}",
            "operation": operation,
        })
        logger.info("[%s] Done → %s", job_id, output_filename)

    except subprocess.TimeoutExpired:
        jobs[job_id].update({"status": "error", "message": "Processing timed out"})
    except Exception as exc:
        logger.error("[%s] Error: %s", job_id, exc)
        jobs[job_id].update({"status": "error", "message": str(exc)})
    finally:
        cleanup_file(str(input_path))


# ── Routes ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "ffmpeg": "available"}

@app.get("/")
async def serve_frontend():
    """Serve the frontend HTML — so one server does everything."""
    html_path = FRONTEND_DIR / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return {"message": "Frontend not found. Place index.html in ../frontend/"}

@app.post("/process", response_model=ProcessResponse)
async def start_process(req: ProcessRequest, background_tasks: BackgroundTasks):
    """
    Accepts a media URL + operation, queues an async job, and returns a job_id
    immediately so the frontend can poll for status.
    """
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "status": "pending",
        "stage": "queued",
        "operation": req.operation,
    }
    background_tasks.add_task(process_job, job_id, req.url, req.operation)
    return ProcessResponse(
        status="pending",
        job_id=job_id,
        operation=req.operation,
    )

@app.get("/status/{job_id}", response_model=ProcessResponse)
async def get_status(job_id: str):
    """Poll endpoint – frontend calls this until status is 'done' or 'error'."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    if job["status"] == "done":
        return ProcessResponse(
            status="success",
            output=job.get("output_url"),
            job_id=job_id,
            operation=job.get("operation"),
            analysis=job.get("analysis"),
        )
    if job["status"] == "error":
        return ProcessResponse(
            status="error",
            message=job.get("message", "Unknown error"),
            job_id=job_id,
        )
    # still running
    return ProcessResponse(
        status="pending",
        message=job.get("stage", "processing"),
        job_id=job_id,
    )

@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Clean up a completed job and its output file."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs.pop(job_id)
    if "output_url" in job:
        filename = job["output_url"].split("/")[-1]
        cleanup_file(str(OUTPUT_DIR / filename))
    return {"deleted": job_id}
