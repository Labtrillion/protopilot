# ProtoPilot — Zoro-1 판단 에이전트 (MVP)

화학 공정 시뮬레이터에서 복합 알람이 발생했을 때 다음 순서로 동작하는 루프입니다.
SOP 근거(RAG) → 허용 선택지 구성 → Zoro-1 Choice 판단 → 등록 도구 실행 → 상태 갱신 → 재판단 → 권고 또는 운영자 검토.
최종 출력은 TEP Studio 감독기에 넘길 **설정값 명령**(`hold` 또는 `propose_setpoint`: 대상 루프·목표값·근거 SOP·확인 신호·재검토 시각·원복 조건)입니다.
RAG와 시뮬레이터는 **어댑터와 가짜 도구**로 대체했으므로 이 파트만 독립적으로 개발·검증할 수 있습니다.

> ⚠️ 예시 시나리오·문서·수치는 모두 **가상**입니다. 정책 값은 예시 설정이며, 실제 공정에서 검증된 기준이 아닙니다.

## 실행

```bash
pip install "pydantic>=2.6" pytest
python examples/run_demo.py          # 가짜 Zoro + 가짜 도구, 결과는 examples/output/
python examples/run_demo.py --sop-only --rag jiyeon  # SOP 대응안 한 가지 선택, 시뮬레이션 없음
python examples/run_demo.py --rag jiyeon   # 지연님 RAG 실제 출력(rag_jiyeon_result.json)으로 실행
python scripts/build_tep_studio_sop.py     # Studio 제어 코드 → 센서별 SOP (rag_jiyeon 지식 + docs/SOP_TEP_STUDIO.md)
python -m pytest -q                  # 핵심 테스트
python scripts/export_schemas.py     # docs/schemas/*.schema.json, docs/examples/*.json 재생성
python scripts/benchmark_pair.py zoro-rag/validation/nominal.csv zoro-rag/validation/cooling_combined.csv  # 심슨 vs Zoro 짝비교
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

- `ZORO_BASE_URL`이 있으면 `/v1/systemone`을 사용하고, 이전 서버의 경우 `/v1/decide`로 재시도합니다. 가짜 클라이언트 실행은 `model_client_kind: fake`로 표시합니다.
- `build_policy()`가 만드는 ProtoPilot 정책은 먼저 알람·계측·SOP·지식을 바탕으로 `case1` 하나를 선택합니다. `Choice`로 대응·센서 정보 요청·재검색·운영자 검토를 선택하고, 선택된 대응의 `Noul` 적용 가능성과 `Score` 근거 충실도를 확인합니다. 결과에 `sop_route`, `sop_sources`, `setpoint_command`가 포함됩니다. SOP 버전, 등록 전제조건, 필수 센서 및 안전 검사는 코드에서 확인합니다.
- 벤치마크가 연결된 실행은 `case1`을 먼저 시험한 뒤, 같은 체크포인트에서 목표값을 한 번에 하나씩 변형해 비교합니다. 기본 후보 간격은 목표값의 `−0.5%, +0.5%, −1%, +1%`이며 `setpoint_variant_steps_pct`에서 설정합니다. 유지가 `case1`이면 변경할 값이 없어서 기준만 시험합니다.
- 비용과 추종 정확도는 `SimulationToolkit`이 시뮬레이션 시계열을 `EvaluationSpec`에 따라 채점합니다. 첫 단계의 결과와 모델 응답은 `RunReport.sop_stage`에 남으며, 단독 SOP 실행은 `ZoroAgent.run_sop()`를 사용합니다. 재검색을 실행하려면 호출자가 `retrieve_again` 콜백을 공급합니다.
- 활성 외란은 시뮬레이터 안에만 있고 에이전트 입력·결과에는 들어가지 않습니다.
- 주의: `docs/SOP_TEP_STUDIO.md` 와 `tep_control.py` 는 CMU TEP Studio(Braatz, SETPT(19) 퍼지 B) 기준입니다.
  ProtoPilot 시뮬레이터는 Ricker 제어기(설정값 9개: 반응기 압력·온도·액위, 생산량, 제품 G, ya/yac)라 그대로 적용되지 않습니다.

## 구조

| 파일 | 역할 |
|---|---|
| `zoro_agent/inputs.py` | A: 사건·관측·알람·안전 검사 |
| `zoro_agent/rag.py` | A: 정규화된 RAG 타입 + `ExampleRagV0Adapter` |
| `zoro_agent/sim_tools.py` | **실제 시뮬레이터 어댑터** `SimulationToolkit`: ToolRequest → 분기 실행(BranchRunner) → 채점 → ToolResult. FakeToolkit 자리에 등록 |
| `zoro_agent/protopilot_cases.py` | ProtoPilot 시뮬레이터(tep-studio, Ricker 제어기)용 사건별 카탈로그·벤치마크·정책, 팀 SOP(`server/demo-sops.json`) 근거 변환 |
| `zoro_agent/tep_control.py` | CMU TEP Studio(tep-rust, Braatz) 제어 로직 이식. **ProtoPilot 시뮬레이터(Ricker)와는 제어 구조가 다르다** — 아래 참고 |
| `rag_jiyeon/` | 지연님 RAG(검색 엔진). 정답 누출 차단·경보 태그 기반 SOP 후보·v1 경보 형식 등 수정, 센서별 SOP 추가 — `rag_jiyeon/README.md` |
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
| `zoro-rag/` | 이전 RAG 패키지. 비용식·평가기(`studio_eval.py`)와 팀 SOP 원문만 참고용으로 사용 |
| `docs/IO_SPEC.md` | **팀 공유용 입출력 명세** (필드·단위·시간 기준·null 의미·팀원별 필요 데이터) |

## 데모 흐름 (가짜 Zoro, 가짜 점수)

1. 퍼지 분석값 XMEAS(30)이 1500 s 전 시료라 case 시뮬레이션이 **보류**됩니다. `XMV(6) 100% 수동 개방`(미등록 밸브 명령)과 생산 목표 변경(허용 조작 아님)은 **제외**됩니다.
2. 퍼지 분석 조회 → 같은 시료가 다시 와서 **정보 갱신 실패**. 다시 조회 → 새 시료 반영, 시뮬레이션이 **열립니다**.
3. 심슨(hold) 기준 실험 후 SETPT(19) case 를 ±0.5 간격으로 시험합니다. 각 요청에는 `case`(Scheme::set_setpoint 로 적용할 변경)와 `evaluation`(채점 명세)이 붙습니다.
4. DN050(13.323 mol%)이 심슨을 이긴 뒤 4개 case 가 연속으로 이기지 못해 **탐색 종료**. UP200·DN200 은 시험하지 않습니다.
5. 권고 → `setpoint_command = propose_setpoint SETPT(19) 13.823→13.323 mol%`, `case_selection` 에 시험 순서별 판정이 남습니다.
