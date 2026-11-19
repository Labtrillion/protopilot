# ProtoPilot TEP 시뮬레이터

Tennessee Eastman Process의 Mode 1을 로컬에서 실행하는 Three.js 관제 화면입니다. 상단에서 공정의 배관과 유량·가스 압력을 보고, 하단에서 계산된 전체 채널과 실시간 알람을 확인할 수 있습니다.

## 실행

Python 3.13, Node.js, npm, Git 및 Python 패키지의 C 확장 빌드에 필요한 컴파일러가 필요합니다. macOS에서는 Xcode Command Line Tools의 clang을 사용했습니다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
npm start
```

서버 실행 후 브라우저에서 [시뮬레이터 열기](http://127.0.0.1:8765/). 실행 시작 시 새 기록 폴더를 만들며, 화면의 초기화 버튼은 Mode 1의 초기 상태에서 **일시정지된 새 실행**을 만듭니다. 이전 기록은 남습니다.

## 공정과 계측

- Fig. 1의 원료 4계통, 반응기, 응축기, 기액 분리기, 재순환 압축기, 스트리퍼, 재비기, 2개 펌프, 3개 분석기, 냉각수 및 스팀을 연결했습니다. 배관 번호는 도면을 따릅니다. 영문 접미사가 붙은 표기는 같은 도면 스트림의 구간 또는 유틸리티 분기를 식별합니다.
- **유량**의 화살표 속도는 연결된 계측값을 기준 유량과 비교한 상대적 표현입니다. 별도 유량 태그가 없는 구간에는 방향을 표시합니다. **가스 압력**은 XMEAS(7), (13), (16)의 kPa 게이지압을 설비와 연결 구간에 표시합니다. 계장용 공기 공급압은 TEP 모델의 계측 범위 밖입니다.
- 제조 설비의 형상은 `references/`의 NIST 배치 예시와 PlantSim, 현장 사진을 참고해 재구성했습니다. 크기와 내부 구조는 관측용 도식입니다. 설비 내부 보기에서 냉각 코일과 교반기, 기액 분리기 액면, 스트리퍼 내부를 확인할 수 있습니다.
- 기본 계측 41개, 확장 계측 32개, 내부 진단 179개와 조작 명령값 12개를 포함합니다. 조성 분석은 원래 모델의 0.1 h 또는 0.25 h 갱신 주기와 지연을 유지합니다. XMEASCOMP의 원시값은 몰분율 단위로 표시하고 저장합니다.

## 조정과 확인

- **값 조정**에서 폐루프 목표, 조작 명령 XMV, 모델 외란 IDV, 센서 오프셋, 알람 기준을 바꿀 수 있습니다. 일시정지 중 수정한 XMV와 IDV의 물리 응답은 실행을 재개하면 반영됩니다. 압력 목표 변경은 논문 Table 7 예시인 2705 → 2645 kPa(g)로 재현할 수 있습니다.
- **압력 알람 테스트**는 반응기 압력 표시값에 오프셋을 적용해 Table 6의 정상 운전 상한을 넘깁니다. 실제 계산값은 JSON 샘플의 `native_values`에 함께 기록됩니다. 실제 모델 보호 정지는 TEP 커널의 트립을 받아 따로 표시합니다. Chrome에서 알람 소리를 들으려면 벨 옆의 음량 버튼을 먼저 누릅니다.
- 알람을 확인하면 이력에 확인 이벤트가 추가됩니다. 알람은 값이 정상 범위로 돌아올 때 활성 목록에서 해제됩니다. 센서 태그, 알람 제목 또는 설비 이름을 눌러 관련 모델로 이동할 수 있습니다.

## 로컬 기록

실행마다 `data/<run-id>/`에 아래 파일을 저장합니다. 샘플은 기본적으로 약 0.6초마다 기록하며, 센서 오프셋과 알람 기준 수정 시에도 해당 시점의 스냅샷을 남깁니다.

| 파일 | 내용 |
| --- | --- |
| `telemetry.csv` | 시간, 샘플 번호, 264개 채널 |
| `telemetry.jsonl` | 전체 샘플, 오프셋 시험의 원계측값, 활성 외란 |
| `latest.json` | 최근 샘플 |
| `events.jsonl` | 조작, 알람, 복구, 확인 및 트립 |
| `schema.json` | 채널명, 단위, 그룹, 샘플링 정보 |

화면에서 현재 실행의 CSV와 표준 JSON을 다운로드할 수 있습니다. JSON 내보내기에는 스키마, 샘플, 이벤트가 함께 들어갑니다.

## 출처

- Fig. 1 및 Tables 3–7에 사용된 원 논문. [Downs & Vogel (1993), *A plant-wide industrial process control problem*](https://doi.org/10.1016/0098-1354(93)80018-I)
- 수정된 Tennessee Eastman 계산 커널, 채널 스키마 및 Ricker 제어기. [KhalidLabs TEP Studio, 고정 리비전](https://github.com/khalidlabs/tep-studio/tree/852f0c69ab974540750d61bd1dbfafd56bfd80bb)
- 3D 설비의 외형 참고. [Phygitalism PlantSim](https://phygitalism.com/plantsim_/), [NIST의 Tennessee Eastman 모델](https://www.nist.gov/publications/implementations-tennessee-eastman-process-modelica)
