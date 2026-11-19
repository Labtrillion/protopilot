# Zoro RAG — 알림 JSON에서 문헌·SOP 근거 검색

입력은 알림에서 추출한 JSON입니다. **BGE-M3 + BM25 → Qwen3 리랭커**로 관련 문헌과 SOP 검토 계획을 찾습니다.

## 실제 모델 검증

이 폴더에서 다음 Python 명령을 실행하면 TEP Studio와 IDV(8) 예제를 CPU에서 검증합니다.

```bash
.venv/bin/python scripts/verify_rag.py --device cpu
```

터미널에 BGE-M3 임베딩 차원과 dense 검색 결과, BM25 검색 결과, Qwen 리랭킹 점수, SOP 검토 계획을 출력합니다. 전체 알람 결과는 `artifacts/verification/tep_studio/result.json`과 `artifacts/verification/idv8/result.json`에 저장합니다. 각 예제의 검증 인덱스도 같은 폴더 아래에 저장하여 재사용합니다. 한 예제만 확인할 때는 `--scenario tep_studio` 또는 `--scenario idv8`을 붙입니다.

## TEP Studio 센서별 SOP로 실행 (2026-11-19 추가)

```bash
python main.py --config config/tep_studio.yaml --input examples/tep_studio/alarm_event.json --output artifacts/tep_studio/result.json
```

`examples/tep_studio/knowledge/tep_studio_braatz_sop.json` 은 상위 프로젝트의 `scripts/build_tep_studio_sop.py` 가 TEP Studio 제어 코드(tep-rust tepsim-control)에서 생성한 센서별 대응 SOP입니다. 계측 41개마다 제어 루프·P/PI·주기·최종 밸브·퍼지 오버라이드를 적고, 감독기가 바꿀 수 있는 SETPT(19) case 계획을 담았습니다. 사람이 읽는 표는 [SOP 문서](../docs/SOP_TEP_STUDIO.md)에 있습니다.

### 이번에 고친 것
- **정답 누출 차단**: `simulation_context.disturbance_id` 와 `recent_changes` 의 주입 외란은 기본으로 검색·결과에 쓰지 않습니다(`alarm.use_simulation_disturbance_id: false`). 제거한 필드는 `alarm_context.withheld_ground_truth_fields` 에 남습니다.
- **SOP 후보 일반화**: SOP 검색과 `candidate_actions` 를 외란 번호·`synthetic_demo_fixture` 출처가 아니라 **경보 태그**(`trigger_variable_ids`)로 정합니다.
- **시뮬레이터 적용성**: 계획의 `plan.simulator` 가 설정 `simulator` 와 다르면 `status: simulator_mismatch`. IDV(8) 데모 SOP의 반응기 압력 설정값은 APMonitor 교육용 구현 전용입니다(TEP Studio Braatz 구조에는 반응기 압력 루프가 없음).
- **경보 형식 통일**: `schema_version: zoro-alarm-event-v1`(canonical_id·alarm_level·simulation_time_seconds) 입력을 자동 변환합니다. 시뮬레이션 초는 1970-01-01 UTC 기준 시각으로 옮깁니다(벽시계 아님).
- **문서 버전**: 청크·근거에 원본 파일 내용 해시(`document_version`/`source_version` = `sha256:<12>`)를 붙입니다. 인덱스 스키마가 5로 바뀌어 `ingest --rebuild` 가 필요합니다.
- **지식 파일**: `TEP_Disturbance_Knowledge.md` 의 잘못된 경로와 `scenarios.json`(주입 시각) 참조를 지웠고, `idv_catalog.json` 의 `name_ko` 를 한국어로 채웠습니다.
- **SOP 는 안전 근거로 세지 않음**: 작성 SOP(`authored_review_protocol`)는 공정 안전 제약 근거로 인정하지 않습니다.
- 테스트 파일 읽기에 `encoding="utf-8"` 지정(Windows), 테스트의 `/home/yongbin53` 캐시 경로 고정 제거(`ZORO_SPARSE_CACHE` 로 지정).

알려진 문제: Windows 의 로컬 Qdrant 에서는 `delete_collection` 후에도 점이 남아 `test_persistent_index_repeated_ingestion_and_empty_results` 가 실패합니다(수정 전에도 동일, Linux 미확인).

