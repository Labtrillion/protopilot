# Zoro-1 에이전트 입출력 명세 (v1, 팀 공유용)

> 담당: Zoro 입력·출력 / 허용 선택지 구성 / 도구 실행 연결 / 상태 갱신·재판단
> 상태: **해커톤 MVP 초안**. 수치·조건·ID는 모두 **예시 설정**이며, 실제 공정에서 검증된 기준이 아닙니다.
> 타입 원본: `sop_ranking/*.py` (Pydantic v2) · JSON Schema: `docs/schemas/` · 예시 JSON: `docs/examples/`

---

## 0. 공통 규약

| 항목 | 규약 |
|---|---|
| 시간 기준 | `*_s` = **시뮬레이션 시각(초)**. `sim_time_s`, `measured_at_s`, `raised_at_s`, `evaluated_at_s` 는 시뮬레이션 시작 기준 **절대 시각**입니다. 실험 결과 안의 `first_exceeded_at_s`, `recovery_time_s`, `t_start_s/t_end_s` 는 **run 시작(= snapshot 시점) 기준 상대 시각**입니다. 벽시계(UTC ISO-8601)는 로그 `logged_at` 에만 씁니다. |
| 단위 | 값 옆에 `unit` 문자열을 함께 둡니다(`kPa(g)`, `degC`, `mol_frac`, `t/h`, `t`). 단위가 다른 지표끼리는 비교하지 않습니다. |
| null | "없음/모름"을 뜻합니다. 0, false, pass 로 바꾸지 않습니다. 필드별 의미는 아래 표에 적었습니다. |
| 미정의 필드 | 모든 스키마는 `extra="forbid"` 입니다. 정의되지 않은 필드는 거부합니다(숨은 정답·오타 유입 방지). 예외: Zoro 응답 `ChoiceAnswer` 는 공식 필드 추가에 대비해 무시합니다. |
| 실제 vs 가상 | 실제 관측은 `Observation(origin="plant_measurement")` 만 씁니다. 시뮬레이션 결과는 `SimulationRun`/`ImpactResult(kind="simulation_prediction")` 입니다. 상태의 다른 필드에 저장하고, 모델 입력에서도 `actual_observations` 와 `virtual_experiments` 로 나눕니다. |
| 금지 입력 | `fault_id`, `hidden_cause`, `fault_schedule`, `ground_truth` 등은 입력 단계(`extra=forbid`)와 Zoro 요청 직전 키 검사(`LeakError`)에서 두 번 막습니다. 평가용 정답은 `evaluation_only.json` 에만 두고, 로더는 이 파일을 읽지 않습니다. |
| 조건 검사 | `pass` / `fail` / `unknown` 세 값입니다. **unknown 은 절대 pass 가 아닙니다.** |
| ID 연결 | `decision_id = {event_id}-D{nn}` → `request_id = {decision_id}-ZQ` → `tool_request_id = {decision_id}-T1` → `run_id` (도구가 부여). 로그는 이 ID들로 연결됩니다. |

---

## 1. 전체 흐름

```
EventInput + RAG 원본 ─(RagAdapter)→ NormalizedRagResult
        └──────────────→ AgentState(state_version)
loop:
  한도 확인 (판단 횟수·벽시계) ─ 도달 → 종료(limit_reached)
  OptionSet = build_options(state, catalog, policy, 연결된 도구)
      open 중 운영자 검토 외 선택지 없음 → 정책 결정으로 운영자 검토 (no_valid_options / limit_reached)
  ZoroRequest(state 요약 + open 선택지만 criteria) → Zoro → ZoroRawResponse(원본 보존)
  to_decision: 형식·허용 목록·분포 키·옵션 상태 재검증 → Decision
      무효 → 운영자 검토 (model_response_invalid)
      recommendation → 최종 권고 (recommendation_ready)
      human_review   → 운영자 검토 (human_review_selected)
      tool_call      → ToolRequest(등록된 args_template 로만 구성) → ToolExecutor
  apply_tool_result: 성공 + 새 정보일 때만 반영, state_version +1, 이력 기록
  assess_comparison: 기준/후보 비교 가능성 재판정
      복구 불가 도구 오류 → 운영자 검토 (unrecoverable_tool_error)
```

