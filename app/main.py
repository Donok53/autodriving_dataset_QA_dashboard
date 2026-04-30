from fastapi import FastAPI

app = FastAPI(
    title="Autodriving Sensor Log QA Dashboard",
    description="Sensor quality and driving event analysis dashboard",
    version="0.1.0",
)


@app.get("/")
def read_root() -> dict[str, str]:
    return {
        "service": "autodriving-sensor-log-qa-dashboard",
        "message": "Sensor QA dashboard is running.",
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
