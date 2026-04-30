import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import logging
import os
import tempfile
from pathlib import Path
from threading import Lock
import time
from urllib.parse import unquote

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.bag_analyzer import InvalidBagFileError, analyze_bag
from app.services.analyzer import InvalidSensorLogError, analyze_csv
from app.services.issue_reporter import report_unexpected_error
from app.services.job_store import create_job, get_job, update_job

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_sensor_log.csv"
SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".bag"}
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024 * 1024)))
MAX_ACTIVE_UPLOAD_BYTES = int(os.getenv("MAX_ACTIVE_UPLOAD_BYTES", str(250 * 1024 * 1024 * 1024)))
MAX_UPLOAD_SIZE_LABEL = os.getenv("MAX_UPLOAD_SIZE_LABEL", "10GB")
MAX_ACTIVE_UPLOAD_SIZE_LABEL = os.getenv("MAX_ACTIVE_UPLOAD_SIZE_LABEL", "250GB")
UPLOAD_TEMP_DIR = Path(os.getenv("UPLOAD_TEMP_DIR", tempfile.gettempdir()))
ALLOW_LOCAL_UNLIMITED_UPLOADS = os.getenv("ALLOW_LOCAL_UNLIMITED_UPLOADS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOCAL_UPLOAD_HOSTS = {
    host.strip().lower()
    for host in os.getenv("LOCAL_UPLOAD_HOSTS", "127.0.0.1,localhost,::1").split(",")
    if host.strip()
}
_upload_reservation_lock = Lock()
_upload_reservations: dict[str, int] = {}
templates = Jinja2Templates(directory=PROJECT_ROOT / "app" / "templates")
logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


_configure_logging()


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@asynccontextmanager
async def lifespan(app: FastAPI):
    _cleanup_abandoned_upload_files()
    yield


app = FastAPI(
    title="Autodriving Sensor Log QA Dashboard",
    description="Sensor quality and driving event analysis dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")


class UploadTooLargeError(ValueError):
    pass


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = request.headers.get("rndr-id") or request.headers.get("x-request-id") or "-"
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.exception(
            "request_failed method=%s path=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            duration_ms,
            request_id,
        )
        report_unexpected_error(
            exc,
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "stage": "request",
            },
        )
        raise

    duration_ms = int((time.perf_counter() - start_time) * 1000)
    log_method = logger.info
    if response.status_code >= 500:
        log_method = logger.error
    elif response.status_code >= 400:
        log_method = logger.warning
    log_method(
        "request_completed method=%s path=%s status_code=%s duration_ms=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


@app.get("/")
def dashboard(request: Request):
    return _dashboard_response(request)


@app.get("/sample")
def sample_dashboard(request: Request):
    summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
    return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name)


@app.post("/upload")
async def upload_log(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        return _dashboard_response(request, error="파일 이름을 확인할 수 없습니다.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".csv", ".bag"}:
        return _dashboard_response(request, error="CSV 또는 BAG 파일만 업로드할 수 있습니다.")

    if suffix == ".bag":
        return await _analyze_uploaded_bag(request, file)

    content = await file.read()
    if not content:
        return _dashboard_response(request, error="비어 있는 파일입니다.")

    try:
        summary = analyze_csv(BytesIO(content)).to_dict()
        return _dashboard_response(request, summary, file.filename)
    except InvalidSensorLogError as exc:
        return _dashboard_response(request, error=str(exc))


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/sample-analysis")
def sample_analysis() -> dict[str, object]:
    try:
        return analyze_csv(SAMPLE_DATA_PATH).to_dict()
    except InvalidSensorLogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _dashboard_response(
    request: Request,
    summary: dict[str, object] | None = None,
    source_name: str | None = None,
    error: str | None = None,
):
    local_unlimited_upload = _is_local_unlimited_upload(request)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "source_name": source_name,
            "error": error,
            "has_result": summary is not None,
            "upload_limit_bytes": None if local_unlimited_upload else MAX_UPLOAD_BYTES,
            "upload_limit_label": "제한 없음" if local_unlimited_upload else MAX_UPLOAD_SIZE_LABEL,
            "analysis_click_error_enabled": _env_flag("ENABLE_ANALYSIS_CLICK_ERROR_SCENARIO"),
            "analysis_click_error_threshold": max(2, _env_int("ANALYSIS_CLICK_ERROR_THRESHOLD", 5)),
            "analysis_click_error_window_ms": max(1000, _env_int("ANALYSIS_CLICK_ERROR_WINDOW_SECONDS", 10) * 1000),
        },
    )