모델 호출 횟수는 고정하지 않습니다. 매 판단은 **독립 요청**입니다. 앞선 도구 결과가 필요한 판단은 다음 요청에서 따로 묻습니다.

---

## 2. 경계 A — 외부 모듈 → 에이전트

### A-1 `EventInput` (장하·영하 → 나) — `docs/examples/A1_event_input.json`

| 필드 | 필수 | 설명 / null 의미 |
|---|---|---|
| `event_id` | ✔ | 사건 ID |
| `state_version` | ✔ | 백엔드 상태 버전(≥0). 에이전트는 도구 결과를 반영할 때마다 +1 |
| `sim_time_s` | ✔ | 현재 시뮬레이션 시각 [s] |
| `snapshot_id` | ✔ | **대응 실험의 공통 비교 출발점**. 모든 대응 시뮬레이션은 이 snapshot 에서 시작해야 비교할 수 있습니다 |
| `alarms[]` | ✔ | `alarm_id, sensor_id, condition, raised_at_s, active, priority?, message?, threshold?`(경보 임계값 — 정확도 계산에 사용), `unit?, raw_tag?`. 원본 경보 형식 `zoro-alarm-event-v1` 은 `alarm_adapter.adapt_alarm_event` 로 변환(`docs/BENCHMARK.md` §5) |
| `observations[]` | ✔ | 최근 관측 이력(센서별 여러 점 가능). `value`: null = 결측. `measured_at_s`: null = 시각 모름 → 신선도 unknown. `quality`: good/uncertain/bad. `sample_id`: 분석 시료 ID(같은 시료 재전달 판별용), null = 미제공 |
| `safety_checks[]` | | **외부 규칙 엔진 결과**. 에이전트가 계산하지 않습니다. `status` pass/fail/unknown, `evaluated_at_s` null = 미평가 |
| `missing_info[]` | | 누락·미수신 정보. `sensor_id` 가 있고 그 센서에 새 측정이 반영되면 해소됩니다 |
| `active_setpoints[]` | | **현재 적용 중인 상위 목표** `setpoint_id, value, unit, since_s`(null = 적용 시각 모름). 없으면 설정값 변경 case 는 보류됩니다 |

**신선도 판정**(정책 `freshness`): 관측 없음 / 값 null / quality=bad → fail. 시각 null → unknown. `sim_time_s - measured_at_s > max_age` → fail. quality=uncertain → unknown. 나머지 → pass.

### A-2 RAG 결과 (지연 → 나) — 원본은 어댑터로 `NormalizedRagResult` 로 정규화

원본 형식은 바뀔 수 있으므로 `RagAdapter.normalize(raw, catalog)` 만 교체하면 됩니다. **지연님 RAG 출력(`zoro_rag.AlarmResult`)은 `JiyeonRagAdapter`** 가 변환합니다: 근거(`source_version` 포함)를 옮기고, SOP 계획의 `candidates`(catalog_action_id)·`baseline` 만 후보로 만들며, `alarm_context`(시뮬레이터 정보)는 가져오지 않습니다. 예: `examples/scenario_tep_purge/rag_jiyeon_result.json`. 현재 원본 예시는 `A2_rag_raw_example_v0.json`, 정규화 결과는 `A2_rag_normalized.json` 입니다.

| 타입 | 필드 |
|---|---|
| `Evidence` | `evidence_id`, `doc_id`, `doc_version`(null = 출처 미제공 → 불확실성으로 보고), `doc_title?`, `clause`(null = 미제공), `text`(원문), `source_uri?`, `retrieval_score`(**검색 관련도일 뿐. 실행 가능성·안전성으로 쓰지 않음**. 선택지 구성기는 이 값을 읽지 않습니다) |
| `RagCandidate` | `raw_ref`(RAG 원래 문자열), `action_id`(null = 등록 행동과 매핑 안 됨), `mapping` exact/alias/unmapped, `purpose`, `evidence_ids`, `condition_ids`(등록 조건으로 매핑됨), `unmapped_condition_refs`(→ unknown 처리) |
| `UnresolvedItem` | 검색만으로 판단할 수 없는 내용 `topic, detail, evidence_ids` |
| `warnings[]` | 정규화 중 발견한 문제(근거 참조 누락, 버전 없음, 매핑 실패) |

