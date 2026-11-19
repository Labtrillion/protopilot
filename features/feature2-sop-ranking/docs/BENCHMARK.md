# 벤치마크: Zoro vs 심슨(SOP 단독)

**심슨** = SOP 만 규칙으로 따르는 1차원 정책입니다. 상위 목표를 바꾸지 않고(hold) 하위 제어기에 맡깁니다(SOP S08 "SOP 단독은 기존 목표를 유지한다"). 에이전트의 기준 실험 `SIM_HOLD`(case `CASE-HOLD`)가 심슨입니다.
주장하려는 것: **같은 경보 상황(같은 snapshot·시드·외란·빌드)에서 Zoro 가 고른 설정값 case 가 심슨보다 낫다.**

> 아래 수치(여유폭·patience·관찰 시간·목표값)는 모두 **예시 설정**입니다(`policy.json → benchmark`, `values_status: example`). 실제 공정에서 검증된 기준이 아니며 최종 시험 전에 팀이 고정해야 합니다.

코드: `sop_ranking/benchmark.py` · 오프라인 짝비교: `scripts/benchmark_pair.py` · 경보 원본 어댑터: `sop_ranking/alarm_adapter.py`

---

## 1. 지표

### 비용 — USD/m³ (낮을수록 좋음)
- **시뮬레이터 분기(ProtoPilot, `sop_ranking/zoro_bridge.py`)**: `feat/setpoint-decision` 의 정의를 그대로 씁니다. TEP 내부 운전비 `operating_cost_internal_per_hour` 를 제어 스텝마다 적분한 총액(`server/branch_metrics.step_cost`)을 같은 스텝 적분 생산량으로 나눕니다(`branch_summary` 의 `cost_per_m3`). `RunScore.cost_basis = "tep_operating_cost_internal"`, `cost_usd` 에는 `total` 만 있습니다. 생산 손실(`production_loss_m3`)과 알람 시간(`alarm_minutes`, `AlarmMonitor` 실제값 채널)도 같은 정의로 지표에 붙습니다. 같은 체크포인트·horizon 에서 `alarm_response.run_branch` 와 값이 같은지 `test_branch_cost_and_alarm_minutes_match_the_alarm_response_branch` 로 확인합니다.
- **오프라인 시계열(TEP Studio CSV 등)**: 스텝 적분값이 없으므로 Downs & Vogel (1993) 표 9의 네 운전비(퍼지 손실, 제품 손실, 압축기 동력, 스팀)를 표본 시각 기준 사다리꼴 적분합니다. 식은 `rag/legacy/studio_eval.py` 와 같습니다. 검증 CSV 3개로 대조했고 **총비용·m³당 비용·생산량이 상대오차 1e-9 이내로 일치**합니다(`test_cost_matches_rag_studio_eval`). `cost_basis = "downs_vogel_table9"`.
- 비용 정의가 다른 run 끼리는 비교하지 않습니다(`beats` 가 거부).
- 두 정책은 **생산량당 비용**으로 비교합니다. 정책 간 생산량이 최대 1% 다를 수 있으므로(`rag/legacy/STUDIO.md` 짝비교 조건), 총액으로 비교하면 감산만으로 1% 절감처럼 보일 수 있습니다.

### 정확도 — tracking accuracy (0~1, 높을수록 좋음)
추적 변수마다 매 표본에서

```
s(t) = max(0, 1 − |x(t) − 목표| / |경보 임계값 − 목표|)
```

- 목표에 정확히 있으면 1, 경보 임계값에 닿거나 넘으면 0입니다. 임계값 밖에 있는 동안은 계속 0입니다.
- 상·하한이 모두 있으면 값이 있는 쪽의 거리를 씁니다. 상한만 있으면(예: 압력 HI) 아래쪽도 같은 거리로 봅니다.
- 평가 구간 시간 평균 → 변수 가중 평균. **사건 경보 변수는 가중치 2**, 나머지는 1입니다.

추적 변수(예시):

| 변수 | 목표 | 임계값 | 출처 |
|---|---|---|---|
| XMEAS(7) 반응기 압력 | 2705 kPa(g) | HI 2895 (경보 이벤트의 `threshold` 가 있으면 그 값) | 목표: Downs & Vogel 기본 운전점. 상한: STUDIO.md 데모 기준 = 경보 예시 |
| XMEAS(9) 반응기 온도 | 120.40 °C | ±0.50 | SOP S07 프로젝트 판정 폭 |
| XMEAS(8) 반응기 액위 | 75 % | ±5 | SOP S07 |
| XMEAS(12) 분리기 액위 | 50 % | ±5 | SOP S07 |
| XMEAS(15) 스트리퍼 액위 | 50 % | ±5 | SOP S07 |

