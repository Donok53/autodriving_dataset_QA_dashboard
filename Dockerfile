FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

EXPOSE 8000

CMD ["sh", "-c", "echo ''; echo 'Autonomous Driving Dataset QA is starting.'; printf 'Local Docker Browser URL: http://localhost:%s\\n' \"${HOST_PORT:-8000}\"; printf 'Local Docker Health Check: http://localhost:%s/health\\n' \"${HOST_PORT:-8000}\"; echo 'Note: Uvicorn may display 0.0.0.0 because it is the container bind address.'; echo ''; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
