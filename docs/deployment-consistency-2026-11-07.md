# 로컬·공개 서버·GitHub 정합성

2026년 11월 7일 읽기 전용 비교 결과.
서버나 Git 이력을 이 점검에서 변경하지 않았다.

## 결론

로컬 제품 소스와 공개 서버의 파일은 일치한다. 불필요한 재배포는 하지 않는다.
반면 현재 GitHub main은 후속 제품 연결 코드보다 뒤에 있어 제출본 동기화가 필요하다.

## 비교 근거

- 로컬 HEAD: `bd9f405938261590fb335bc9916f4a3682aed1a7`
- GitHub HEAD/main: 같은 커밋
- SSH 대상: 기존 팀 개발 서버
- 원격 앱: `/data/hackathon/farmcheck/app`
- 원격 hash 확인: 2026-11-07 06:07:33 UTC, 약 06:08 UTC 재확인
- `src/farmcheck/`의 Python·정적 파일 22개 SHA-256이 로컬과 모두 동일
- `deploy/supervisord.conf`, `pyproject.toml`, `uv.lock`, `README.md`도 동일
- 원격은 runtime-only 배포로 `.git`, 테스트·학습·문서 폴더가 없음

대표 SHA-256:

| 파일 | 로컬 = 원격 |
| --- | --- |
| `src/farmcheck/app.py` | `1768628be392e96cb094d0e816a6b58da0742d6c60f9875278b91292a604a53a` |
| `src/farmcheck/store.py` | `63a50497ca9a534614a73c2fb1443bf2d173102d353639f7a6bacc38ac27ef8e` |
| `src/farmcheck/import_routes.py` | `f6b033b46aca3f2f06d2faee7cde5844a646e0db84f598a361503fd3333ec9a7` |
| `src/farmcheck/model_connection.py` | `efc9a3eba13e38fdb8c2798b8355394e3989ebf04f9db1658ad66e93ed4cc29f` |
| `src/farmcheck/static/app.js` | `956184f62376b5e9c477c8358b89790d008519b76211e32fd3fa64627092f7ae` |
| `deploy/supervisord.conf` | `4147199db8cd0c1254970571695080a60924e95ac2237dc48ef1ed96e674535d` |

Supervisor는 farmcheck RUNNING, PID 12304를 반환했다.
프로세스는 8501 포트에서 실행되며 자료는 synthetic, 모델은 pilot,
공개 접두사는 `/i/SpPClk4QcdJwast1`, secure cookie가 켜져 있다.
원격 디스크 해시는 확인했지만 모든 Python 객체의 메모리 내용까지 대조한 것은 아니다.
실제 동작 증거는 `public-api-qa-2026-11-07.md`에 별도로 기록했다.

## 필요한 GitHub 반영

현재 커밋 이후의 제품 변경은 비공개 JPEG+JSON 가져오기, 작업공간 접근 제어,
pilot 모델 선택·추론 출처, 고정 보고서 목록과 연결 UI를 포함한다.
수정된 코드가 참조하는 새 `import_routes.py`, `model_connection.py`도 함께
제출해야 한다. 관련 제품·작업공간 보안 테스트와 팀원 안내를 포함한다.

다른 작업에서 만들어진 변경의 작성자를 이 점검자가 자신으로 설명하지 않는다.
서버 DB, 원본 ZIP·사진, 키, 모델 가중치, 비공개 테스트 ZIP은 GitHub에서 제외한다.
오프라인 학습 도구의 변경은 제품 runtime 동기화와 별개다.

커밋·푸시는 아직 실행하지 않았다. 승인받은 뒤 파일별 diff와 포함 범위를
확정하고 검증된 단위로 반영해야 3단계 전체를 완료할 수 있다.
