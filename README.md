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
- Render

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

## 로컬 서버 운영

대용량 bag 업로드를 로컬에서 테스트할 때는 SSD/NVMe 경로에 임시 저장하는 구성을 권장합니다. 기본값은 프로젝트의 `runtime/uploads`입니다.

```bash
./scripts/run_local_server.sh
```

기본 설정은 업로드 파일 1개당 10GB, 동시 업로드 임시 저장소 250GB입니다.
임시 파일은 `UPLOAD_HOST_DIR`에 저장되고, 분석 완료 후 삭제됩니다. `UPLOAD_HOST_DIR`을 지정하지 않으면 프로젝트의 `runtime/uploads`를 사용합니다. 원본 bag 파일과 임시 저장소를 같은 HDD에 두면 읽기와 쓰기가 겹쳐 느려질 수 있으므로, 대용량 테스트는 SSD/NVMe 임시 저장소를 우선 사용합니다.

```text
http://127.0.0.1:8000
```

포트나 저장 위치를 바꾸려면 실행 전에 환경 변수를 지정합니다.

```bash
APP_PORT=8080 \
UPLOAD_HOST_DIR=./runtime/uploads \
./scripts/run_local_server.sh
```

디스크 여유 공간이 더 중요하고 속도 저하를 감수할 수 있다면 HDD 경로를 지정할 수 있습니다.

```bash
UPLOAD_HOST_DIR=/media/byeongjae/HDD00/autodriving_sensor_qa_uploads \
./scripts/run_local_server.sh
```

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

Render의 기본 배포 주소는 `https://서비스이름.onrender.com` 형식으로 제공되며, HTTPS 인증서는 Render가 자동으로 관리합니다.

무료 Render Web Service는 리소스가 작기 때문에 공개 데모와 CI/CD 검증용으로 사용합니다. `render.yaml`에는 Render 환경에서 파일 1개당 100MB, 동시 업로드 임시 저장소 300MB 제한을 적용했습니다. 대용량 bag 검증은 로컬 Docker 실행 환경에서 확인하는 것을 권장합니다.

Render 무료 인스턴스의 주요 제약은 다음과 같습니다.

- 512MB RAM, 0.1 CPU
- 15분 동안 요청이 없으면 sleep 상태로 전환
- 재시작, redeploy, sleep 이후 로컬 임시 파일 유지 보장 없음
- 무료 Web Service는 persistent disk 미지원

## 운영 로그와 자동 이슈 생성

애플리케이션은 요청 처리, 업로드 작업, 분석 작업의 주요 상태를 표준 로그로 남깁니다. Render에서는 서비스의 Logs 화면에서 `error`, `warning`, `job_id`, `request_id` 같은 키워드로 검색할 수 있습니다.

예상하지 못한 서버 오류가 발생했을 때 GitHub issue를 자동으로 만들고 싶다면 Render 환경 변수에 아래 값을 추가합니다. 잘못된 파일 업로드나 스키마 오류처럼 사용자가 만든 입력 오류는 Render 로그에만 남기고, GitHub issue는 만들지 않습니다.

```text
AUTO_CREATE_GITHUB_ISSUES=true
GITHUB_ISSUE_REPOSITORY=Donok53/autodriving_dataset_QA_dashboard
GITHUB_ISSUE_TOKEN=github_pat_...
GITHUB_ISSUE_LABELS=bug
AUTO_ISSUE_COOLDOWN_SECONDS=3600
AUTO_ISSUE_MAX_PER_RUNTIME=5
```

`GITHUB_ISSUE_TOKEN`은 GitHub fine-grained personal access token을 사용하고, 대상 저장소에 대한 Issues 읽기/쓰기 권한만 부여합니다. 이 값은 Render의 Secret 환경 변수로만 저장하고 Git에 커밋하지 않습니다.

자동 이슈에는 오류 타입, Render 서비스명, 배포 commit, job/request context, traceback이 포함됩니다. 업로드 파일명과 임시 파일 경로는 공개 issue에 노출되지 않도록 제외하거나 축약합니다.

자동 이슈 생성을 실제로 확인하고 싶다면 임시 테스트 엔드포인트를 켭니다.

```text
ENABLE_ERROR_TEST_ENDPOINT=true
ERROR_TEST_TOKEN=임의의-긴-테스트-토큰
```

배포 후 아래 요청을 보내면 의도적으로 RuntimeError가 발생하고, 자동 issue 생성이 켜져 있으면 GitHub issue가 생성됩니다.

```bash
curl -i \
  -H "X-Error-Test-Token: 임의의-긴-테스트-토큰" \
  https://서비스이름.onrender.com/api/debug/runtime-error
```

확인이 끝나면 `ENABLE_ERROR_TEST_ENDPOINT=false`로 되돌립니다.

## 분석 항목

파일 업로드 시 브라우저에서 업로드 진행률을 표시하고, 서버 분석은 인메모리 job 상태를 통해 검사 진행률을 표시합니다. 분석이 끝나면 완료된 job 결과 페이지로 이동합니다.

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