def _is_local_unlimited_upload(request: Request) -> bool:
    if not ALLOW_LOCAL_UNLIMITED_UPLOADS:
        return False

    hostname = (request.url.hostname or "").lower()
    return hostname in LOCAL_UPLOAD_HOSTS


async def _analyze_uploaded_bag(request: Request, file: UploadFile):
    enforce_size_limit = not _is_local_unlimited_upload(request)
    with _create_upload_temp_file(".bag") as temp_file:
        temp_path = Path(temp_file.name)
        total_bytes = 0
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            if enforce_size_limit and total_bytes + len(chunk) > MAX_UPLOAD_BYTES:
                temp_path.unlink(missing_ok=True)
                return _dashboard_response(request, error=_upload_too_large_message())
            total_bytes += len(chunk)
            temp_file.write(chunk)

    try:
        summary = analyze_bag(temp_path).to_dict()
        return _dashboard_response(request, summary, file.filename or temp_path.name)
    except InvalidBagFileError as exc:
        return _dashboard_response(request, error=str(exc))
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/upload")
async def create_analysis_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JSONResponse:
    filename, suffix = _validate_upload_filename(file.filename)
    enforce_size_limit = not _is_local_unlimited_upload(request)

    job = create_job(filename, suffix.removeprefix("."))
    logger.info("analysis_job_created job_id=%s source_type=%s upload_mode=form", job.job_id, job.source_type)
    temp_path = await _write_upload_to_temp_file(file, suffix, job.job_id, enforce_size_limit)
    background_tasks.add_task(_run_analysis_job, job.job_id, temp_path, suffix)

    updated_job = get_job(job.job_id) or job
    return JSONResponse(updated_job.to_dict())


@app.post("/api/upload/raw")
async def create_raw_analysis_job(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    if _should_trigger_analysis_click_error_scenario(request):
        raise RuntimeError("intentional runtime error after repeated analysis button clicks")

    enforce_size_limit = not _is_local_unlimited_upload(request)
    content_length = _validate_content_length(request.headers.get("content-length"), enforce_size_limit)
    filename, suffix = _validate_upload_filename(_filename_from_header(request))

    job = create_job(filename, suffix.removeprefix("."))
    logger.info("analysis_job_created job_id=%s source_type=%s upload_mode=raw", job.job_id, job.source_type)
    _reserve_upload_bytes(job.job_id, content_length or 0)
    try:
        temp_path = await _write_request_stream_to_temp_file(request, suffix, job.job_id, enforce_size_limit)
    except Exception:
        _release_upload_reservation(job.job_id)
        raise
    background_tasks.add_task(_run_analysis_job, job.job_id, temp_path, suffix)

    updated_job = get_job(job.job_id) or job
    return JSONResponse(updated_job.to_dict())


def _should_trigger_analysis_click_error_scenario(request: Request) -> bool:
    return (
        _env_flag("ENABLE_ANALYSIS_CLICK_ERROR_SCENARIO")
        and request.headers.get("x-error-test-scenario") == "analysis-clicks"
    )


@app.get("/api/jobs/{job_id}")
def get_analysis_job(job_id: str) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="분석 작업을 찾을 수 없습니다.")
    return job.to_dict()


