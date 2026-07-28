import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.schemas import CreateVideoRequest, CreateVideoResponse, JobDetail, JobSummary
from app.cleanup import purge_job
from app.documents.docx import DocxExtractError, extract_steps
from app.domain.models import Job, JobStatus
from app.llm.client import (
    GuardMisconfiguredError,
    GuardUnavailableError,
    NormalizerMisconfiguredError,
    NormalizerUnavailableError,
    ScriptNormalizer,
    SubjectGuard,
)
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.subjects import get_subject_config
from app.worker.queue import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["videos"])

# Only artifacts the pipeline produces are downloadable — no path traversal.
ALLOWED_ARTIFACTS = {
    "script.txt",
    "sentences.json",
    "scenes_index.json",
    "narration.mp3",
    "transcript.json",
    "scenes.json",
    "data.json",
    "meta.json",
    "video.mp4",
    "thumbnail.jpg",
    "source.docx",
    "steps.json",
    "screencast.mp4",
}

# Both GIF89a (animated, the common case) and GIF87a (older, static-capable)
# are valid — the extension alone is not proof of content, so every upload is
# checked against these regardless of filename.
_GIF_MAGIC = (b"GIF87a", b"GIF89a")

# .docx is a zip file — this is its local-file-header signature.
_DOCX_MAGIC = b"PK\x03\x04"


def _deps(
    request: Request,
) -> tuple[JobRepository, ArtifactStore, JobQueue, SubjectGuard, ScriptNormalizer]:
    s = request.app.state
    return s.jobs, s.artifacts, s.queue, s.guard, s.normalizer


def _title_from_script(script: str, limit: int = 80) -> str:
    """A short single-line title for a script-mode job (list display + compose)."""
    first_line = next((ln.strip() for ln in script.splitlines() if ln.strip()), "")
    title = " ".join(first_line.split())
    return f"{title[: limit - 1]}…" if len(title) > limit else title


async def _normalize_script(
    normalizer: ScriptNormalizer, script: str, subject: str, language: str
) -> tuple[str, str]:
    """Clean formatting (headings, markdown, bullets) into plain spoken prose and
    derive a title, in one LLM call. On failure, falls back to the raw script +
    a heuristic title so the user is never blocked. Shared by every input path
    that supplies its own narration verbatim (script mode, GIF screencast mode)."""
    title = _title_from_script(script)
    try:
        result = await normalizer.normalize(script, subject, language)
        return result.narration.strip() or script, result.title.strip() or title
    except (NormalizerUnavailableError, NormalizerMisconfiguredError) as exc:
        logger.warning("script normalization failed, using raw script: %s", exc)
        return script, title


async def _read_upload_limited(file: UploadFile, max_bytes: int, *, label: str) -> bytes:
    """Read an upload in chunks, rejecting it the moment it crosses max_bytes
    rather than buffering an arbitrarily large file into memory first."""
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/videos", response_model=CreateVideoResponse, status_code=status.HTTP_202_ACCEPTED
)
async def request_video(body: CreateVideoRequest, request: Request) -> CreateVideoResponse:
    jobs, _, queue, guard, normalizer = _deps(request)
    settings = request.app.state.settings
    if body.subject == "user-guide":
        raise HTTPException(
            status_code=400,
            detail="subject='user-guide' requires a screen recording — use "
            "POST /api/v1/videos/screencast instead.",
        )
    subject_config = get_subject_config(body.subject, settings)

    if body.input_mode == "script":
        # User supplies the narration: enforce the per-orientation cap (on the raw
        # input, before normalization) and skip the subject-relevance guard
        # (trusted content).
        script = (body.script or "").strip()
        max_len = (
            settings.max_script_length_short
            if body.orientation == "vertical"
            else settings.max_script_length_long
        )
        if not script:
            raise HTTPException(status_code=400, detail="Script must not be empty.")
        if len(script) > max_len:
            raise HTTPException(
                status_code=400, detail=f"Script too long (max {max_len} characters)."
            )
        script, title = await _normalize_script(normalizer, script, body.subject, body.language)
        job = Job(
            input_mode="script",
            query=title,
            script=script,
            subject=body.subject,
            orientation=body.orientation,
            language=body.language,
        )
    else:
        query = (body.query or "").strip()
        max_len = settings.max_query_length
        if not query:
            raise HTTPException(status_code=400, detail="Query must not be empty.")
        if len(query) > max_len:
            raise HTTPException(
                status_code=400, detail=f"Query too long (max {max_len} characters)."
            )

        try:
            verdict = await guard.check(query, body.subject)
        except GuardMisconfiguredError as exc:
            raise HTTPException(
                status_code=500,
                detail="Subject validation is misconfigured. Contact support.",
            ) from exc
        except GuardUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Subject validation is temporarily unavailable: {exc}",
            ) from exc
        if not verdict.is_valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Query is not a {subject_config.display_name} concept: "
                    f"{verdict.reason}"
                ),
            )

        job = Job(
            input_mode="topic",
            query=query,
            subject=body.subject,
            orientation=body.orientation,
            language=body.language,
        )

    await jobs.create(job)
    await queue.enqueue(job.id)
    return CreateVideoResponse(
        id=job.id,
        input_mode=job.input_mode,
        subject=job.subject,
        orientation=job.orientation,
        language=job.language,
        status=job.status,
    )