## IDV(8) 시나리오 처음 실행

```bash
cd features/feature1-setpoint-gate/rag
source .venv/bin/activate
python main.py
```

`main.py`가 문서와 SOP를 색인한 뒤 복수 알람 JSON을 처리합니다. 처음에는 모델 로딩과 색인 시간이 필요합니다. 다음 실행에서는 기존 인덱스를 재사용하고 변경된 자료만 갱신합니다.

터미널에서는 문서 읽기 → 문서 임베딩·저장 → Dense/BM25 검색 → Qwen 리랭커 단계별 `tqdm` 진행률을 표시합니다. 진행률은 stderr, 결과 JSON은 stdout과 결과 파일로 출력합니다. stderr를 파일로 돌리면 진행 막대는 자동으로 숨깁니다. 모델 로딩 시간은 작업 진행률과 별개이며, 변경된 청크가 없으면 임베딩 단계는 건너뜁니다.

| 용도 | 파일 |
|---|---|
| 시나리오 알람 입력 | `examples/idv8/alarm_event.json` |
| 시나리오 SOP | `examples/idv8/knowledge/sop.json` |
| 원본 공정 문헌 | `zoro_rag_knowledge/` |
| 데모 설정 | `config/demo_idv8.yaml` |
| 실행 후 결과 | `artifacts/idv8/result.json` |
| 실행 후 색인 기록 | `artifacts/idv8/ingest.json` |

데모 알림은 원료 A 조성 이상·Purge 유량 증가·운전 비용 증가입니다. 센서값과 경보 임계값은 합성이며 실제 공장 로그가 아닙니다.

SOP는 2705·2750·2800·2850 kPa(g)의 압력 Setpoint를 같은 조건에서 시뮬레이션으로 비교하는 계획을 제공합니다. 최고 압력 2900 이하(셧다운 3000에서 여유 100)와 나머지 공정·생산·품질 제약을 검토하도록 구성했습니다.

출력의 `candidate_actions`는 **시뮬레이션 검토 계획**입니다. 실제 압력 최적화 시뮬레이션은 구현하지 않았으므로 결과와 최종 추천값은 null이며, 제어 조작을 실행하지 않습니다. SOP는 `synthetic_demo_sop`으로 구분하고 안전 제약은 원본 문헌에서 찾습니다.

## main.py를 직접 실행

```bash
source .venv/bin/activate
python main.py --input examples/idv8/alarm_event.json --output artifacts/idv8/result.json
```

실제 알림을 연결할 때는 `python main.py --config config/settings.yaml --input 알림.json --output artifacts/result.json`처럼 설정과 입력을 지정하세요. 알림 서비스에서 JSON을 만드는 어댑터는 별도입니다.

## 알림 JSON 메타데이터

기존 숫자형 `process_state` 입력도 지원합니다. 새 형식은 각 태그에 관측 메타데이터를 함께 넣습니다. 전체 예제는 `examples/idv8/alarm_event.json`에 있습니다.

```json
{
  "value": 2715.0,
  "unit": "kPa(g)",
  "measured_at": "2026-11-19T14:00:00+09:00",
  "quality": "good",
  "sample_id": null
}
```

| 위치 | 의미 |
|---|---|
| `timestamp` | 이벤트 기준 시각. 측정 신선도 판단 기준 |
| `snapshot_id` | 재현할 시뮬레이터 상태의 참조 ID |
| `process_state[TAG]` | 위 형식의 관측값 또는 기존 숫자/문자열/null |
| `alarms[].measured_at` | 경보에 연결된 측정값의 시각 |
| `alarms[].quality`, `sample_id` | 측정 품질과 분석 시료 참조 |
| `alarms[].activated_at` | 경보 활성 시각. 측정 시각과 별도 |
| `alarms[].threshold` | 경보 발생 기준 |
| `alarms[].deadband_clear_value` | deadband 해제 기준값. 발생 기준과 별도 |
| `active_objective` | `objective_id`, `metric`, `direction`, `target_value`, `unit`, `applied_at`, `description`, `constraints` |

`quality`는 `good`, `uncertain`, `bad`, `unknown`이며 대문자 입력도 받습니다. 생략하면 `unknown`입니다. `direction`은 목표에서 `minimize/maximize/maintain`, 알람에서 `high/low`를 사용합니다. 비용 최소화처럼 정해진 목표 수치가 없으면 `target_value`는 null입니다.

