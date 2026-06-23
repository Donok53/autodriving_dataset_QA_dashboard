# MLOps 보고서 작성 근거

이 문서는 과제 보고서 목차에 맞춰 현재 저장소에서 확인할 수 있는 구현 근거와 캡쳐 대상을 정리한 자료입니다.

## 1. 프로젝트 개요

- 프로젝트 이름: 자율주행 센서 로그 QA 대시보드
- 목적: CSV/ROS bag 주행 로그를 업로드하면 센서 품질, 토픽 동기화, 주행 이벤트, XAI/VLM 분석 영상을 웹에서 확인하는 서비스
- GitHub: https://github.com/Donok53/autodriving_dataset_QA_dashboard
- 배포 주소: https://autodriving-dataset-qa-dashboard.onrender.com/
- MLflow Tracking Server 주소: 로컬 실행 기준 http://127.0.0.1:5000

캡쳐 대상:

- GitHub repository main 화면
- Render 배포 서비스 대시보드 화면
- 웹 서비스 메인 화면
- MLflow Experiments 화면과 run 상세 화면

## 2. 소프트웨어 주요 기능

서비스 기능:

- CSV 센서 로그 업로드 및 결측치, timestamp 중복, sampling gap 검사
- ROS bag 업로드 및 토픽 수, 주기, gap, 센서 커버리지 분석
- bag 카메라 프레임 추출 및 웹 영상 플레이어 재생
- `/xai/vlm_log` 요약 및 정상 주행, 안전 정지, 회피, 목적지 도착 집계
- 분석 작업 진행률 표시와 job 결과 페이지 제공

ML 기능:

- `models/current/student_baseline.joblib` student VLM 모델 로딩
- bag 카메라 프레임을 서버에서 모델 입력으로 변환
- 대표 객체 예측, confidence, top candidates, motion summary 생성
- overlay 프레임을 만들어 `Bag VLM 분석 영상`으로 표시
- `/api/xai/model-info`, `/api/xai/predict`, `/api/xai/log-summary` 제공

입력 데이터:

- CSV: `data/sample_sensor_log.csv`
- ROS bag: `data/sample_no_gps_5s.bag`, `data/sample_no_vehicle_motion_5s.bag`
- 학습 manifest: `data/vlm_training_manifest.csv`

출력 결과:

- 품질 점수, 품질 지표, 동기화 상태, 이상 구간, 이벤트 목록
- 카메라/VLM overlay 영상
- XAI/VLM 로그 요약
- 모델 예측 결과와 모델 메타데이터

## 3. 실행 환경

- 사용 OS: Windows 11 기준 작성 가능, WSL/Linux/macOS 명령도 제공
- Git/GitHub: main branch, public repository
- Docker: `Dockerfile`, `scripts/run_docker.ps1`, `scripts/run_docker.sh`
- MLflow: `requirements-mlops.txt`, `scripts/run_mlflow_server.*`, `docker-compose.mlflow.yml`
- MLflow 저장소: backend DB는 `mlruns/mlflow.db`, artifact는 `mlartifacts`
- 배포 환경: Render Docker Web Service, `render.yaml`

## 4. 전체 MLOps 파이프라인 구조

코드 변경 흐름:

```text
local edit -> git commit -> git push -> GitHub Actions -> pytest/Docker build -> Render auto deploy
```

모델 학습 흐름:

```text
data/vlm_training_manifest.csv -> scripts/train_vlm_model.py -> MLflow run -> models/candidates/{version}
```

보고서용 비교 실험 생성:

```text
scripts/create_mlflow_comparison_runs.py
  -> outdoor-rich-v1 baseline import
  -> outdoor-rich-v2-full / lite / regularized candidate training
  -> MLflow experiment runs
  -> MLflow Model Registry versions
```

모델 등록/반영 흐름:

```text
candidate 평가 -> scripts/promote_model.py -> models/current -> /api/xai/model-info -> 서비스 추론 반영
```

서비스 운영 흐름:

```text
사용자 파일 업로드 -> FastAPI 분석 job -> 센서/bag/XAI/VLM 분석 -> 결과 페이지 표시 -> 로그/오류 대응
```

## 5. Git 기반 개발 과정

- 개발 흐름: 기능 단위로 구현 후 테스트, commit, push
- 커밋 전략: 사용자 관점에서 확인 가능한 단위로 한국어 메시지 작성
- 예시 커밋:
  - `서버 VLM bag 분석 영상 생성`
  - `서버 VLM 주행상태 요약 보존`
- 브랜치: feature branch에서 PR 생성 후 main merge 흐름을 사용했고, 현재 main에 반영됨

## 6. CI/CD 구성

파일:

- `.github/workflows/ci.yml`

주요 단계:

- repository checkout
- Python 3.11 설치
- `requirements.txt` 설치
- `pytest` 실행
- MLOps script `py_compile` 검증
- Docker image build 검증

캡쳐 대상:

- GitHub Actions 최신 성공 run
- workflow 단계별 성공 화면

## 7. Docker 기반 환경 구성

서비스 Docker:

- `Dockerfile`
- Python 3.11 slim 기반
- FastAPI 앱, 데이터, 모델 복사
- `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`

실행:

```powershell
.\scripts\run_docker.ps1
```

```bash
./scripts/run_docker.sh
```

MLflow Docker:

```bash
docker compose -f docker-compose.mlflow.yml up
```

## 8. ML 모델 구성

사용 데이터:

- `data/vlm_training_manifest.csv`
- 9개 객체 클래스에 대한 학습 manifest와 deterministic augmentation

모델 종류:

- scikit-learn `LogisticRegression`
- grayscale image feature + context feature를 결합한 student VLM 분류 모델

학습 코드:

- `scripts/train_vlm_model.py`

평가 지표:

