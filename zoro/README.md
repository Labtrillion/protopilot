# ProtoPilot — Zoro-1 판단 에이전트 (MVP)

화학 공정 시뮬레이터에서 복합 알람이 발생했을 때 다음 순서로 동작하는 루프입니다.
SOP 근거(RAG) → 허용 선택지 구성 → Zoro-1 Choice 판단 → 등록 도구 실행 → 상태 갱신 → 재판단 → 권고 또는 운영자 검토.
최종 출력은 TEP Studio 감독기에 넘길 **설정값 명령**(`hold` 또는 `propose_setpoint`: 대상 루프·목표값·근거 SOP·확인 신호·재검토 시각·원복 조건)입니다.
독립 데모는 저장된 RAG 출력과 가짜 도구를 사용합니다. 라이브 연동은 서버 SOP를 근거로 시뮬레이터 분기를 실행합니다.

> ⚠️ 예시 시나리오·문서·수치는 모두 **가상**입니다. 정책 값은 예시 설정이며, 실제 공정에서 검증된 기준이 아닙니다.

## 폴더 안내

| 경로 | 역할 |
|---|---|
| [`zoro_agent/`](zoro_agent/) | Zoro-1 모델 호출, 대응 후보 구성, 도구 실행 및 결과 비교, 최종 권고 |
| [`rag_jiyeon/`](rag_jiyeon/README.md) | 경보 JSON에서 문헌과 SOP를 찾는 독립 실행형 검색기 |
| [`rag_jiyeon/legacy/`](rag_jiyeon/legacy/README.md) | 이전 증상 검색기와 Studio 평가 코드, 검증 CSV, 팀 SOP 원문 |
| [`docs/`](docs/) · [`examples/`](examples/) | 입출력 명세·스키마와 에이전트 실행 예제 |

라이브 `/api/zoro-agent` 요청에서는 `server/zoro_bridge.py`가 [서버 데모 SOP](../server/demo-sops.json)를 에이전트 입력으로 변환합니다. `--rag jiyeon` 데모는 검색기가 미리 생성한 결과 JSON을 `JiyeonRagAdapter`로 변환합니다.

## 실행

```bash
cd zoro
pip install "pydantic>=2.6" pytest
python examples/run_demo.py          # 가짜 Zoro + 가짜 도구, 결과는 examples/output/
python examples/run_demo.py --rag jiyeon   # 지연님 RAG 실제 출력(rag_jiyeon_result.json)으로 실행
python scripts/build_tep_studio_sop.py     # Studio 제어 코드 → 센서별 SOP (rag_jiyeon 지식 + docs/SOP_TEP_STUDIO.md)
python -m pytest -q                  # 핵심 테스트
python scripts/export_schemas.py     # docs/schemas/*.schema.json, docs/examples/*.json 재생성
python scripts/benchmark_pair.py rag_jiyeon/legacy/validation/nominal.csv rag_jiyeon/legacy/validation/cooling_combined.csv  # 심슨 vs Zoro 짝비교
```

실제 Zoro-1 서버 연결(도구는 여전히 가짜):

```bash
# trida-stack: python serve.py --mode decide --port 30001
export ZORO_MODE=http ZORO_BASE_URL=http://localhost:30001   # 필요 시 ZORO_API_KEY, ZORO_REASONING=false|true|auto
python examples/run_demo.py
```

비밀값은 코드에 넣지 않습니다(`.env.example` 참조). 결과의 `model_client_kind`(real/fake)와 `tools_virtual` 로 실제 호출 여부를 표시합니다.

## ProtoPilot 시뮬레이터 연결

저장소 루트의 시뮬레이터 서버가 알람마다 저장하는 체크포인트(모델·난수·제어기 메모리·수동 조작·활성 외란)에서
에이전트가 고른 case 를 분기 실행합니다. 연결 코드는 `server/zoro_bridge.py` 입니다.

```sh
# 서버 실행 후 (저장소 루트 README 참고)
curl -X POST localhost:8765/api/command -H 'Content-Type: application/json' -d '{"op":"cooling_demo"}'
curl -X POST localhost:8765/api/zoro-agent -H 'Content-Type: application/json' -d '{"run":"<run>","alarm_id":<id>}'
```

