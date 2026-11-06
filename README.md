# FarmCheck

녹차 기록에서 확인할 항목을 찾고, 원본 근거와 미해결 질문을 다음 담당자에게
전달하는 검토 도구입니다. 농장 진단·처방·자동제어 서비스가 아닙니다.

후보 → 원문·사진·비교 → 검토/보류 → 수정·재검사 → 고정 인수인계를 지원합니다.
공개 시연은 소유한 합성 기록 8개와 SVG만 사용하며 실제 사진은 공개하지 않습니다.

공개 시연:
https://devopen.jntp-ax.or.kr/i/SpPClk4QcdJwast1/

## 실행

Python 3.12와 uv가 필요합니다.

```sh
uv sync --locked
uv run uvicorn farmcheck.app:app --host 127.0.0.1 --port 8502
```

브라우저에서 `http://127.0.0.1:8502/`를 엽니다. 기본값은 합성 모드입니다.
Windows에서 제공된 ZIP을 로컬에서 검토하려면 다음 환경 변수를 지정합니다.

```powershell
$env:FARM_DATA_MODE="green_tea"
$env:FARM_SOURCE_ZIP="C:/Users/user/Downloads/Sample.zip"
$env:FARM_DB_PATH="var/green_tea.sqlite"
uv run uvicorn farmcheck.app:app --host 127.0.0.1 --port 8503
```

실제 모드는 루프백에서만 사용합니다. 이미지·JSON은 원본 ZIP에서 읽고,
수정과 검토 이력은 `var/`의 별도 SQLite DB에 저장합니다.
브라우저 쿠키는 익명 작업 공간을 구분하므로 쿠키를 잃으면 기존 공간에
다시 접근할 수 없습니다. 중요한 인수인계는 파일로 내려받으세요.

## 검증과 한계

```sh
uv run pytest -q
uv run basedpyright
uv run ruff check .
```

2026-11-06 UTC 기준 전체 테스트 66개, 타입 검사와 린트가 통과했습니다.
실제 원본 재고는 녹차 3,872쌍, pH > 14 후보 33개 사진 기록과 24개 묶음입니다.
독립 측정이나 고장 횟수가 아닙니다. 실제 사진은 모두380×380입니다.
기존 작물·주차 모델에 실제 사진5장을 보내 HTTP200을 받았지만 정확도나
현장 효과를 측정한 실험으로 주장하지 않습니다.

사용 순서는 `docs/user-guide.md`, 근거와 경계는 `docs/data-contract.md`,
모델 연결 증거는 `docs/model-smoke.md`, 향후 사람 대상 실험은
`docs/evaluation-protocol.md`, 개발 과정은 `docs/development-journal.md`에 있습니다.
발표자료는 `presentation/deck.pptx`, `presentation/deck.pdf`입니다.

## 배포

`deploy/supervisord.conf`는 승인된 Ubuntu의 팜체크 전용 경로와8501포트 설정입니다.
서버에는 합성 모드만 배포합니다. `APP_BASE_PATH`는 외부 공개 경로이며,
`APP_ROUTE_PREFIX`는 프록시가 실제로 전달하는 경로입니다.
현재 프록시는 접두사를 보존하므로 두 값이 같습니다.
다른 서버에서는 경로·포트를 해당 환경에 맞게 수정해야 합니다.

## Data boundaries

- 원천 기록과 사진은 저장소에 포함하지 않습니다.
- 공개 모드는 합성 시연 데이터만 사용합니다.
- AgentStream 실연동은 사용자 요청으로 별도 일정에 진행합니다.
- 규칙 선별은 실제 센서 고장이나 작물 이상을 확정하지 않습니다.
- AgentStream 질의는 현재503 `disabled_by_owner`를 반환합니다.
  문맥 내보내기는 실제 에이전트 답변이 아닙니다.
- 원본 연결·해석 실패는 경로와 원본 해시를 가진 문제 목록으로 보존합니다.
- 모델 실패나 오래된 응답은 사람이 작성한 검토·해소를 자동으로 덮어쓰지 않습니다.
