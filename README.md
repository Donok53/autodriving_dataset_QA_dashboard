# 자율주행 센서 로그 QA 대시보드

자율주행 주행 로그 CSV 또는 ROS bag 파일을 분석하여 센서 품질, 동기화 상태, 이상 구간, 주행 이벤트를 자동으로 요약하는 웹 서비스입니다.

## 주요 목표

- 센서 로그의 결측치, timestamp 이상, sampling gap을 검사합니다.
- camera, lidar, imu, gps, 차량 움직임 명령(cmd_vel)의 동기화 상태를 요약합니다.
- 급가속, 급제동, GPS jump, 센서 dropout 이벤트를 탐지합니다.
- ROS bag 파일의 토픽 주기, 메시지 수, 핵심 데이터 스트림 커버리지, timestamp gap을 검사합니다.
- GitHub Actions, Docker, Render 배포 흐름을 연결할 수 있는 구조로 개발합니다.

## 기술 스택

- Python
- FastAPI
- Pandas
- Pytest
- Rosbags
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

## Render 배포

1. Render에서 New Web Service를 선택합니다.
2. GitHub 저장소 `Donok53/autodriving_dataset_QA_dashboard`를 연결합니다.
3. 배포 방식은 Docker를 선택합니다.
4. 배포가 끝나면 `/health`와 `/api/sample-analysis`로 실행 상태를 확인합니다.

배포 URL은 Render 서비스 생성 후 README에 추가합니다.

## 분석 항목

### CSV 로그

- 결측치 비율
- timestamp 중복
- sampling gap
- 센서 timestamp 동기화 offset
- 센서 dropout 구간
- 급가속 및 급제동 이벤트
- GPS 위치 jump 이벤트

### ROS bag 파일

- bag 전체 메시지 수와 주행 시간
- 토픽별 메시지 수, 추정 주파수, 중앙 주기, 최대 gap
- camera, lidar, imu, gps, vehicle_motion 계열 토픽 커버리지
- 핵심 데이터 스트림 누락 여부
- 데이터 스트림 간 nearest timestamp offset
- 차량 움직임 명령(cmd_vel) 토픽 누락 여부
- IMU 수평 가속도 이벤트
- GPS 위치 jump 이벤트

bag 분석은 `rosbags` 라이브러리를 사용하여 ROS 설치 없이 ROS1 `.bag` 파일을 읽는 방식으로 구성했습니다.

대용량 bag 파일은 브라우저 업로드와 임시 저장에 시간이 오래 걸릴 수 있으므로, 실제 주행 데이터 검증은 로컬 실행 또는 Docker 실행 환경에서 먼저 확인하는 것을 권장합니다.

## CSV 스키마

샘플 데이터는 `data/sample_sensor_log.csv`에 포함되어 있습니다.

| 컬럼 | 설명 |
| --- | --- |
| `timestamp` | 기준 로그 timestamp |
| `speed_mps` | 차량 속도 |
| `accel_mps2` | 차량 가속도 |
| `latitude`, `longitude` | GPS 좌표 |
| `camera_timestamp`, `lidar_timestamp`, `imu_timestamp`, `gps_timestamp`, `vehicle_motion_timestamp` | 센서 및 차량 움직임 데이터 timestamp |
| `camera_ok`, `lidar_ok`, `imu_ok`, `gps_ok`, `vehicle_motion_ok` | 센서 및 차량 움직임 데이터 수집 상태 |

## 프로젝트 구조

```text
app/
  main.py                  FastAPI 엔트리포인트
  models.py                분석 결과 모델
  services/
    analyzer.py            통합 분석 파이프라인
    loader.py              CSV 로딩 및 정규화
    quality_checker.py     품질 검사
    sync_checker.py        센서 동기화 분석
    event_detector.py      주행 이벤트 탐지
  templates/
  static/
data/
  sample_sensor_log.csv
tests/
.github/workflows/ci.yml
Dockerfile
render.yaml
```
