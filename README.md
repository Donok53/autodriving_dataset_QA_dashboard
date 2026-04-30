# 자율주행 센서 로그 QA 대시보드

자율주행 주행 로그 CSV를 분석하여 센서 품질, 동기화 상태, 이상 구간, 주행 이벤트를 자동으로 요약하는 웹 서비스입니다.

## 주요 목표

- 센서 로그의 결측치, timestamp 이상, sampling gap을 검사합니다.
- camera, lidar, radar, imu, gps 센서의 동기화 상태를 요약합니다.
- 급가속, 급제동, GPS jump, 센서 dropout 이벤트를 탐지합니다.
- GitHub Actions, Docker, Render 배포 흐름을 연결할 수 있는 구조로 개발합니다.

## 기술 스택

- Python
- FastAPI
- Pandas
- Pytest
- Docker
- GitHub Actions

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`으로 접속합니다.

## 테스트 실행

```bash
pytest
```

## Docker 실행

```bash
docker build -t autodriving-sensor-qa .
docker run --rm -p 8000:8000 autodriving-sensor-qa
```

컨테이너 실행 후 `http://127.0.0.1:8000/health`에서 상태를 확인할 수 있습니다.

## DevOps 파이프라인

이 저장소는 다음 흐름으로 개발과 배포를 연결합니다.

```text
Git push
  -> GitHub Actions
  -> pytest
  -> Docker image build
  -> Render Docker Web Service
```

CI 워크플로는 `.github/workflows/ci.yml`에 정의되어 있으며, push 또는 pull request가 발생하면 테스트와 Docker 빌드 검증을 자동으로 수행합니다.

Render 배포는 `render.yaml`을 기준으로 Docker Web Service를 생성하고, `/health` 엔드포인트를 health check로 사용합니다.