**매핑 규칙**: 카탈로그와 **정확히 일치**하거나 **팀이 합의한 별칭표**(`aliases`, `condition_aliases`)에 있을 때만 매핑합니다. 유사도나 추측으로 매핑하지 않습니다. 매핑되지 않은 후보는 `UNMAPPED:<raw_ref>` 로 남기고 항상 **제외**합니다.

### A-3 행동·도구 카탈로그 `ActionCatalog` (정민·장하 정의 → 나) — `A3_action_catalog.json`

| 필드 | 설명 |
|---|---|
| `action_id` | 모델이 고르는 ID. 실행은 이 ID → 등록 도구로만 연결됩니다 |
| `kind` | `sensor_query` / `relation_query` / `simulation` / `human_review` |
| `tool_name` | 실행 도구 이름. `ToolExecutor` 에 등록돼 있어야 실제로 지원되는 것으로 봅니다 |
| `supported` | 실행 기능이 구현·연결됐는지. false 이면 제외 |
| `case_id` | simulation 이 시험할 **등록 대응 case**(아래 A-3a). 지원되는 simulation 은 필수입니다. 모델은 설정값·밸브값을 만들지 않습니다 |
| `args_template` | 도구 인자 틀. 치환자는 `$snapshot_id`, `$event_id`, `$latest_sample_id:<sensor>`, `$latest_value:<sensor>` 만 허용합니다 |
| `preconditions[]` | 조건 ID. 조건 종류: `sensor_fresh`, `safety_check`, `run_succeeded` |
| `required_fresh_sensors[]` | 이 센서들이 신선해야 실행 가능 |
| `target_sensors[]` | sensor_query 대상. 대상이 모두 신선하면 조회 선택지를 닫습니다 |
| `rerun` | `scope`(per_snapshot: 같은 snapshot 에서 완료되면 재실행 금지 / unlimited), `retry_after_failure`, `max_runs` |
| `requires_rag_evidence` | RAG 근거가 없으면 제외 |
| `offer_without_rag` | RAG 후보가 없어도 기본 제공(기준 실험·조회·운영자 검토) |
| `recommendable` | 이 실험을 근거로 `RECOMMEND:<action_id>` 권고 선택지를 만들 수 있는지 |

### A-3a 대응 case 와 허용 설정값 (`ActionCatalog.setpoints`, `.cases`)

TEP Studio 계약(`rag/legacy/STUDIO.md` "Zoro 입력, 출력, 실행 경계")에 맞춘 구조입니다. Zoro 는 **hold 또는 propose_setpoint** 를 내고, 하위 P·PI 제어와 보호 기능은 그대로 둔 채 **상위 목표만** 바꿉니다. Zoro-1 은 선택형 모델이므로 숫자를 만들지 않습니다. 목표값은 모두 여기 등록된 case 에서 나오고, Zoro 는 그중 무엇을 시험·권고할지 고릅니다.

`SetpointSpec` (감독기 허용 조작 목록 = STUDIO 의 `allowed_actions`)

| 필드 | 설명 |
|---|---|
| `setpoint_id` | 예 `SETPT(19)` |
| `scheme_index` | Studio `Scheme::set_setpoint(index, value)` 의 index |
| `loop_id`, `controlled_tag` | 대상 상위 루프와 추종 계측. 예 `CONTRL19`, `XMEAS(30)` |
| `unit`, `default_value` | 단위와 Studio PRESET 기동값 |
| `allowed_min/max`, `max_step`, `max_rate_per_h`, `min_wait_s` | 허용 범위, 현재값 대비 1회 최대 변경폭, 램프 속도(null = 계단 허용), 직전 변경 후 최소 대기(null = 제한 없음) |
| `values_status` | `example` / `agreed`. 지금은 전부 example |

`ResponseCase`

