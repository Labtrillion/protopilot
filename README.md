# ProtoPilot TEP 시뮬레이터

Tennessee Eastman Process의 Mode 1을 로컬에서 실행하는 Three.js 관제 화면입니다. 공정의 배관과 유량·가스 압력을 보고, SP를 조정하거나 채널별 실시간 계측을 확인할 수 있습니다.

작업 브랜치와 Feature별 Zoro-1 요구사항은 [개발 지침](AGENTS.md), Brev와 `main`의 병합 내역은 [모노레포 통합 기록](docs/MONOREPO_MERGE.md)에서 확인할 수 있습니다.

## 실행

Python 3.13, Node.js, npm, Git 및 Python 패키지의 C 확장 빌드에 필요한 컴파일러가 필요합니다. macOS에서는 Xcode Command Line Tools의 clang을 사용했습니다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
npm start
```

서버 실행 후 브라우저에서 [시뮬레이터 열기](http://127.0.0.1:8765/). 초기화 버튼은 Mode 1 초기 상태에서 **일시정지된 새 실행**을 만듭니다.

## Brev 서버 구성

| 경로 | 내용 |
| --- | --- |
| `src/`, `server/` | 관제 화면과 시뮬레이터 API |
| `zoro/` | 판단 에이전트, RAG, SOP와 인덱스 |
| `vendor/trida-stack/` | Zoro-1 서빙용 Trida Stack 서브모듈 |
| `scripts/` | Brev 실행과 Zoro-1 환경 설치 |
| `.runtime/` | 서버 설정, Zoro-1 가상환경, 로그 및 PID |
| `worktrees/pp-setpoint/` | Brev의 기존 setpoint 개발용 워크트리 |

새로 체크아웃한 Brev 서버에서 Zoro-1을 준비하고 실행하려면 저장소 루트에서 다음을 실행합니다. 기존 가상환경이 있는 서버에서는 다시 설치할 필요가 없습니다.

```sh
git submodule update --init vendor/trida-stack
scripts/install-zoro.sh
scripts/start-zoro.sh
npm run build
scripts/start-protopilot.sh
```

Brev에서는 `.runtime/zoro.env`에서 서버 환경을 설정합니다. 로컬 Zoro-1을 사용하는 값은 `ZORO_BASE_URL=http://127.0.0.1:30001`입니다. 기존 체크포인트를 사용할 때는 `TRIDA_DECIDE_MODEL`에 해당 디렉터리를 지정합니다. 모델 캐시는 Hugging Face 설정을 따릅니다.

## 라이브 제어와 시나리오