경보 이벤트에 위 표에 없는 태그가 오면 `reference_setpoints` 의 목표를 씁니다. 목표가 없으면 정확도에서 빼고 `untracked_alarm_tags` 로 보고합니다(목표값을 만들어 넣지 않음).

### 적격 게이트 (하나라도 실패하면 그 run 은 '이길' 수 없음)
1. 트립 없이 완주
2. **마지막 1시간** 동안 모든 추적 변수가 임계값 안 — SOP S07 "1시간 연속 범위 안이면 관측 복귀"
3. 생산량 ≥ 심슨의 **99%** — STUDIO.md 짝비교 조건(정책 간 생산 차이 1% 이내)
4. 제품 품질: G/(G+H) 45–55 %, D+E+F ≤ 2 mol% — STUDIO.md 데모 채점 기준

---

## 2. "A 가 B 보다 낫다"의 정의

1. **적격이 먼저**입니다. A 가 부적격이면 지고, A 는 적격인데 B 가 부적격이면 A 가 이깁니다(경보를 해소하는 것이 비용보다 먼저).
2. 둘 다 적격이면 **ε-지배**로 봅니다.
   - 어느 지표도 허용폭 이상 나빠지지 않는다: 비용 +0.5% 이내, 정확도 −0.01 이내.
   - 그리고 적어도 하나가 개선폭 이상 좋아진다.

| 비교 | 정확도 개선폭 | 비용 개선폭 | 허용 악화폭 |
|---|---|---|---|
| case vs **심슨** | +0.03 | −1.0 % | 정확도 −0.01, 비용 +0.5 % |
| case vs **현재 최선 case** | +0.02 | −0.5 % | 같음 |

**수치를 이렇게 잡은 이유**
- 비용 1%: 검증 CSV 에서 같은 시드의 nominal 과 IDV(4) 운전은 m³당 비용이 0.0001% 미만, IDV(4,5,11) 복합 외란도 0.015% 밖에 다르지 않습니다. 같은 시드 짝비교에서 비교 자체의 잡음은 매우 작습니다. 그런데 생산량 허용 차이가 1%이므로, 1% 미만의 비용 차이는 생산량 차이와 구분하기 어렵습니다. 그래서 심슨 대비 개선은 1% 이상을 요구합니다.
- 정확도 0.03: 같은 CSV 에서 IDV(4,5,11) 복합 외란이 정확도를 0.031 낮췄습니다(0.917 → 0.886, `scripts/benchmark_pair.py` 로 측정). "외란 하나를 상쇄하는 정도"를 의미 있는 개선의 최소 단위로 잡았습니다.
- case 간 개선폭은 심슨 대비의 절반 수준입니다. 이미 심슨을 이긴 후보끼리는 작은 차이로도 교체할 수 있게 하되, 0으로 두면 같은 수준의 후보가 계속 최선을 갈아치워 탐색이 끝나지 않습니다.
- **한계**: 위 근거는 한 시드의 결과입니다. **시드를 바꿨을 때의 변동폭은 아직 측정하지 않았습니다.** 여러 시드에서 심슨끼리 비교해 변동폭의 2~3배로 다시 정하는 것을 권합니다.

---

## 3. case 탐색 종료 규칙

case 는 현재 설정값에서 가까운 것부터 양방향으로 번갈아 둡니다. 예시는 SETPT(19) 13.823 mol% 기준 ±0.5, ±1.0, ±1.5, ±2.0 의 8개입니다(간격 0.5, 최대 변경폭 2.0). 시험 순서는 Zoro 가 고르고, 규칙은 **실제로 시험한 순서**에 적용합니다.

```
case_n 을 선택 ⇔
  (1) case_n 이 심슨을 이긴다 (위 표의 심슨 기준)
  (2) case_n 이후 시험한 patience=4 개 case 중 어느 것도 case_n 을 이기지 못한다
      (이기면 그 case 가 새 최선, 카운터 0부터)
```

