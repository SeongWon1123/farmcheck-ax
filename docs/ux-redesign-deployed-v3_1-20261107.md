# UI 재설계 공개 반영 확인 (2026-11-07)

- 공개 URL: https://devopen.jntp-ax.or.kr/i/SpPClk4QcdJwast1/
- 실제 확인 버전: review-flow-20261107-v3.1
- 실제 자료/합성 예제 분리, 가져오기 모달, 사진·AI 차이·사람 판단 중심 배치
- 긴 파일명·원문·좌표 정보·실행 ID·해시·분류 점수·보정/해소 절차는 접힌 근거로 이동
- 이미 실행한 AI의 재실행과 긴 보류 사유도 접어 기본 화면을 단순화
- 보고서: 현재 기록 또는 체크한 명시적 기록만 포함. 읽기용 HTML 우선, CSV 및 접힌 JSON/무결성

## 실제 검증
- 고정본 pytest137 통과, basedpyright 오류/경고0, Ruff src tests 통과, 최종 JS문법 통과
- 공개 UI 파일 선택 전 업로드0, 명시적 가져오기 성공, AI 실행 클릭1회당 predict1회
- 실제 제공 pH16 원문 보존, 모델v1 real_inference, 사람 보류, 해당 기록1건 보고서3종200
- 기존 보고서 JSON/CSV/HTML 모두 이전 바이트와 동일
- 공개390px문서폭375,320px문서폭305. 새 작업공간 실제자료0건, 다른 작업공간 기록404
- 임시 로컬 검증 서버8503/8504 종료. 공개 앱·모델은 유지

## 복원·소스 경계
- 앱/DB백업: /data/hackathon/farmcheck/backups/pre-ux-workbench-20261107T064503Z
- 표현 정리 전 UI백업: /data/hackathon/farmcheck/backups/pre-ux-polish-20261107T064929Z
- 기본 릴리스 SHA256: b6767a76b8269877d5e3deb343cc898465f30efd11ea3ee05034fc5e47735408
- UI정리 SHA256: e75eb3ddb670d66035c38732645a0b0024155d65b5afabb233136dc62ce8a13c

작업 중 외부 동시 파일 변경을 발견해 원래 작업 트리를 덮어쓰지 않고 tmp/ux-frozen-v2 기준본을 고정해 검증·배포했습니다. 이후 최종 접기 정리는 이 기준본의 static3파일에만 적용했습니다. 현재 작업 트리의 나중 변경이 모두 배포됐다고 가정하면 안 됩니다. 배포 기준본/서버 릴리스와 대조 후 후속 작업을 진행하세요. GitHub push와 발표자료 갱신은 하지 않았습니다.