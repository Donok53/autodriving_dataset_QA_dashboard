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
