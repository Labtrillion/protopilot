# ProtoPilot 모노레포 통합 기록

2026년 11월 20일 KST에 GitHub `main`과 NVIDIA Brev의 기존 `/home/ubuntu/protopilot` 체크아웃을 `a2ceacf`로 맞췄다. 이 문서는 통합 당시의 코드와 검증을 기록한다. 이후 작업 절차와 Feature별 Zoro-1 요구사항은 [AGENTS.md](../AGENTS.md)를 따른다.

## 병합 순서

| 커밋 | 작업 | 반영 내용 |
| --- | --- | --- |
| `674cc2f` | 기존 `main` | SOP 관측 조건에 따른 판단, Zoro 에이전트, 알람 대응 분기 비교가 들어 있던 기준점 |
| `4819d44` → `3d1a329` | `codex/monorepo-layout`을 `main`에 병합 | Git 충돌 없이 Brev 실행 구조, 문서, Trida Stack 서브모듈을 추가. 로컬의 프론트엔드 수정 4개를 보존 |
| `0d3ee1a` | 로컬 프론트엔드 작업 커밋 | WebGL 초기화 실패·연결 중단 시 2D 공정도로 전환. 컨텍스트 복구 시 조명을 다시 만들고, 사용자가 3D를 보던 경우 3D 보기를 복원 |
| `602f269` | Brev의 작업을 `codex/brev-server-sync`에 보존 | 서버에 있던 알람 검토 UI와 RAG 정리를 커밋. 텍스트 파일 6개의 CRLF 개행을 LF로 맞춤. 커밋을 SSH로 가져와 로컬 GitHub 인증으로 브랜치를 푸시 |
| `a2ceacf` | `codex/brev-server-sync`를 `main`에 병합 | 프론트엔드와 문서 충돌 3개를 해결해 `main`에 푸시. Brev의 기존 체크아웃에서 `main`으로 전환하고 `git pull --ff-only`와 서브모듈 동기화를 실행 |

`a2ceacf`의 부모는 로컬 WebGL 변경이 들어간 `0d3ee1a`와 Brev 작업을 보존한 `602f269`다. 두 작업 모두 `main` 이력에서 확인할 수 있다.

## 변경된 내용

### Brev 실행 환경

- `src/`의 관제 화면, `server/`의 시뮬레이터 API, `zoro/`의 에이전트와 RAG를 동일한 저장소 루트에서 사용한다.
- `.gitmodules`에 `vendor/trida-stack`을 등록했다. 통합 시점에 고정한 서브모듈 커밋은 `0ddcf516e458a82892d7fe0d0dabeb113d3cf424`다.
- `scripts/install-zoro.sh`는 서빙용 가상환경을 만들고 의존성을 설치한다. `scripts/start-zoro.sh`는 Zoro-1 판단 서버를, `scripts/start-protopilot.sh`는 관제 서버를 실행한다. `scripts/server-runtime.sh`가 `.runtime/zoro.env`와 서비스 로그·PID를 처리한다.
- `.runtime/`과 `worktrees/`를 Git에서 제외했다. Brev의 기존 보조 체크아웃 `worktrees/pp-setpoint/`와 서버 설정은 해당 서버에서 관리한다. 실행 진입점과 환경 변수는 [README의 Brev 서버 구성](../README.md#brev-서버-구성)에 있다.

### 관제 화면과 Zoro 대응 검토

- `src/app.css`, `src/index.html`, `src/plant-view.js`, `src/scene.js`에 WebGL 장애 상태 표시와 2D 대체 경로를 추가했다.
- `src/app.js`와 `src/event-monitor.js`가 충돌했다. `main`의 알람 찾기, 대응 비교, 알림 끄기, 활성 알람 필터와 키보드 초점 복원 로직을 기준으로 Brev의 Zoro 검토 클릭 처리를 연결했다.
- `src/alarm-card.js`는 체크포인트가 있는 활성 경고에 **Zoro 대응 검토** 버튼을 표시한다. `src/zoro-agent.js`는 기존 `POST /api/zoro-agent`를 호출하고 모델 판단, 분기별 지표, 권고 및 재시도 상태를 표시한다. 기존 대응 비교 버튼은 `POST /api/alarm-response`를 계속 사용한다.

### RAG와 벤치마크

- 기존 `zoro/zoro-rag/`의 검색기, SOP 원문, 코퍼스, Studio 평가 코드와 검증 CSV를 `zoro/rag_jiyeon/legacy/`로 이동했다. `zoro/rag_jiyeon/zoro_rag_knowledge/`의 공용 IDV 카탈로그와 외란 가이드를 참조하도록 정리했다.
- RAG 예시 JSON, SOP 생성 스크립트, `zoro/tests/test_core.py`와 문서의 경로를 새 구조에 맞췄다.
- `zoro/docs/BENCHMARK.md`의 충돌은 `main`의 비용 정의와 Brev의 경로 변경을 함께 반영해 해결했다. 라이브 분기는 시뮬레이터 내부 운전비를 적분하고, 오프라인 CSV는 문헌의 비용식으로 적분한다. 오프라인 계산과 비교 조건의 참조 경로를 각각 `rag_jiyeon/legacy/studio_eval.py`와 `rag_jiyeon/legacy/STUDIO.md`로 갱신했다.

## 검증 결과

| 확인 항목 | 통합 당시 결과 |
| --- | --- |
| `npm run build` | 프론트엔드 번들 생성 성공 |
| `node --test tests/*.mjs` | 7개 통과 |
| `.venv/bin/python -m unittest discover -s tests -v` | 서버 회귀 테스트 29개 통과 |
| `zoro/`에서 `python -m pytest -q` | 에이전트 테스트 42개 통과 |
| 추가 동작 확인 | 대응 비교·Zoro 검토·알림 끄기 클릭 연결, 체크포인트 미존재 시 검토 버튼 숨김, 이동한 RAG의 공용 IDV 조회 확인 |
| Git 동기화 | 로컬 `main`, GitHub `origin/main`, Brev의 `main`이 모두 `a2ceacf`. 로컬과 Brev 작업 트리가 깨끗하고 Trida Stack 커밋 일치 |

Brev에서 확인한 범위는 저장소와 서브모듈 동기화까지다. 위 78개 테스트는 로컬에서 실행했다.

## Git에서 재확인

```sh
git log --first-parent --oneline 674cc2f..a2ceacf
git diff --find-renames --stat 674cc2f..a2ceacf
git diff a2ceacf^1 a2ceacf -- src zoro/docs/BENCHMARK.md
git show --stat 602f269
git submodule status -- vendor/trida-stack
```