| 필드 | 설명 |
|---|---|
| `case_id`, `policy` | `hold`(변경 없음 = SOP 단독 기준선) / `propose_setpoint`(변경 1건 이상) |
| `setpoint_changes[]` | `setpoint_id, target_value, unit, apply_at_s`(run 시작 기준), `ramp_rate_per_h`(null = 계단) |
| `sop_refs[]` | 근거 SOP 단계 `sop_id, version, step_id` |
| `watch_signals[]`, `review_after_s` | 적용 후 확인할 신호, 재검토까지 시간 |
| `revert_rules[]` | 원복 조건 `rule_id, description, signal`(null = 이벤트형), `op, threshold, unit` |
| `expected_cost_direction` | purge/product/compressor/steam 별 increase/decrease/no_change/unknown. **case 등록자의 사전 가설**이며 시뮬레이션·실측 결과가 아닙니다. 근거는 `expected_direction_basis` |

**카탈로그 검증**(로드 시 거부): 허용 목록에 없는 설정값(예 `XMV(6)` 직접 명령), 단위 불일치, 허용 범위 밖 목표, 램프 속도 초과, hold 에 변경 있음 / propose 에 변경 없음, case 없는 지원 simulation.
**실행 시 검사**(`setpoint_envelope`): 현재값 미제공 → 보류, 현재값 대비 변경폭 초과·이미 같은 값 → 제외, 직전 변경 후 대기 미충족 → 보류. 권고 선택지에도 같은 검사를 다시 적용합니다.

### A-4 도구 결과 `ToolResult` (장하·동은 → 나) — `A4_tool_result_*.json`

| 필드 | 설명 |
|---|---|
| `tool_request_id` | 요청과 같은 ID여야 합니다(다르면 거부) |
| `status` | `succeeded` / `failed` / `aborted` / `running` |
| `payload` | `kind` 로 구분: `sensor_query{observations[], unavailable_sensor_ids[]}`, `relation_query{edges[]}`, `simulation{run, impact?}` |
| `error` | `code, message, recoverable`. recoverable=false → 반복 종료, 운영자 검토 |
| `finished_sim_time_s` | 실제 공정 조회가 끝난 시각. 가상 실험은 공정 시간을 진행시키지 않으므로 null |
| `is_virtual` | 가짜 도구 또는 가상 결과이면 true |

`SimulationRun`: `run_id, snapshot_id, action_id, case_id, conditions, status, timeseries_ref(시계열 저장 위치, null = 없음), error`
`ExperimentConditions` (**비교 키**): `snapshot_id, horizon_s, simulator_version, disturbance_profile_id, seed`(null = 미제공), `initial_composition_sample_id`. 하나라도 다르면 동일 조건 A/B 비교가 아닙니다.
`ImpactResult` (동은): `run_id, original_alarm_recovered`(null = 판단 불가), `recovery_time_s`(null = 회복 안 됨/판단 불가), `affected_equipment[], affected_sensors[], exceedances[{sensor_id, equipment_id, limit_name, limit_value, unit, first_exceeded_at_s, duration_s, max_excess}], metrics{name: {value, unit, better}}, evidence_spans[{run_id, sensor_id, t_start_s, t_end_s}]`
`ImpactResult.score` (동은, `RunScore`): 벤치마크 점수 — 비용 USD/m³, 정확도, 게이트. 정의와 수치는 **`docs/BENCHMARK.md`**.
`ExternalComparison` (동은, 선택): `baseline_run_id, run_ids, comparable, reasons_not_comparable`. 에이전트 자체 판정과 **AND** 로 결합하므로 외부 판정이 비교 가능하다고 해도 자체 조건 검사를 덮어쓰지 않습니다.

**상태 반영 규칙**
- `status != succeeded` → 관측·영향을 반영하지 않습니다(실패한 시뮬레이션은 run 상태만 기록하고 영향 결과는 만들지 않음).
- 조회 성공이라도 `measured_at_s` 가 기존보다 새롭지 않거나 같은 `sample_id` 이면 갱신하지 않습니다 → `info_updated=false`. **조회 성공 ≠ 정보 갱신 성공**입니다.
- 요청하지 않은 센서, 값 null, quality bad, 시각 null, 미래 시각 → 반영하지 않습니다.
- run 의 `action_id`/`snapshot_id` 가 요청과 다르면 반영하지 않습니다.