- **patience 4**: case 를 양방향으로 번갈아 두므로 4개는 최선 뒤로 **각 방향 2칸씩** 더 본 것입니다. 1차원 설정값에서 비용·정확도가 한 봉우리 모양이면 2칸 연속 나빠진 뒤 다시 좋아질 가능성은 낮다고 보았습니다. 제안하신 5개 대신 4개로 잡은 이유는 양방향 대칭 때문입니다.
- **최대 8개 case**(심슨 제외): 이 안에서 끝나지 않으면 그때까지의 최선을 고릅니다. 시험할 case 가 남지 않았거나 시뮬레이션 예산이 다 떨어져도 같습니다.
- **동점이면 먼저 시험한 case 를 유지**합니다. 시험 순서가 가까운 것부터이므로 설정값을 덜 바꾸는 쪽이 남습니다.
- 끝까지 심슨을 이긴 case 가 없을 때:
  - 심슨이 적격이면 **hold 권고**(`RECOMMEND:SIM_HOLD`, setpoint_command = hold)
  - 심슨도 부적격이면 **운영자 검토**(`none_acceptable`)
- 종료 조건이 충족되면 남은 case 시뮬레이션 선택지는 `case_search_open` 검사로 **제외**됩니다. 선정된 case 외의 권고 선택지도 제외됩니다.

데모 결과(가짜 점수, 가상 예측)는 다음과 같습니다. 심슨은 압력이 마지막 1시간에도 임계값 위에 있어 부적격입니다.

| 순서 | case | 판정 |
|---|---|---|
| 1 | UP050 (14.323) | 부적격 |
| 2 | DN050 (13.323) | 적격 → 심슨을 이김, **최선** |
| 3 | UP100 | 부적격 (1/4) |
| 4 | DN100 | 정확도 +0.03 이지만 비용 +0.78% > 허용 0.5% (2/4) |
| 5 | UP150 | 부적격 (3/4) |
| 6 | DN150 | 비용 +2.2% (4/4) → **종료, DN050 선택** |

UP200·DN200 은 시험하지 않습니다(시뮬레이션 7회).

---

## 4. 데이터 흐름

- 시뮬레이션 요청 `ToolRequest.evaluation`(`EvaluationSpec`)에 추적 변수·목표·임계값·관찰 시간·게이트를 붙입니다. **모든 case 에 같은 명세**가 붙어야 비교가 성립합니다.
- 채점(동은님 모듈)은 `ImpactResult.score`(`RunScore`)로 돌려줍니다. 필드는 `usd_per_m3, cost_usd{total | purge,product,power,steam,total}, cost_basis, product_m3, tracking_accuracy, per_variable_accuracy, in_threshold_fraction, time_to_stable_s, final_window_inside, quality_ok, tripped, failed_gates` 입니다. `score_timeseries()` 가 참조 구현이며 Studio CSV 를 그대로 읽습니다.
- 최종 출력 `FinalOutput.case_selection` 에 시험 순서별 판정(심슨 대비·최선 대비 승패와 이유)과 종료 이유가 남습니다.
- 오프라인 발표용 짝비교:
  ```
  python scripts/benchmark_pair.py simpson.csv zoro.csv --alarm alarm_event.json [--start-h 6 --end-h 10]
  ```

## 5. 경보 원본 형식 (`zoro-alarm-event-v1`) 연결

`adapt_alarm_event(raw, snapshot_id=...)` 가 `EventInput` 으로 바꿉니다. 예시는 `examples/scenario_tep_purge/alarm_event.json` 입니다.
- `canonical_id` `XMEAS7` → `XMEAS(7)`, `alarm_level` HI/HIHI/LO/LOLO → high/high_high/low/low_low, `threshold`·`unit`·`raw_tag` 를 보존합니다. 임계값은 정확도 계산에 그대로 쓰입니다.
- **`simulation_context`(disturbance_id) 는 평가 정답이므로 버립니다.** `recent_changes` 는 형식이 합의되지 않아 반영하지 않습니다.
- 원본에 없는 것은 만들지 않습니다.
  - `snapshot_id` 는 호출자가 넘깁니다.
  - 태그별 측정 시각이 없어 이벤트 시각을 쓰고 경고를 남깁니다.
  - 품질 정보가 없어 기본값은 `uncertain` 입니다(→ 신선도 unknown → 시뮬레이션 보류).
  - `process_state` 에서 단위를 모르는 태그(예시의 XMEAS9, XMV10)는 관측에서 뺍니다.

**경보 형식에 추가를 요청할 필드**: `snapshot_id`, 태그별 단위·측정 시각·품질, 분석기 시료 ID, 현재 적용 중인 상위 목표(`active_setpoints`), 경보 활성 시각, 경보 해제 임계값(deadband).