경보의 기존 `timestamp`는 측정 시각으로 계속 지원합니다. `measured_at`과 함께 넣으면 같은 시각이어야 합니다. `activated_at`을 측정 시각으로 대신 쓰지 않습니다. 시간대 오프셋을 명시하는 것을 권장하며, 기존 시간대 없는 입력은 UTC로 해석합니다. 미래 측정 시각은 신선도를 확정하지 않고 표시합니다.

결과 `alarm_context.measurements`에 태그별 메타데이터와 `stale`을 보존하고, 기존 `observed_variables`에는 값만 유지합니다. `active_alarms`에는 경보 측정과 활성 시각·해제값을 별도로 보존합니다. 이벤트 시각·스냅샷 ID·현재 목표·시뮬레이터 컨텍스트도 결과에 포함합니다.

품질 문제, 누락 단위/측정 시각, 오래된 값, 단위·시료 불일치는 `missing_information`과 최종 `missing_evidence`에 표시합니다. 값을 삭제하거나 단위를 임의 변환하지 않습니다. 경보와 공정 상태가 서로 다른 관측이면 각각 유지하며, 같은 태그의 표기만 다른 중복 입력은 거부합니다.

숫자형 해제값은 high 경보에서 발생 기준 이하, low 경보에서 발생 기준 이상이어야 합니다. 이 검증은 경보 상태를 자동 해제하는 기능은 아닙니다.

SOP 검토 계획에는 `snapshot_id`와 `active_objective`도 함께 반환합니다. 입력 품질이나 목표·제약 정합성에 문제가 있으면 상태를 `input_review_required`로 표시합니다. 목표 비교는 명시된 metric·방향·단위·제약 키의 일치 검사이며, 최적화 가능성의 수학적 검증은 아닙니다. 스냅샷 ID나 현재 목표가 빠진 경우도 검토 대상으로 표시합니다.

스냅샷·시료 ID는 참조를 보존할 뿐 실제 스냅샷 저장/복원이나 시료 데이터 조회를 수행하지 않습니다. 데모에 추가한 ID·품질·활성 시각·해제값·목표는 모두 합성 데이터입니다.

## 폴더

- `main.py`, `src/zoro_rag/`: 실행 코드, 파싱·청킹·검색·알람 처리.
- `examples/idv8/`: 이번 시나리오 입력과 SOP.
- `examples/alarm_event.json`, `examples/multi_alarm_event.json`: 기존 입력 예제. 테스트에서 사용합니다.
- `zoro_rag_knowledge/`: 원본 지식 문서와 두 검색기가 공유하는 외란 가이드 및 IDV 카탈로그. 손상 PDF의 복구 경고와 일부 깨진 문자가 남아 있습니다.
- [`legacy/`](legacy/README.md): 이전 검색기, 팀 SOP 원문, Studio 평가 도구와 검증 CSV.
- `artifacts/`: 실행할 때 생성되는 결과.
- `config/`: 기본·데모·테스트 설정.
- `scripts/`: 설치·환경 확인·모델 다운로드.
- `tests/`, `evaluation/`: 테스트 코드와 초안 평가 사례.

`.venv`와 프로젝트 밖 모델 캐시는 실행 환경입니다. `.zoro_demo_idv8`은 데모 실행 시 생성되는 검색 인덱스이며 기본 인덱스 `.zoro_index`와 분리합니다. 의존성은 `pyproject.toml`, 검증한 Linux 버전은 `constraints-linux.txt`에서 관리합니다.

## 보조 명령

```bash
python main.py --help
python main.py --config config/demo_idv8.yaml stats
python -m pytest -q
```

`stats`는 색인을 준비한 뒤 사용하세요. 기본 설정은 `config/demo_idv8.yaml`이며, `python main.py`만 실행하면 IDV(8) 시나리오의 색인 준비부터 알람 처리·결과 저장까지 실행됩니다. 셸 데모 스크립트는 필요하지 않습니다.

새 Linux 환경 설치는 `bash scripts/setup_linux.sh`로 진행합니다. 현재 서버에는 환경과 모델이 준비되어 있어 재설치할 필요가 없습니다.
