from io import BytesIO
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.bag_analyzer import InvalidBagFileError, analyze_bag
from app.services.analyzer import InvalidSensorLogError, analyze_csv
from app.services.job_store import create_job, get_job, update_job

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_sensor_log.csv"
templates = Jinja2Templates(directory=PROJECT_ROOT / "app" / "templates")

app = FastAPI(
    title="Autodriving Sensor Log QA Dashboard",
    description="Sensor quality and driving event analysis dashboard",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")


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
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일 이름을 확인할 수 없습니다.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".csv", ".bag"}:
        raise HTTPException(status_code=400, detail="CSV 또는 BAG 파일만 업로드할 수 있습니다.")

    source_type = suffix.removeprefix(".")
    job = create_job(file.filename, source_type)
    temp_path = await _write_upload_to_temp_file(file, suffix, job.job_id)
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


async def _write_upload_to_temp_file(file: UploadFile, suffix: str, job_id: str) -> Path:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        total_bytes = 0
        update_job(job_id, status="pending", progress=3, stage="파일 저장 중")
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            temp_file.write(chunk)

    if total_bytes == 0:
        temp_path.unlink(missing_ok=True)
        update_job(job_id, status="failed", progress=100, stage="업로드 실패", error="비어 있는 파일입니다.")
        raise HTTPException(status_code=400, detail="비어 있는 파일입니다.")

    update_job(job_id, status="running", progress=10, stage="분석 작업 대기 중")
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
