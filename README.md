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

## 로컬 서버 운영

대용량 bag 업로드를 로컬 HDD에 저장하면서 운영하려면 아래 스크립트를 사용합니다.

```bash
UPLOAD_HOST_DIR=/path/to/autodriving_sensor_qa_uploads \
./scripts/run_local_server.sh
```

기본 설정은 업로드 파일 1개당 10GB, 동시 업로드 임시 저장소 250GB입니다.
임시 파일은 `UPLOAD_HOST_DIR`에 저장되고, 분석 완료 후 삭제됩니다. `UPLOAD_HOST_DIR`을 지정하지 않으면 프로젝트의 `runtime/uploads`를 사용합니다.

```text
http://127.0.0.1:8000
```

포트나 저장 위치를 바꾸려면 실행 전에 환경 변수를 지정합니다.

```bash
APP_PORT=8080 \
UPLOAD_HOST_DIR=/path/to/autodriving_sensor_qa_uploads \
./scripts/run_local_server.sh
```

## HTTPS 로컬 서버 운영

공개 도메인을 가지고 있거나 DuckDNS 같은 동적 DNS를 사용할 수 있다면 Caddy reverse proxy로 HTTPS를 붙일 수 있습니다.

```bash
PUBLIC_DOMAIN=sensor-qa.example.com \
UPLOAD_HOST_DIR=/path/to/autodriving_sensor_qa_uploads \
./scripts/run_https_local_server.sh
```

기본 호스트 포트는 Caddy용 `8088`, `8443`입니다. 공유기에서 아래처럼 포트포워딩합니다.

```text
외부 TCP 80  -> 이 PC 내부 IP:8088
외부 TCP 443 -> 이 PC 내부 IP:8443
```

DNS의 `A` 레코드는 공유기 공인 IP를 가리켜야 합니다. Caddy 인증서와 설정 데이터는 기본적으로 프로젝트의 `runtime/caddy`에 저장되며, `CADDY_DATA_DIR`, `CADDY_CONFIG_DIR` 환경 변수로 바꿀 수 있습니다.

### DuckDNS DNS 인증 방식

외부 80/443 포트를 사용할 수 없는 네트워크에서는 DuckDNS API token으로 DNS-01 인증을 수행합니다. 이 방식은 인증서 발급 시 외부에서 서버의 80/443 포트로 접속할 필요가 없습니다.

```bash
cp deploy/duckdns.env.example .env.duckdns
```

`.env.duckdns`에 DuckDNS token과 운영 값을 넣습니다. 이 파일은 `.gitignore`에 의해 Git에 올라가지 않습니다.

```text
PUBLIC_DOMAIN=bagfile-qa.duckdns.org
DUCKDNS_API_TOKEN=replace_with_duckdns_token
HTTPS_PORT=8443
PUBLIC_HTTPS_PORT=18443
LOCAL_APP_PORT=8088
CADDY_CONTAINER_DNS=8.8.8.8
DUCKDNS_RESOLVER=8.8.8.8:53
UPLOAD_HOST_DIR=/path/to/autodriving_sensor_qa_uploads
ALLOW_LOCAL_UNLIMITED_UPLOADS=true
LOCAL_UPLOAD_HOSTS=127.0.0.1,localhost,::1
```

실행합니다.

```bash
./scripts/run_duckdns_https_server.sh
```

공유기에서는 HTTPS 접속용 포트만 열면 됩니다.

```text
외부 TCP 18443 -> 이 PC 내부 IP:8443
```

접속 주소는 `https://bagfile-qa.duckdns.org:18443` 형식입니다.
이 스크립트는 HTTPS 포트만 공개하므로, HTTP 공개가 필요하지 않다면 공유기의 18080/8088 포워딩은 끄면 됩니다.
서버 PC에서 직접 대용량 파일을 올릴 때는 Caddy와 공유기를 거치지 않는 `http://127.0.0.1:8088` 로컬 주소를 사용하면 업로드 경로가 짧아집니다.
기본값에서는 `127.0.0.1` 또는 `localhost`로 들어온 로컬 직결 업로드만 파일 1개당 10GB 제한을 적용하지 않습니다. 공개 HTTPS 주소에는 10GB 제한이 유지됩니다.

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