- **값 조정**에서 폐루프 목표 9개를 변경할 수 있습니다. `ya`는 반응기 입구의 `100 × A / (A+C)`, `yac`는 `A+C` 몰백분율입니다. 두 값은 GC-106의 지연이 적용된 원계측으로 계산합니다.
- **시나리오 → 제어 옵션**에서 원료 조성 보정, 자동 보호 제어, 제품 G 피드백을 바꿀 수 있습니다. 자동 보호 제어는 기본으로 켜져 있습니다. 반응기 고압 2,900 kPa(g)부터 생산 지수를 제한하고, 고액위 90%부터 재순환 밸브 명령을 제한합니다. 개입과 해제는 이벤트에 남습니다.
- **시나리오 → 데모 시나리오**에서 5개 중 하나를 선택하고 **불러와 재실행 준비**를 누릅니다. 새 기록이 시간 0에서 일시정지됩니다. **실행**을 누르면 예약된 시각에 조작을 적용하고 지정된 종료 시각에 일시정지합니다. [데모 시나리오 JSON과 형식](docs/scenarios/DEMO_SCENARIOS.md#실행용-시나리오-json)
- **현재 시나리오 JSON 저장**으로 시작 조건과 조작 이력을 내보낼 수 있습니다. **이전 로컬 기록**에서는 저장된 실행을 다시 준비하거나 태그별 추세, CSV와 JSON을 확인할 수 있습니다.

## 라이브 알람 대응 비교

1. `POST /api/command`에 `{"op":"cooling_demo"}`를 보내 냉각수 입구 온도 변동과 밸브 고착을 재현합니다. 데모용 온도 기준을 사용하며 알람이 뜬 시점에 일시정지합니다. `{"op":"pressure_test"}`는 센서 시험을 재현합니다.
2. `GET /api/live`의 `run`과 `response_events`에서 확인한 ID를 `POST /api/alarm-response`에 `run`과 `alarm_id`로 전달합니다. 응답에 데모 SOP, 알람 발생 시점의 계측, Zoro-1이 우선 검토한 대응이 포함됩니다. SOP는 [로컬 문서](server/demo-sops.json)에 들어 있습니다.
3. 각 분기는 알람 발생 시점의 모델 상태, 난수 상태, 제어기 메모리, 수동 조작값과 활성 외란을 복제합니다. 외란을 고정한 채 최대 3시간 45분 동안 생산 목표를 유지하는 SOP 기준안과 25개 감량 조합을 비교합니다. **시뮬레이션 결과 보기**에서 적용 시점(즉시·15·30·45·60분)과 감량률(1·2.5·5·7.5·10%)별 카드로 결과를 확인할 수 있습니다. 각 카드에 점수, 트립, 알람 기준 복귀, 정상 운전 범위 이탈, 보호 개입, 최고 압력·온도, 분리기 액위, 생산량과 SOP 기준안 대비 차이가 표시됩니다.

4. `POST /api/zoro-agent`에 같은 `run`과 `alarm_id`를 보내면 [Zoro 판단 에이전트](zoro/README.md)가 SOP 후보 중 시험할 분기를 하나씩 고르고, 결과를 받아 다음 분기나 최종 권고를 정합니다. 각 분기는 위와 같은 체크포인트 복제로 실행하고, 비용·목표 근접도로 SOP 단독(유지) 대비 비교합니다.

분석은 라이브 관제와 분리된 모의 실험입니다. 센서 시험은 오프셋 적용 전 모델 계측으로 비교합니다. 분석 이력은 실행 ID, 알람 체크포인트, SOP 개정 번호, 모델 판단과 분기 결과를 `data/agent-trace.jsonl`에 기록합니다.

## Zoro-1과 개발 도구

라이브 알람 비교는 Zoro-1의 `/v1/systemone`으로 SOP에 정의된 확인 항목과 우선 검토할 후보를 선택합니다. 공식 [Zoro-1 서빙 방식](https://github.com/trillion-labs/trida-stack/tree/main/inference/zoro-1#decisions)으로 실행한 서버 주소를 `ZORO_BASE_URL`에 설정합니다. 예를 들어 30001 포트로 로컬에서 서빙하면 `ZORO_BASE_URL=http://127.0.0.1:30001 npm start`로 연결합니다. 서빙 환경에 따라 `ZORO_MODEL`과 `ZORO_API_KEY`를 설정할 수 있습니다. 서버 미설정·오류 시 로컬 규칙을 사용하며, 응답과 로그에 판단 경로가 기록됩니다.

개발용 실험이 필요하면 `.venv/bin/python -m server.experiments`로 [독립 실험 도구](http://127.0.0.1:8050/experiments/)를 실행합니다. **고급 설정**에서 운전 모드·초기 상태·제어 튜닝을 바꿀 수 있고, **Compare**, **Metrics / Record**, **JSON 시나리오**로 독립 실행을 확인할 수 있습니다.

기존 패키지의 `.venv/bin/tep-mcp`(stdio), `.venv/bin/tep`(실행·FDD 벤치마크·데이터셋·RL 내보내기), Gymnasium, 최적화 어댑터와 수치 검증을 같은 가상환경에서 사용할 수 있습니다. 파이썬 회귀 검사는 `.venv/bin/python -m unittest discover -s tests -v`로 실행합니다.

## Fig. 1 스트림 대조

| 스트림 | 시작 → 도착 | 연결 및 계측 |
| --- | --- | --- |
| 1 | A 원료 → 혼합 헤더 | XMV(3), XMEAS(1) |
| 2 | D 원료 → 혼합 헤더 | XMV(1), XMEAS(2) |
| 3 | E 원료 → 혼합 헤더 | XMV(2), XMEAS(3) |
| 4 | A/C 원료 → 스트리퍼 하부 | XMV(4), XMEAS(4) |
| 5 | 스트리퍼 상부 → 압축기 토출측 헤더 | #8 유량계 뒤에서 합류 |
| 6 | 혼합 헤더 → 반응기 | XMEAS(6), 반응기 입구 분석기 GC-106 |
| 7 | 반응기 상부 → 응축기 → 기액 분리기 | 응축기 출구를 기액 혼합으로 표시 |
| 8 | 기액 분리기 상부 → 압축기 → 혼합 헤더 | 토출 XMEAS(5), 토출→흡입 바이패스 XMV(5) |
| 9 | 기액 분리기 상부 분기 → 퍼지 출구 | XMV(6), XMEAS(10), 퍼지 분석기 GC-109 |
| 10 | 분리기 하부 → 이송 펌프 P-101 → 스트리퍼 측면 | XMV(7), XMEAS(14) |
| 11 | 스트리퍼 하부 → 제품 펌프 P-102 → 제품 출구 | XMV(8), XMEAS(17), 제품 분석기 GC-111 |
| 12 | CWS → 반응기 코일 → CWR | 회수 밸브 XMV(10), 유량 XMEASADD(6) |
| 13 | CWS → 응축기 냉각수측 튜브 → CWR | 회수 밸브 XMV(11), 유량 XMEASADD(8) |
| STM / COND | 스팀 공급 → 재비기 유틸리티측 → 응축수 회수 | XMV(9), 스팀 유량 XMEAS(19) |
| 재비기 공정측 | 스트리퍼 하부 분기 → 재비기 → 스트리퍼 하부 증기 환류 | 스팀과 공정 유체는 별도 유로 |

배관 번호 뒤의 접미사는 같은 스트림의 배관 구간이나 유틸리티 분기를 식별합니다. 별도 유량 태그가 없는 구간은 방향을 표시합니다. 설비의 치수와 내부 형상은 NIST 배치 예시, PlantSim, 현장 사진을 참고한 관측용 도식입니다.

## 공정 화면

- **배관 추적**에서 재순환, 냉각수, 스팀·재비기를 선택하면 해당 회로가 강조되고 공정도 아래에 전체 연결 순서가 표시됩니다.
- **가스 압력**은 XMEAS(7), (13), (16)의 kPa 게이지압을 3D 모델과 2D 공정도의 설비 옆에 표시합니다. 계장용 압축공기 공급압은 TEP 모델의 계측 범위 밖입니다.
- **내부 전체**에서 반응기, 응축기, 분리기, 스트리퍼, 재비기의 내부를 동시에 볼 수 있습니다. 액면은 모델 값을 따라 움직이고, 파란 점선은 액위 목표입니다. 밸브 스템은 커널의 밸브 위치 상태를 따라 움직입니다.
- **3D 모델**의 전체 보기는 2D 공정도의 설비 배치, 밸브와 배관 경로를 따릅니다. A·D·E는 혼합 헤더에 합류하고 A/C는 별도 배관으로 스트리퍼에 공급됩니다.
- **2D 공정도**에서도 주요 유량·가스 압력, 액면·액위 목표가 표시됩니다. 밸브 기호를 선택해 개도를 조정할 수 있습니다.
- **연결 배관** 패널은 3D·2D 공정도나 실시간 계측에서 선택한 설비의 배관 번호와 흐름 방향을 표시합니다.
- 기본 계측 41개, 확장 계측 32개, 내부 진단 179개와 조작 명령 12개를 포함합니다. 조성 분석은 모델의 갱신 주기와 지연을 유지합니다. XMEASCOMP는 원시 단위인 몰분율로 표시하고 저장합니다.
- **실시간 계측** 표에서 각 채널의 현재값을 확인합니다. 선택한 행에는 수신한 최근 최대 60개 값의 추세가 표시됩니다.

## 조정과 확인

- **SP 목표**에서 9개 제어 루프의 목표값을 보고 각각 슬라이더로 변경할 수 있습니다. 슬라이더를 놓으면 목표값이 적용되고, **SP 응답**에 해당 루프의 목표와 계측값의 추세, 편차, 관련 밸브의 실제 위치와 명령값이 표시됩니다.

- **값 조정**에서 폐루프 목표, 조작 명령 XMV, 모델 외란 IDV, 센서 오프셋, 알람 기준을 바꿀 수 있습니다. 일시정지 중 수정한 XMV와 IDV의 물리 응답은 실행을 재개하면 반영됩니다. 압력 목표 변경은 논문 Table 7 예시인 2705 → 2645 kPa(g)로 재현할 수 있습니다.
- 센서 오프셋 시험 시 실제 계산값은 JSON 샘플의 `native_values`에 기록됩니다. 계측 경고는 값이 정상 범위로 돌아오면 해제되고 모델 보호 정지는 상단 상태 표시에서 확인할 수 있습니다.

## 로컬 기록

페이지를 열거나 새로고침하면 고유한 새 폴더를 만들어 현재 공정 상태부터 기록합니다. 이전 폴더의 기록은 보존됩니다. 공정은 같은 로컬 서버에 연결된 브라우저 탭 사이에서 공유됩니다.

기록마다 `data/<run-id>/`에 아래 파일을 저장합니다. 샘플은 기본적으로 약 0.6초마다 기록하며, 센서 오프셋과 알람 기준 수정 시에도 해당 시점의 스냅샷을 남깁니다.

| 파일 | 내용 |
| --- | --- |
| `telemetry.csv` | 시간, 샘플 번호, 264개 채널 |
| `telemetry.jsonl` | 전체 샘플, SP 9개, 내부 상태 50개, 요청·적용 조작값 및 실제 위치, 제어 진단, 활성 외란과 경과 시간, 제약 여유 및 누적 지표 |
| `latest.json` | 최근 샘플 |
| `events.jsonl` | 조작, 알람, 복구, 확인 및 트립 |
| `schema.json` | 채널명, 단위, 그룹, 샘플링 정보 |
| `scenario.json` | 초기 조건과 조작 이력. 이전 기록에서 내려받을 때 마지막 기록 스텝을 포함합니다. |

화면에서 현재 실행의 CSV와 표준 JSON을 다운로드할 수 있습니다. JSON 내보내기에는 스키마, 샘플, 이벤트가 함께 들어갑니다.

## 출처

- Fig. 1 및 Tables 3–7에 사용된 원 논문. [Downs & Vogel (1993), *A plant-wide industrial process control problem*](https://doi.org/10.1016/0098-1354(93)80018-I)
- 수정된 Tennessee Eastman 계산 커널, 채널 스키마 및 Ricker 제어기. [KhalidLabs TEP Studio, 고정 리비전](https://github.com/khalidlabs/tep-studio/tree/852f0c69ab974540750d61bd1dbfafd56bfd80bb)
- 3D 설비의 외형 참고. [Phygitalism PlantSim](https://phygitalism.com/plantsim_/), [NIST의 Tennessee Eastman 모델](https://www.nist.gov/publications/implementations-tennessee-eastman-process-modelica)
- 2D 화면 참고. [CMU TEP Studio 공정도](https://kitchingroup.cheme.cmu.edu/tep-rust/studio/)