---

## 3. 선택지 구성 (`build_options`)

입력: 정규화된 RAG 후보 + 카탈로그 + 현재 상태 + 실행 이력 + 연결된 도구 목록.
선택지 종류: 도구 호출(조회·관계·시뮬레이션), `RECOMMEND:<action_id>`, `REQUEST_HUMAN_REVIEW`.

| 검사 | 막힘 | 결과 |
|---|---|---|
| registered | hard | 미등록이면 제외 |
| supported | hard | `supported=false` 이거나 도구가 연결되지 않았으면 제외 |
| condition:\<id\> | soft | fail/unknown → 보류 |
| input_fresh:\<sensor\> | soft | 오래된 값 → 보류 |
| query_needed | hard | 대상이 모두 최신이면 조회 제외 |
| not_duplicate | 진행 중 soft / 완료·최대 횟수 hard | |
| budget | hard | 도구 호출·시뮬레이션 한도 |
| rag_evidence | hard | 근거가 필요한데 없으면 제외 |
| setpoint_envelope | 변경폭 초과 hard / 현재값·대기 미확인 soft | simulation case 의 허용 범위 검사 |
| args_resolvable | soft | 인자 구성에 필요한 값이 없으면 보류 |

**상태**: hard 검사에 fail 이 하나라도 있으면 `excluded`. soft fail 또는 unknown 이 있으면 `deferred`. 모두 pass 이면 `open`. 모든 선택지에 검사별 `status/detail` 과 `reason` 을 기록합니다.

**최종 권고 허용 정책** (`policy.recommendation`, 예시 값): 아래 조건을 **모두** 만족해야 `RECOMMEND:X` 가 열립니다.
1. X 실험이 현재 snapshot 에서 성공
2. 기준 실험(`baseline_action_id`) 성공. 이것이 **필수 비교 대상**입니다
3. 기준과 동일 조건으로 비교 가능한 후보 ≥ `min_candidate_runs`
4. X 가 기준과 비교 가능(조건 불일치 → 제외)
5. 원래 알람 회복 예측(`require_alarm_recovery`), 한계 초과 0건(`block_if_limit_exceedance`)
6. `required_safety_checks` 가 모두 pass (unknown → 보류)
7. `required_fresh_sensors` 신선
8. RAG 근거 존재
9. (`policy.benchmark` 가 있을 때) **case 탐색 종료 규칙**이 이 case 를 선택(`case_selection`). 심슨을 이긴 case 가 없고 심슨이 적격이면 `RECOMMEND:<기준>`(hold)이 열립니다. 종료 후 남은 case 시뮬레이션은 `case_search_open` 으로 제외됩니다. → `docs/BENCHMARK.md`

---

## 4. 경계 B — 에이전트 ↔ Zoro-1

### 공식 형식 (확인 출처)
- **Zoro-1 모델 카드**: https://huggingface.co/trillionlabs/Zoro-1 (2026-11-19 확인)
  - 요청 `{"state": str|object, "questions": {qid: {"type": "choice"|"noul"|"score", "instructions": str, "criteria": ...}}}`. choice 의 `criteria` 는 `{선택지: 설명}` 객체입니다.
  - 응답 `{"answers": {qid: {"type": "choice", "choice": str, "confidence": float, "probabilities": {선택지: float}}}}`
  - 서버(trida-stack): `python serve.py --mode decide --port 30001` → `POST /v1/decide`, 본문 `{"request": <요청>, "reasoning": false}`. reasoning 은 `true`/`"auto"` 도 가능합니다.
  - 확률은 보정 온도(기본 1.59, `temperature.json`)가 적용된 값입니다.
- 참고(같은 Typed Decision 계열 API): https://openrouter.ai/docs/guides/features/decisions.md. 답이 `type: "refusal"` 이거나 누락되면 "미응답"으로 보고 fail-closed 로 처리합니다. 이 원칙을 그대로 적용했습니다.