- `ZORO_BASE_URL` 이 있으면 Zoro-1(`/v1/decide`), 없으면 고정 순서 규칙(결과에 `model_client_kind: fake`).
- case 는 알람 시점 설정값과 팀 SOP 후보(유지·즉시/30분/60분 후 5% 감량)로 사건마다 생성합니다.
- 활성 외란은 시뮬레이터 안에만 있고 에이전트 입력·결과에는 들어가지 않습니다.
- 주의: `docs/SOP_TEP_STUDIO.md` 와 `tep_control.py` 는 CMU TEP Studio(Braatz, SETPT(19) 퍼지 B) 기준입니다.
  ProtoPilot 시뮬레이터는 Ricker 제어기(설정값 9개: 반응기 압력·온도·액위, 생산량, 제품 G, ya/yac)라 그대로 적용되지 않습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `zoro_agent/inputs.py` | A: 사건·관측·알람·안전 검사 |
| `zoro_agent/rag.py` | 검색 결과 공통 형식과 `JiyeonRagAdapter`, `ExampleRagV0Adapter` |
| `zoro_agent/sim_tools.py` | **실제 시뮬레이터 어댑터** `SimulationToolkit`: ToolRequest → 분기 실행(BranchRunner) → 채점 → ToolResult. FakeToolkit 자리에 등록 |
| `zoro_agent/protopilot_cases.py` | ProtoPilot 시뮬레이터(tep-studio, Ricker 제어기)용 사건별 카탈로그·벤치마크·정책, 팀 SOP(`server/demo-sops.json`) 근거 변환 |
| `zoro_agent/tep_control.py` | CMU TEP Studio(tep-rust, Braatz) 제어 로직 이식. **ProtoPilot 시뮬레이터(Ricker)와는 제어 구조가 다르다** — 아래 참고 |
| `docs/SOP_TEP_STUDIO.md` | 생성된 센서별 대응 SOP 표 |
| `zoro_agent/benchmark.py` | 심슨(SOP 단독) 대비 비용(USD/m³)·정확도 채점, 비교 규칙, case 탐색 종료 규칙 — `docs/BENCHMARK.md` |
| `zoro_agent/alarm_adapter.py` | 경보 원본(`zoro-alarm-event-v1`) → `EventInput` |
| `zoro_agent/cases.py` | 대응 case·허용 설정값(SETPT)·시뮬레이터 case·허용 범위 검사 |
| `zoro_agent/registry.py` | 행동·조건·case 카탈로그, 정확 일치·합의 별칭 매핑만 허용 |
| `zoro_agent/tool_io.py` | C: `ToolRequest` / A: `ToolResult`, 실험·영향·비교 타입 |
| `zoro_agent/state.py` | 내부 상태, 신선도, 인자 치환, 결과 반영 규칙, 비교 가능성 판정 |
| `zoro_agent/options.py` | 선택지 구성기 (pass/fail/unknown → open/deferred/excluded) |
| `zoro_agent/zoro.py` | B: Zoro 요청 생성, HTTP·가짜·스크립트 클라이언트, 원본 응답 → `Decision` |
| `zoro_agent/agent.py` | 판단 반복 실행부, 종료 조건, 최종 출력 구성 |
| `zoro_agent/outputs.py` | C: 로그·진행 상태·최종 출력 |
| `zoro_agent/tools.py` | 도구 실행기 + 시나리오 기반 가짜 도구 |
| `examples/scenario_tep_purge/` | TEP 반응기 압력 상승 + 퍼지 분석 시료 지연 → 퍼지 B 목표(SETPT(19)) case 비교 (가상) |
| `docs/IO_SPEC.md` | **팀 공유용 입출력 명세** (필드·단위·시간 기준·null 의미·팀원별 필요 데이터) |

## 데모 흐름 (가짜 Zoro, 가짜 점수)

1. 퍼지 분석값 XMEAS(30)이 1500 s 전 시료라 case 시뮬레이션이 **보류**됩니다. `XMV(6) 100% 수동 개방`(미등록 밸브 명령)과 생산 목표 변경(허용 조작 아님)은 **제외**됩니다.
2. 퍼지 분석 조회 → 같은 시료가 다시 와서 **정보 갱신 실패**. 다시 조회 → 새 시료 반영, 시뮬레이션이 **열립니다**.
3. 심슨(hold) 기준 실험 후 SETPT(19) case 를 ±0.5 간격으로 시험합니다. 각 요청에는 `case`(Scheme::set_setpoint 로 적용할 변경)와 `evaluation`(채점 명세)이 붙습니다.
4. DN050(13.323 mol%)이 심슨을 이긴 뒤 4개 case 가 연속으로 이기지 못해 **탐색 종료**. UP200·DN200 은 시험하지 않습니다.
5. 권고 → `setpoint_command = propose_setpoint SETPT(19) 13.823→13.323 mol%`, `case_selection` 에 시험 순서별 판정이 남습니다.
