from io import BytesIO
import tempfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.bag_analyzer import InvalidBagFileError, analyze_bag
from app.services.analyzer import InvalidSensorLogError, analyze_csv
from app.services.job_store import create_job, get_job, update_job

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_sensor_log.csv"
SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".bag"}
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024
MAX_UPLOAD_SIZE_LABEL = "10GB"
templates = Jinja2Templates(directory=PROJECT_ROOT / "app" / "templates")

app = FastAPI(
    title="Autodriving Sensor Log QA Dashboard",
    description="Sensor quality and driving event analysis dashboard",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")


class UploadTooLargeError(ValueError):
    pass


@app.get("/")
def dashboard(request: Request):
    summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
    return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name)


@app.post("/upload")
async def upload_log(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, "파일 이름을 확인할 수 없습니다.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".csv", ".bag"}:
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, "CSV 또는 BAG 파일만 업로드할 수 있습니다.")

    if suffix == ".bag":
        return await _analyze_uploaded_bag(request, file)

    content = await file.read()
    if not content:
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, "비어 있는 파일입니다.")

    try:
        summary = analyze_csv(BytesIO(content)).to_dict()
        return _dashboard_response(request, summary, file.filename)
    except InvalidSensorLogError as exc:
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, str(exc))


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
    summary: dict[str, object],
    source_name: str,
    error: str | None = None,
):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "source_name": source_name,
            "error": error,
        },
    )


async def _analyze_uploaded_bag(request: Request, file: UploadFile):
    with tempfile.NamedTemporaryFile(suffix=".bag", delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        while chunk := await file.read(1024 * 1024):
            temp_file.write(chunk)

    try:
        summary = analyze_bag(temp_path).to_dict()
        return _dashboard_response(request, summary, file.filename or temp_path.name)
    except InvalidBagFileError as exc:
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, str(exc))
    finally:
        temp_path.unlink(missing_ok=True)


@app.post("/api/upload")
async def create_analysis_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JSONResponse:
    filename, suffix = _validate_upload_filename(file.filename)

    job = create_job(filename, suffix.removeprefix("."))
    temp_path = await _write_upload_to_temp_file(file, suffix, job.job_id)
    background_tasks.add_task(_run_analysis_job, job.job_id, temp_path, suffix)

    updated_job = get_job(job.job_id) or job
    return JSONResponse(updated_job.to_dict())


@app.post("/api/upload/raw")
async def create_raw_analysis_job(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    _validate_content_length(request.headers.get("content-length"))
    filename, suffix = _validate_upload_filename(_filename_from_header(request))

    job = create_job(filename, suffix.removeprefix("."))
    temp_path = await _write_request_stream_to_temp_file(request, suffix, job.job_id)
    background_tasks.add_task(_run_analysis_job, job.job_id, temp_path, suffix)

    updated_job = get_job(job.job_id) or job
    return JSONResponse(updated_job.to_dict())


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
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, "분석 작업을 찾을 수 없습니다.")

    if job.status == "completed" and job.result is not None:
        return _dashboard_response(request, job.result, job.filename)

    summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
    error = job.error or f"분석이 아직 완료되지 않았습니다. 현재 단계: {job.stage}"
    return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, error)


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


def _validate_content_length(raw_content_length: str | None) -> None:
    if raw_content_length is None:
        return

    try:
        content_length = int(raw_content_length)
    except ValueError:
        return

    if content_length > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_upload_too_large_message())


def _upload_too_large_message() -> str:
    return f"파일은 {MAX_UPLOAD_SIZE_LABEL} 이하만 업로드할 수 있습니다."


async def _write_upload_to_temp_file(file: UploadFile, suffix: str, job_id: str) -> Path:
    async def read_next_chunk() -> bytes:
        return await file.read(UPLOAD_CHUNK_SIZE)

    return await _write_chunks_to_temp_file(read_next_chunk, suffix, job_id)


async def _write_request_stream_to_temp_file(request: Request, suffix: str, job_id: str) -> Path:
    stream = request.stream().__aiter__()

    async def read_next_chunk() -> bytes:
        try:
            return await stream.__anext__()
        except StopAsyncIteration:
            return b""

    return await _write_chunks_to_temp_file(read_next_chunk, suffix, job_id)


async def _write_chunks_to_temp_file(read_next_chunk, suffix: str, job_id: str) -> Path:
    temp_path: Path | None = None
    total_bytes = 0

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            update_job(job_id, status="pending", progress=3, stage="파일 저장 중")
            while chunk := await read_next_chunk():
                if total_bytes + len(chunk) > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError
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

    if total_bytes == 0:
        if temp_path is None:
            raise HTTPException(status_code=400, detail="비어 있는 파일입니다.")
        temp_path.unlink(missing_ok=True)
        update_job(job_id, status="failed", progress=100, stage="업로드 실패", error="비어 있는 파일입니다.")
        raise HTTPException(status_code=400, detail="비어 있는 파일입니다.")

    update_job(job_id, status="running", progress=10, stage="분석 작업 대기 중")
    if temp_path is None:
        raise HTTPException(status_code=500, detail="임시 파일을 생성하지 못했습니다.")
    return temp_path


def _run_analysis_job(job_id: str, temp_path: Path, suffix: str) -> None:
    try:
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
    except (InvalidSensorLogError, InvalidBagFileError) as exc:
        update_job(job_id, status="failed", progress=100, stage="분석 실패", error=str(exc))
    except Exception as exc:
        update_job(job_id, status="failed", progress=100, stage="분석 실패", error=f"예상하지 못한 오류: {exc}")
    finally:
        temp_path.unlink(missing_ok=True)