> 제가 확인한 것은 모델 카드의 공개 설명입니다. 게이트 뒤의 `zoro_decide.py` / trida-stack 소스는 직접 열람하지 못했습니다. 실제 서버를 연결할 때 응답 본문 최상위 구조를 한 번 확인해야 합니다(`answers` 가 최상위인지).

### B-1 `ZoroRequest` (실제 전송 본문) — `B1_zoro_request_*.json`
- `questions.next_action` (choice) 하나만 둡니다. `criteria` 에는 **이번 판단에서 open 인 선택지만** 넣습니다.
- `state` 섹션: `event`, `active_alarms`, `active_setpoints`, `actual_observations`(최신값·age·신선도), `information_gaps`, `safety_checks`, `sop_evidence`(선택지에 쓰인 근거 발췌), `rag_unresolved`, `history`(이전 선택·도구 상태·정보 갱신 여부·결과 요약), `virtual_experiments`(run·영향·비교, **가상 예측**으로 표시), `unavailable_options`(보류/제외 이유), `budget_remaining`.
- simulation 선택지 설명(`criteria`)에는 case 요약(예 `case CASE-SP19-12P5: propose_setpoint (SETPT(19) 목표 12.5 mol%)`)을 붙입니다.
- 내부 추론 전문은 넣지 않고 요약만 넣습니다.

### B-2 `ZoroRawResponse` — 원본 보존
`request_id, client_kind("real"|"fake"), model, endpoint, received_at, latency_ms, body(원본 그대로)`

### B-3 `Decision` — 검증·변환된 내부 결정 (`B3_decision_*.json`)

| 필드 | 설명 |
|---|---|
| `decision_kind` | `tool_call` / `recommendation` / `human_review` |
| `decided_by` | `zoro` 또는 `policy`(모델을 호출하지 않고 정책으로 운영자 검토를 결정한 경우) |
| `selected_option_id` | 검증을 통과한 선택. 거부되면 null |
| `model_choice_raw` | 모델이 실제로 고른 값(거부된 경우 포함) |
| `tool_request` | 등록된 `args_template` 로만 만든 `ToolRequest` |
| `evidence_refs[]` | `rag_evidence` / `run` / `policy_check` 등 참조 |
| `selection_distribution` | `meaning="action_selection_distribution"`. **안전 확률·회복 확률·정답률이 아닙니다** |
| `validation` | `valid, errors, warnings, allowed_option_ids` |

**검증**: 답 누락 또는 refusal → 무효. choice 형식 오류 → 무효. 허용 목록 밖 선택 → 거부. 분포에 허용 목록 밖 키가 있으면 거부. 분포에 빠진 허용 키 → 경고. 선택된 옵션 상태가 open 이 아님 → 거부(이중 방어). 무효인 결정은 도구를 실행하지 않고 운영자 검토로 종료합니다.

**설명 생성 금지**: Zoro 는 선택과 분포만 반환합니다. 이유 문장은 만들지 않습니다. 최종 출력의 `rationale` 은 에이전트가 상태·정책·실험·근거로 구성한 것이며, 항목마다 `basis` 와 `refs` 를 붙입니다.

---

## 5. 경계 C — 에이전트 → 백엔드·화면 (영하)