- accuracy
- macro F1
- weighted F1
- macro precision
- macro recall

초기 모델과 신규 모델 비교:

- 초기 모델: `models/current/model_info.json`의 `outdoor-rich-v1`
- 신규 모델: `scripts/create_mlflow_comparison_runs.py` 실행 후 MLflow에서 `outdoor-rich-v2-full`, `outdoor-rich-v2-lite`, `outdoor-rich-v2-regularized` metric 비교

## 9. MLflow 기반 실험 관리

Tracking Server:

- 기본 주소: http://127.0.0.1:5000

실행:

```bash
pip install -r requirements-mlops.txt
./scripts/run_mlflow_server.sh
```

기록 항목:

- parameter: image size, feature dim, train/test rows, LogisticRegression 설정
- metric: accuracy, macro F1, weighted F1, macro precision, macro recall
- artifact: training manifest, service model bundle, classification report, confusion matrix
- model: MLflow sklearn classifier

비교 run 생성:

```bash
python scripts/create_mlflow_comparison_runs.py
```

캡쳐 대상:

- `xai-vlm-dashboard` experiment에서 여러 run이 나란히 보이는 화면
- `outdoor-rich-v1` baseline run
- `outdoor-rich-v2-*` candidate run들의 metric 비교 화면
- MLflow Models 메뉴의 `xai_student_model` version 목록

가장 좋은 모델 선정 기준:

- 1차 기준: macro F1
- 2차 기준: accuracy
- 운영 안정성 기준: `/api/xai/model-info`, pytest, bag 업로드 분석 성공 여부

## 10. 모델 등록 및 서비스 반영

저장 방식:

- 현재 champion: `models/current/student_baseline.joblib`
- 현재 메타데이터: `models/current/model_info.json`
- 후보 모델: `models/candidates/{version}`
- 이전 모델: `models/versions/{timestamp}-{version}`

서비스 로딩 방식:

- `app/services/model_service.py`가 `MODEL_PATH` 또는 `models/current/model_info.json`의 model path를 읽음

신규 모델 반영:

```bash
python scripts/promote_model.py --candidate-dir models/candidates/outdoor-rich-v2
```

방식:

- 수동 승격 사용
- 이유: 학습 metric만으로 실제 bag 영상 품질을 완전히 보장하기 어렵기 때문에, 사람이 MLflow metric과 대시보드 결과를 확인한 뒤 champion으로 반영

## 11. 재학습 또는 모델 개선 과정

재학습 이유 예시:

- 기존 모델이 특정 실외 객체나 장애물 상황을 충분히 구분하지 못함
- bag 영상에서 대표 객체와 주행 상태 설명을 함께 보여줄 필요가 생김

변경 항목:

- 데이터: `data/vlm_training_manifest.csv`
- 코드: `scripts/train_vlm_model.py`
- 파라미터: image size, LogisticRegression C, max_iter 등

비교 방법:

- MLflow run metric 비교
- `models/current/model_info.json`과 candidate `model_info.json` 비교
- 업로드 bag 결과 화면에서 VLM overlay 품질 확인

## 12. 운영 로그 및 문제 대응

서비스 로그:

- FastAPI middleware가 request method, path, status code, duration, request id 기록
- Render Logs 화면에서 확인

예측 요청 로그:

- `/api/xai/model-info`
- `/api/xai/predict`
- bag upload job stage/progress

일부러 발생시킨 문제 예시:

- 손상된 `.bag` 파일 업로드
- 결과: dashboard error로 표시, 서버는 500이 아니라 사용자 입력 오류로 처리
- 원인: bag index 또는 파일 구조가 깨짐
- 해결: bag index 복구 로직과 사용자 메시지 처리 추가

## 13. 롤백 및 이전 모델 관리

이전 모델 보관:

- `scripts/promote_model.py` 실행 시 현재 champion을 `models/versions`에 자동 보관

롤백:

```bash
python scripts/rollback_model.py --list
python scripts/rollback_model.py --version-dir models/versions/<archived-model-dir>
```

코드:

- `app/services/model_registry.py`

## 14. 전체 파이프라인 동작 흐름

코드 수정 후 서비스 반영:

```text
code edit -> pytest -> git commit -> git push -> GitHub Actions -> Render auto deploy
```

데이터 변경 후 재학습:

```text
training manifest edit -> train_vlm_model.py -> MLflow run -> candidate model 생성
```

모델 변경 후 운영 반영:

```text
candidate 검토 -> promote_model.py -> models/current 변경 -> pytest -> commit/push -> 배포
```

## 15. 문제 해결 경험

예시 1:

- 문제: server-side VLM 통합 후 `정상주행`, `회피중`, `안전모드` 요약이 사라진 것처럼 보임
- 원인: 카메라 객체 VLM summary가 bag 기반 XAI summary를 덮어씀
- 해결: bag 기반 `xai_summary`는 유지하고 server VLM summary는 `server_vlm.xai_summary`로 분리

예시 2:

- 문제: 손상된 bag index 파일 분석 실패
- 원인: bag index field를 읽지 못함
- 해결: 사용자 오류 메시지와 복구 가능한 bag 처리 흐름 추가

## 16. 느낀 점 및 개선 방향

배운 점:

- ML 모델 자체보다 학습, 등록, 승격, 롤백, 운영 로그가 연결되어야 실제 서비스 운영이 가능함
- 자동 승격보다 metric과 실제 결과 화면을 함께 확인하는 수동 champion 승격이 초기 프로젝트에는 안전함

개선 방향:

- 실제 주행 bag에서 더 많은 학습 label 수집
- MLflow Model Registry stage를 더 적극적으로 사용
- Render 배포와 모델 artifact 저장소를 분리
- 대용량 bag 처리를 위한 비동기 worker와 외부 object storage 도입
