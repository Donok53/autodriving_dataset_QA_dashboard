from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.analyzer import InvalidSensorLogError, analyze_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_sensor_log.csv"
templates = Jinja2Templates(directory=PROJECT_ROOT / "app" / "templates")

app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")

app = FastAPI(
    title="Autodriving Sensor Log QA Dashboard",
    description="Sensor quality and driving event analysis dashboard",
    version="0.1.0",
)


@app.get("/")
def dashboard(request: Request):
    summary = analyze_csv(SAMPLE_DATA_PATH).to_dict()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "source_name": SAMPLE_DATA_PATH.name,
            "error": None,
        },
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/sample-analysis")
def sample_analysis() -> dict[str, object]:
    try:
        return analyze_csv(SAMPLE_DATA_PATH).to_dict()
    except InvalidSensorLogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