| 타입 | 용도 | 예시 |
|---|---|---|
| `ToolRequest` | 도구 실행 요청 `tool_request_id, decision_id, event_id, state_version, action_id, tool_name, case, args, evaluation`(채점 명세, 모든 case 동일), snapshot_id, issued_sim_time_s`. simulation 이면 `case`(`SimulatorCase`)가 시뮬레이터에 넘길 대응입니다 | `C1_tool_request_simulation_hold.json`, `C1_tool_request_simulation_setpoint.json` |
| `LogEntry` | 단계 구분 기록. `stage`: input_received / options_built / model_requested / model_selected / decision_rejected / policy_decision / tool_started / tool_succeeded / tool_failed / tool_running / state_updated / terminated. `decision_id`, `tool_request_id`, `run_id` 로 연결 | `C2_log.json` |
| `ProgressStatus` | 화면용 진행 상태 `phase, iteration, usage, remaining, open/deferred 선택지, finished, termination_reason` | `C3_progress.json` |
| `FinalOutput` | `outcome`(recommendation / human_review), `termination_reason`, `recommended_action_id`(null = 권고 없음), **`setpoint_command`**(아래), `case_selection`(심슨 대비 탐색 기록·종료 판정), `summary`, `rationale[{statement, basis, refs}]`, `uncertainties[]`, `compared_run_ids`, `selection_distribution`, `model_client_kind`, `tools_virtual`, `disclaimer` | `C4_final_output.json` |

### 시뮬레이터로 넘기는 출력

**`SimulatorCase`** (`ToolRequest.case`, 시험 실행용): `case_id, policy, setpoint_changes[], other_setpoints="unchanged", lower_loops_and_protection="unchanged", values_status`.
각 변경(`ResolvedSetpointChange`): `setpoint_id, scheme_index, loop_id, controlled_tag, from_value`(현재 적용값, null = 미제공), `from_source, target_value, unit, apply_at_s, ramp_rate_per_h, allowed_min, allowed_max`. 시뮬레이터 어댑터는 `apply_at_s` 에 `Scheme::set_setpoint(scheme_index, target_value)` 를 호출하면 됩니다. run 실행 인자(`snapshot_id, horizon_s, composition_sample_id`)는 `args` 에 있습니다.

**`SetpointCommand`** (`FinalOutput.setpoint_command`, 최종 적용 제안 — STUDIO "Zoro 출력" 항목과 1:1):

| STUDIO 항목 | 필드 |
|---|---|
| hold 또는 propose_setpoint | `decision`, `case_id` |
| 근거 SOP ID | `sop_refs[]` |
| 대상 상위 루프, 목표값과 단위 | `setpoint_changes[]` (위와 같은 구조) |
| 예상 비용 항목별 변화의 방향 | `expected_cost_direction` (사전 가설) + `expected_direction_basis` |
| 예상과 실제 절감액은 별도 필드 | `simulated_effects[]`(기준 hold run 대비 가상 예측 차이 `metric, delta, unit, run_id, baseline_run_id`) / `actual_effects`(항상 null — 적용 후 평가기가 기록) |
| 확인할 신호 | `watch_signals[]` |
| 재검토 시각 | `review_at_sim_time_s` = `issued_sim_time_s + review_after_s` |
| 원복 조건 | `revert_rules[]` |
| (추가) | `values_status`(example = 검증 기준 아님), `requires_supervisor_approval=true` |

권고가 없으면(운영자 검토 종료 포함) 기준 case 로 **hold** 를 냅니다(SOP S08: "바꿀 근거가 부족하면 현재 목표를 유지한다"). 적용 여부·변화율·대기는 감독기가 최종 결정합니다.

**종료 조건** (`TerminationReason`): `recommendation_ready`, `human_review_selected`, `limit_reached`(판단 횟수·도구·시뮬레이션·벽시계), `unrecoverable_tool_error`, `no_valid_options`, `model_response_invalid`, `model_call_failed`.

---

## 6. 팀원별로 받아야 할 데이터

| 팀원 | 받아야 할 것 | 형식 |
|---|---|---|
| **정민** | 사건 유형, 대응 후보 `action_id` 목록, **대응 case**(바꿀 상위 목표·목표값·근거 SOP 단계·확인 신호·재검토 시간·원복 조건·비용 방향 가설), **허용 설정값 목록**(범위·최대 변경폭·변화율·대기), 적용·확인 조건(`conditions`)과 판정 방식, 기준(무조치) 실험 정의, 권고 허용 기준(필수 비교 대상·알람 회복·한계 초과 정책·필수 안전 검사), 센서별 신선도 한도 | `ActionCatalog` 의 actions/conditions, `AgentPolicy` |
| **지연** | 검색 결과 원문(문서 ID·**버전**·조항·원문·URI), 대응 후보 참조와 근거 chunk 연결, 조건 참조, "검색만으로 판단 불가" 항목, 설비 관계 조회 API. **RAG 참조 → action_id / condition_id 별칭표 합의** | 원본 JSON 1개 + 별칭표. 형식이 정해지면 어댑터 추가 |
| **장하** | 사건 상태(`EventInput`): `snapshot_id`, 관측 이력(시각·품질·sample_id), 알람, 외부 안전 검사 결과. 센서 조회 도구(같은 시료 재전달 여부 포함). **현재 적용 중인 상위 목표**(`active_setpoints`). 시뮬레이션 실행 도구: `snapshot_id + SimulatorCase + horizon + 초기조성 sample` 입력 → `Scheme::set_setpoint(scheme_index, value)` 적용, `run_id` + `ExperimentConditions`(simulator_version, disturbance_profile_id, seed, horizon_s) + 상태 + Studio 형식 CSV 위치 반환. 비동기 실행이면 running/완료 조회 방법 | `(ToolRequest) -> ToolResult` 함수 또는 HTTP |
| **동은** | run 별 `ImpactResult`(원래 알람 회복 여부·시간, 영향 설비·센서, 한계 초과 시각·지속·최대량, 생산 손실 등 지표와 단위, 근거 구간). 기준/A/B 비교 결과(`ExternalComparison`, 비교 불가 이유) | `ImpactResult` / `ExternalComparison` |
| **영하** | `ToolRequest` 를 받아 실행하고 `ToolResult` 를 돌려주는 백엔드 엔드포인트, `LogEntry`/`ProgressStatus`/`FinalOutput` 저장·표시, `state_version`·ID 체계 합의, Zoro 서버 주소·인증 주입(환경변수) | 이 문서의 C 경계 |

---

## 7. 아직 합의·연결이 필요한 것

1. **Zoro 서버 응답 최상위 구조 실측**: `{"answers": ...}` 인지 래핑되어 오는지 확인해야 합니다. 보정 온도(`temperature`)를 기본값으로 둘지도 정해야 합니다.
2. **RAG 실제 원본 형식**과 action/condition **별칭표**(지연·정민).
3. **조건 판정 주체**: 지금은 `sensor_fresh / safety_check / run_succeeded` 만 에이전트가 판정합니다. 그 외 조건은 외부 규칙 엔진의 `safety_checks` 로 받는 방식을 제안합니다(정민).
4. **조성 갱신 후 실험 출발점**: 지금은 같은 `snapshot_id` 에 `composition_sample_id` 를 넘겨 초기 조성을 덮어쓰는 방식입니다. 장하님 시뮬레이터에서 이 방식이 가능한지, 아니면 새 snapshot 을 만들어야 하는지 정해야 합니다. 새 snapshot 을 만들면 기존 run 과 비교할 수 없게 됩니다.
5. **비동기 시뮬레이션**: running 상태는 기록과 중복 방지까지 구현했습니다. 완료를 확인하는 폴링(별도 조회 행동 또는 백엔드 콜백)은 아직 연결하지 않았습니다.
6. **공정 시간 진행**: 조회 결과의 `finished_sim_time_s` 로만 `sim_time_s` 를 갱신합니다. 판단 중에 실제 공정 상태가 바뀌는 것(새 알람·관측)을 반영하는 상태 새로고침 경로가 필요합니다(장하·영하).
7. **시뮬레이터 설정값 어댑터**: STUDIO.md 대로 공개 Studio 화면으로는 상위 목표를 바꿀 수 없으므로 `set_setpoint` 를 호출하는 별도 어댑터가 필요합니다(장하). `scheme_index=19` 는 STUDIO.md 의 `set_setpoint(19, value)` 를 따랐고 Studio 소스로 재확인해야 합니다.
8. **case 목록·허용 범위**: 현재 등록된 case 는 퍼지 B 목표(`SETPT(19)`) 12.5 / 12.0 mol% 와 hold 뿐이며 값은 예시입니다. 다른 상위 목표(제품 E·반응기 온도 등)를 열지, 변경폭·대기를 얼마로 할지 정해야 합니다(정민·감독기).
9. **모든 정책 수치**(신선도 300/720 s, min_candidate_runs, 한도, 압력·생산 원복 임계값 등)는 **예시**입니다.
