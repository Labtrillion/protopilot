# Feature 1 · 설정값 변경 판단

`POST /api/setpoint-decision`은 캡처한 계측 상태와 라이브 SOP를 사용합니다. 활성 알람이 있으면 [Zoro 추론 모듈](setpoint_gate/zoro_inference/__init__.py)이 같은 체크포인트의 RAG 근거와 후보 계획을 함께 전달합니다. Zoro-1의 `noul`로 변경 필요성을 판단하고, 임계값 0.5를 통과한 상위 두 설정값에 대해 `choice`로 허용 범위 내 변경 방향과 크기를 정합니다. 기준안은 정확히 두 변경으로 구성하며 두 항목을 확정할 수 없을 때는 `human_review`와 빈 변경 목록을 반환합니다.

- [설정값 판단 코드](setpoint_gate/setpoint_decision.py)는 `POST /api/setpoint-decision`으로 실행합니다.
- [RAG](rag/README.md)는 독립 검색기, 라이브 센서 SOP 인덱스, 운전 지식, 기존 검증 자료와 [라이브 비교용 평가 기준](rag/evaluation-policy.json)을 관리합니다.
- 응답의 `handoff`에는 체크포인트, 기준안 변경값, 전체 대안, 평가 기준과 RAG 원본(`rag_result`)이 들어갑니다. [Feature 2](../feature2-sop-ranking/README.md) 평가에도 해당 알람의 검색 근거를 전달합니다.
- [Braatz SOP 생성 스크립트](scripts/build_tep_studio_sop.py)는 [RAG용 SOP JSON](rag/examples/tep_studio/knowledge/tep_studio_braatz_sop.json)과 [센서별 대응표](docs/SOP_TEP_STUDIO.md)를 생성합니다. [라이브 규칙](setpoint_gate/setpoint-sops.json)은 체크인된 파일에서 읽고, [라이브 센서 SOP 인덱스](rag/sop-index.json)는 [tep-sops 인덱스 생성기](scripts/build_sop_index.py)로 구축합니다. Braatz 비교 계획에는 Ricker 체크포인트에서 `simulator_mismatch`가 표시됩니다.

라이브 RAG 인덱스를 처음 준비할 때는 Feature 1 폴더에서 다음을 실행합니다.

```bash
cd rag
ZORO_DEVICE=cpu ZORO_MODEL_CACHE="$PWD/.cache/hub" ZORO_SPARSE_CACHE="$PWD/.cache/fastembed" .venv/bin/python main.py --config config/tep_studio.yaml ingest
```

라이브 RAG 워커는 기본적으로 `rag/.venv/bin/python`을 사용합니다. Python 경로는 `PROTOPILOT_RAG_PYTHON`, 검색 대기 시간은 `PROTOPILOT_RAG_TIMEOUT_SECONDS`(기본 150초)로 설정할 수 있습니다.