@app.get("/jobs/{job_id}")
def analysis_job_result(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return _dashboard_response(request, error="분석 작업을 찾을 수 없습니다.")

    if job.status == "completed" and job.result is not None:
        return _dashboard_response(request, job.result, job.filename)

    error = job.error or f"분석이 아직 완료되지 않았습니다. 현재 단계: {job.stage}"
    return _dashboard_response(request, error=error)


def _filename_from_header(request: Request) -> str | None:
    raw_filename = request.headers.get("x-filename")
    if raw_filename is None:
        return None
    decoded_filename = unquote(raw_filename)
    return decoded_filename.replace("\\", "/").split("/")[-1]


def _validate_upload_filename(filename: str | None) -> tuple[str, str]:
    if not filename:
        raise HTTPException(status_code=400, detail="파일 이름을 확인할 수 없습니다.")

    clean_filename = filename.strip()
    suffix = Path(clean_filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="CSV 또는 BAG 파일만 업로드할 수 있습니다.")

    return clean_filename, suffix


def _validate_content_length(raw_content_length: str | None, enforce_size_limit: bool = True) -> int | None:
    if raw_content_length is None:
        return None

    try:
        content_length = int(raw_content_length)
    except ValueError:
        return None

    if enforce_size_limit and content_length > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_upload_too_large_message())

    return content_length


def _upload_too_large_message() -> str:
    return f"파일은 {MAX_UPLOAD_SIZE_LABEL} 이하만 업로드할 수 있습니다."


def _upload_storage_limit_message() -> str:
    return f"동시 업로드 저장 한도 {MAX_ACTIVE_UPLOAD_SIZE_LABEL}를 초과했습니다. 다른 분석이 끝난 뒤 다시 시도해주세요."


def _reserve_upload_bytes(job_id: str, byte_count: int) -> None:
    with _upload_reservation_lock:
        current_reserved = _upload_reservations.get(job_id, 0)
        next_reserved = max(current_reserved, byte_count)
        next_total = sum(_upload_reservations.values()) - current_reserved + next_reserved
        if next_total > MAX_ACTIVE_UPLOAD_BYTES:
            raise HTTPException(status_code=507, detail=_upload_storage_limit_message())
        _upload_reservations[job_id] = next_reserved


def _release_upload_reservation(job_id: str) -> None:
    with _upload_reservation_lock:
        _upload_reservations.pop(job_id, None)


def _create_upload_temp_file(suffix: str):
    UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return tempfile.NamedTemporaryFile(
        suffix=suffix,
        prefix="sensor-qa-upload-",
        dir=UPLOAD_TEMP_DIR,
        delete=False,
    )


def _cleanup_abandoned_upload_files() -> None:
    if not UPLOAD_TEMP_DIR.exists():
        return

    for temp_path in UPLOAD_TEMP_DIR.glob("sensor-qa-upload-*"):
        if temp_path.is_file():
            temp_path.unlink(missing_ok=True)


async def _write_upload_to_temp_file(
    file: UploadFile,
    suffix: str,
    job_id: str,
    enforce_size_limit: bool = True,
) -> Path:
    _reserve_upload_bytes(job_id, 0)

    async def read_next_chunk() -> bytes:
        return await file.read(UPLOAD_CHUNK_SIZE)

    try:
        return await _write_chunks_to_temp_file(read_next_chunk, suffix, job_id, enforce_size_limit)
    except Exception:
        _release_upload_reservation(job_id)
        raise


async def _write_request_stream_to_temp_file(
    request: Request,
    suffix: str,
    job_id: str,
    enforce_size_limit: bool = True,
) -> Path:
    stream = request.stream().__aiter__()

    async def read_next_chunk() -> bytes:
        try:
            return await stream.__anext__()
        except StopAsyncIteration:
            return b""

    return await _write_chunks_to_temp_file(read_next_chunk, suffix, job_id, enforce_size_limit)


