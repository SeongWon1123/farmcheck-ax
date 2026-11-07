# FarmCheck

농업 AI 개발자·데이터 담당자를 위한 학습 전 데이터 검수 도구입니다.
사진·JSON의 원본 근거, 규칙 검사, 작물·주차 추론, 사람의 판단과 고정 검수 보고서를 연결합니다.
성분 측정·질병 진단·비료 처방·자동 라벨 보정 서비스가 아닙니다.

## 사용자 흐름

1. 공개 합성 시연을 보거나, 본인 브라우저의 비공개 작업공간에 JPEG+AI Hub JSON 한 쌍을 추가합니다.
2. 원본·정규화 값·실제 이미지 크기·해시·확인 후보를 비교합니다.
3. 실제 자료에서 AI 추론을 명시적으로 실행합니다. 브라우저는 우리 백엔드만 호출하고, 백엔드가 사진만 AIStream에 전송합니다.
4. 사람이 검토·보류·보정하고, 필요하면 재검사 후 근거를 선택해 해소 판단을 남깁니다.
5. 선택한 범위의 JSON/CSV/HTML 고정 보고서를 만듭니다. 이후 수정해도 이전 고정본은 바뀌지 않습니다.

공개 시연: https://devopen.jntp-ax.or.kr/i/SpPClk4QcdJwast1/
소스 저장소: https://github.com/SeongWon1123/farmcheck-ax

## 실행

Python 3.12와 기존 프로젝트 환경을 사용합니다.

```sh
uv sync --locked
FARM_MODEL_PROFILE=pilot uv run uvicorn farmcheck.app:app --host 127.0.0.1 --port 8502
```

Windows PowerShell:

```powershell
$env:FARM_MODEL_PROFILE="pilot"
uv run uvicorn farmcheck.app:app --host 127.0.0.1 --port 8502
```

기본 자료 모드는 synthetic입니다. 실제 업로드 자료는 전체 공개 카탈로그에 넣지 않고 작업공간에 귀속합니다.
쿠키는 비공개 작업공간 접근 수단입니다. 다른 브라우저와 자료를 공유하지 않으며, 쿠키를 잃으면 같은 공간에 접근할 수 없습니다.
중요한 보고서는 내려받으세요. 계정 기반 팀 협업·복구 기능은 없습니다.

## 실제 자료 추가 조건

- JPEG 1개 + UTF-8 JSON 1개. 두 파일의 이름 줄기와 JSON images.imagepath가 일치해야 합니다.
- JPEG 최대 10MiB·1,600만 픽셀, JSON 최대 1MiB. 메타데이터 없는 JPEG를 사용하세요. EXIF 자료는 거부합니다.
- 작업공간당 최대 32쌍·128MiB. 실제 자료를 공개 기본 시연이나 Git 저장소에 게시하지 않습니다.
- JSON 원본은 보존합니다. 가공 선언 해상도와 실제 이미지 크기가 달라도 경고만 제공하며 자동 보정하지 않습니다.
- 사진·주차 예측과 기록의 불일치는 검수 후보이며, 라벨 오류·센서 고장이라는 확정이 아닙니다.

## 모델 프로필

FARM_MODEL_PROFILE=provided는 기존 제공 모델, pilot은 12팀 추가학습 실험 모델 v1을 사용합니다.
임의 주소를 브라우저에서 지정할 수 없으며 서버의 고정된 프로필만 사용합니다.
실험 모델은 models:/model-1c4cc046/1이며, 기존 모델의 사전학습 중복 여부는 불명확합니다.
성공한 연결 시험이나 같은 출처 점수를 독립적인 정확도·데이터 검수 효과로 주장하지 않습니다.
합성 SVG에는 실제 추론을 요청하지 않습니다. AgentStream 대화 기능은 별개이며 미연동입니다.

## 검증

```sh
uv run pytest -q
uv run basedpyright
uv run ruff check --no-cache src tests
```

이번 변경의 검증·배포 여부는 docs/verification.md 및 최종 작업 보고를 확인하세요.
이전 66개 테스트 통과 기록을 이번 변경의 통과 결과로 대신하지 않습니다.
입력·모델·보고서 API 계약은 docs/product-integration-api.md에 있습니다.

## 배포

기존 팜체크 전용 경로 /data/hackathon/farmcheck, 포트 8501만 사용합니다.
APP_BASE_PATH는 공개 접두사, APP_ROUTE_PREFIX는 프록시가 전달하는 접두사입니다.
기존 공용 모델과 다른 서비스는 수정하지 않습니다.
배포 완료는 실제 모델 응답, 외부 URL의 전체 사용자 흐름, 세션 간 격리, 고정 보고서 불변성을 확인한 뒤에만 선언합니다.

## PDF 근거

AIStream 교육자료 35쪽(백엔드 경유), 40쪽(실제 API·외부 URL 완료 기준), 41~43쪽(연결 확인·추론).
문제출제서 5쪽(제공 데이터 범위·공개 자료 보호·최소 1개 플랫폼 활용).
전체 PDF 7개를 확인했으며 HACCP 지식베이스 실습은 농업 영상 검수 기능의 정답·진단 근거로 사용하지 않습니다.