@router.post(
    "/videos/screencast",
    response_model=CreateVideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_screencast_video(
    request: Request,
    document: UploadFile = File(
        ..., description="A .docx software user guide — one Heading-styled "
        "section per step, each naming its own GIF in a caption line."
    ),
    files: list[UploadFile] = File(
        ..., description="The step GIFs named in the guide's captions, any order."
    ),
    subject: Literal["user-guide"] = Form("user-guide"),
    orientation: Literal["horizontal"] = Form("horizontal"),
    language: Literal["en", "vi"] = Form("en"),
) -> CreateVideoResponse:
    """Software-user-guide videos: one screen-recording GIF per narration step,
    matched to that step BY FILENAME from a .docx guide's own captions — not by
    upload order. Each scene's screencast clip is exactly its own step's GIF
    (see app/pipeline/steps/screencast.py); narration is the guide's own text,
    normalized per step (headings/captions are structural, never spoken)."""
    jobs, artifacts, queue, _, normalizer = _deps(request)
    settings = request.app.state.settings
    # Resolving the config validates the subject exists; failures here would be
    # a server misconfiguration, not a user error, so we let it raise as-is.
    get_subject_config(subject, settings)

    docx_bytes = await _read_upload_limited(document, settings.max_docx_bytes, label="Document")
    if docx_bytes[:4] != _DOCX_MAGIC:
        raise HTTPException(
            status_code=400,
            detail="File is not a .docx (bad magic bytes) — the extension "
            "alone isn't checked.",
        )

    try:
        doc_steps = extract_steps(docx_bytes)
    except DocxExtractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if not files:
        raise HTTPException(status_code=400, detail="At least one GIF must be uploaded.")

    uploaded: dict[str, tuple[str, bytes]] = {}
    for f in files:
        data = await _read_upload_limited(
            f, settings.max_gif_bytes, label=f"GIF '{f.filename}'"
        )
        if data[:6] not in _GIF_MAGIC:
            raise HTTPException(
                status_code=400,
                detail=f"'{f.filename}' is not a GIF (bad magic bytes) — the "
                "extension alone isn't checked.",
            )
        key = (f.filename or "").strip().lower()
        if not key:
            raise HTTPException(status_code=400, detail="An uploaded GIF has no filename.")
        if key in uploaded:
            raise HTTPException(
                status_code=400,
                detail=f"Multiple uploaded GIFs are named '{f.filename}'.",
            )
        uploaded[key] = (f.filename, data)

    # Bijection: every step must resolve to exactly one upload, and every
    # upload must be referenced by exactly one step — either direction failing
    # usually means the wrong file was selected, so both are named explicitly
    # rather than one side being silently ignored.
    matched_bytes: list[bytes] = []
    used_keys: set[str] = set()
    unmatched_steps: list[str] = []
    for doc_step in doc_steps:
        key = doc_step.gif_filename.strip().lower()
        entry = uploaded.get(key)
        if entry is None:
            unmatched_steps.append(doc_step.gif_filename)
            continue
        used_keys.add(key)
        matched_bytes.append(entry[1])
    unused_uploads = [name for key, (name, _) in uploaded.items() if key not in used_keys]

    if unmatched_steps or unused_uploads:
        parts = []
        if unmatched_steps:
            parts.append(
                "the guide references GIF file(s) that weren't uploaded: "
                + ", ".join(unmatched_steps)
            )
        if unused_uploads:
            parts.append(
                "uploaded GIF(s) aren't referenced by any step in the guide: "
                + ", ".join(unused_uploads)
            )
        uploaded_names = ", ".join(sorted(name for name, _ in uploaded.values()))
        raise HTTPException(
            status_code=400,
            detail="; ".join(parts) + f". Uploaded: {uploaded_names}.",
        )

    normalized = await asyncio.gather(*(
        _normalize_script(normalizer, doc_step.narration, subject, language)
        for doc_step in doc_steps
    ))
    narrations = [narration for narration, _ in normalized]
    script = "\n\n".join(narrations)
    title = _title_from_script(script)

    job = Job(
        input_mode="script",
        query=title,
        script=script,
        subject=subject,
        orientation=orientation,
        language=language,
    )
    # Written before the job is queued so the worker never races the artifacts:
    # by the time SEGMENT/SCREENCAST can run, steps.json and every step GIF are
    # already on disk.
    artifacts.save_bytes(job.id, "source.docx", docx_bytes)
    artifacts.save_json(
        job.id, "steps.json",
        [
            {"gif": f"step-{i:03d}.gif", "narration": narrations[i]}
            for i in range(len(narrations))
        ],
    )
    for i, gif_bytes in enumerate(matched_bytes):
        artifacts.save_bytes(job.id, f"source_gifs/step-{i:03d}.gif", gif_bytes)

    await jobs.create(job)
    await queue.enqueue(job.id)
    return CreateVideoResponse(
        id=job.id,
        input_mode=job.input_mode,
        subject=job.subject,
        orientation=job.orientation,
        language=job.language,
        status=job.status,
    )


@router.get("/videos", response_model=list[JobSummary])
async def list_videos(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[JobSummary]:
    jobs, _, _, _, _ = _deps(request)

    parsed_status: JobStatus | None = None
    if status_filter is not None:
        try:
            parsed_status = JobStatus(status_filter.upper())
        except ValueError:
            valid = ", ".join(s.value for s in JobStatus)
            raise HTTPException(
                status_code=400, detail=f"Invalid status '{status_filter}'. Valid: {valid}."
            ) from None

    return [JobSummary.from_job(j) for j in await jobs.list(parsed_status)]


@router.get("/videos/{job_id}", response_model=JobDetail)
async def get_video_job(job_id: str, request: Request) -> JobDetail:
    jobs, artifacts, _, _, _ = _deps(request)
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobDetail.from_job(job, artifacts.list_names(job_id))


@router.delete("/videos/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_job(job_id: str, request: Request) -> Response:
    """Delete a job and its on-disk artifacts. Allowed in any state; a job
    still processing simply has its record dropped and the pipeline's later
    updates become no-ops. YouTube upload records are left intact."""
    jobs, artifacts, _, _, _ = _deps(request)
    if not await purge_job(job_id, jobs, artifacts):
        raise HTTPException(status_code=404, detail="Job not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/videos/{job_id}/video")
async def download_video(job_id: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _, _ = _deps(request)
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != JobStatus.COMPLETED or not job.video_path:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Video is not ready.",
                "status": job.status.value,
                "current_step": job.current_step.value if job.current_step else None,
                "error_message": job.error_message,
            },
        )
    if not artifacts.exists(job_id, "video.mp4"):
        raise HTTPException(status_code=404, detail="Video file missing from artifact store.")
    return FileResponse(
        job.video_path, media_type="video/mp4", filename=f"{job.subject}-{job_id}.mp4"
    )


@router.get("/videos/{job_id}/artifacts/{name}")
async def download_artifact(job_id: str, name: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _, _ = _deps(request)
    if name not in ALLOWED_ARTIFACTS:
        allowed = ", ".join(sorted(ALLOWED_ARTIFACTS))
        raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {allowed}.")
    if await jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not artifacts.exists(job_id, name):
        raise HTTPException(status_code=404, detail=f"Artifact '{name}' not produced yet.")
    return FileResponse(artifacts.path_for(job_id, name), filename=name)
