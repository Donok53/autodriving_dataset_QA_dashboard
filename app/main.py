from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.analyzer import InvalidSensorLogError, analyze_csv

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
    if not file.filename or not file.filename.endswith(".csv"):
        summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
        return _dashboard_response(request, summary, SAMPLE_DATA_PATH.name, "CSV 파일만 업로드할 수 있습니다.")

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