async def _write_chunks_to_temp_file(
    read_next_chunk,
    suffix: str,
    job_id: str,
    enforce_size_limit: bool = True,
) -> Path:
    temp_path: Path | None = None
    total_bytes = 0

    try:
        with _create_upload_temp_file(suffix) as temp_file:
            temp_path = Path(temp_file.name)
            update_job(job_id, status="pending", progress=3, stage="파일 저장 중")
            while chunk := await read_next_chunk():
                if enforce_size_limit and total_bytes + len(chunk) > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError
                _reserve_upload_bytes(job_id, total_bytes + len(chunk))
                total_bytes += len(chunk)
                temp_file.write(chunk)

    except UploadTooLargeError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        update_job(
            job_id,
            status="failed",
            progress=100,
            stage="업로드 실패",
            error=_upload_too_large_message(),
        )
        raise HTTPException(status_code=413, detail=_upload_too_large_message()) from exc
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
        update_job(
            job_id,
            status="failed",
            progress=100,
            stage="업로드 실패",
            error="임시 저장 공간이 부족합니다. 디스크 용량을 확보한 뒤 다시 업로드해주세요.",
        )
        raise HTTPException(
            status_code=507,
            detail="임시 저장 공간이 부족합니다. 디스크 용량을 확보한 뒤 다시 업로드해주세요.",
        ) from exc
    except HTTPException as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
        update_job(
            job_id,
            status="failed",
            progress=100,
            stage="업로드 실패",
            error=str(exc.detail),
        )
        raise
    except asyncio.CancelledError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
        update_job(
            job_id,
            status="failed",
            progress=100,
            stage="업로드 실패",
            error="업로드가 중단되었습니다. 다시 시도해주세요.",
        )
        raise
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
        update_job(
            job_id,
            status="failed",
            progress=100,
            stage="업로드 실패",
            error="업로드가 중단되었습니다. 다시 시도해주세요.",
        )
        raise HTTPException(status_code=499, detail="업로드가 중단되었습니다. 다시 시도해주세요.") from exc

    if total_bytes == 0:
        if temp_path is None:
            raise HTTPException(status_code=400, detail="비어 있는 파일입니다.")
        temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
        update_job(job_id, status="failed", progress=100, stage="업로드 실패", error="비어 있는 파일입니다.")
        raise HTTPException(status_code=400, detail="비어 있는 파일입니다.")

    update_job(job_id, status="running", progress=10, stage="분석 작업 대기 중")
    if temp_path is None:
        raise HTTPException(status_code=500, detail="임시 파일을 생성하지 못했습니다.")
    return temp_path


def _run_analysis_job(job_id: str, temp_path: Path, suffix: str) -> None:
    try:
        logger.info("analysis_job_started job_id=%s source_type=%s", job_id, suffix.removeprefix("."))
        if suffix == ".csv":
            update_job(job_id, status="running", progress=25, stage="CSV 로딩 및 스키마 검사 중")
            summary = analyze_csv(temp_path).to_dict()
            update_job(job_id, progress=90, stage="CSV 분석 결과 정리 중")
        else:
            summary = analyze_bag(
                temp_path,
                progress_callback=lambda progress, stage: update_job(
                    job_id,
                    status="running",
                    progress=progress,
                    stage=stage,
                ),
            ).to_dict()

        update_job(job_id, status="completed", progress=100, stage="분석 완료", result=summary)
        logger.info("analysis_job_completed job_id=%s source_type=%s", job_id, suffix.removeprefix("."))
    except (InvalidSensorLogError, InvalidBagFileError) as exc:
        logger.warning(
            "analysis_job_failed job_id=%s source_type=%s reason=invalid_input error=%s",
            job_id,
            suffix.removeprefix("."),
            exc,
        )
        update_job(job_id, status="failed", progress=100, stage="분석 실패", error=str(exc))
    except Exception as exc:
        logger.exception(
            "analysis_job_failed job_id=%s source_type=%s reason=unexpected",
            job_id,
            suffix.removeprefix("."),
        )
        report_unexpected_error(
            exc,
            {
                "job_id": job_id,
                "source_type": suffix.removeprefix("."),
                "stage": "analysis_job",
            },
        )
        update_job(job_id, status="failed", progress=100, stage="분석 실패", error=f"예상하지 못한 오류: {exc}")
    finally:
        temp_path.unlink(missing_ok=True)
        _release_upload_reservation(job_id)
