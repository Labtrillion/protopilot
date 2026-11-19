# Feature 1 · 설정값 변경 판단

관제 화면의 **Zoro 대응 검토**는 `POST /api/setpoint-decision`에 실행 ID와 알람 ID를 보냅니다. [설정값 판단 코드](setpoint_gate/setpoint_decision.py)가 알람 발생 시점의 계측, 제어 목표, 최근 조작 이력을 사용합니다. [검색 연동 코드](setpoint_gate/zoro_inference/__init__.py)는 해당 알람의 근거와 SOP 계획을 조회해 각 출처와 함께 Zoro-1에 전달합니다.

- `noul`이 0.5 이상이고 허용 범위 안에 변경 후보가 있는 설정값 중 확률이 높은 두 항목을 고릅니다. `choice`가 각 항목의 방향과 폭을 선택합니다. 생산량은 ±5%·±10%, 그 외 설정값은 ±2%·±4% 조합을 사용합니다.
- 검색 근거가 부족하거나 두 변경을 확정할 수 없으면 `human_review`와 빈 변경 목록을 반환합니다. 기준안을 확정하면 응답의 `handoff`로 체크포인트, 기준안, [평가 기준](rag/evaluation-policy.json)과 검색 결과(`rag_result`)를 [Feature 2](../feature2-sop-ranking/README.md)에 전달합니다.
- 배포용 검색기는 [TEP Studio 검색 설정](rag/config/tep_studio.yaml)으로 문헌과 SOP를 색인하고, 현재 공정의 Ricker 제어기와 다른 제어 방식이 필요한 계획은 `simulator_mismatch`로 표시합니다. 현재 운전 규칙은 [Ricker 대응 규칙](setpoint_gate/setpoint-sops.json)에서 읽습니다.

### 배포용 RAG 인덱스

검색기 의존성을 `features/feature1-setpoint-gate/rag/.venv`에 준비한 뒤 저장소 루트에서 인덱스를 구축합니다. Linux의 의존성·모델 설치는 [설치 스크립트](rag/scripts/setup_linux.sh)를 참고하세요.

```bash
cd features/feature1-setpoint-gate/rag
ZORO_DEVICE=cpu ZORO_MODEL_CACHE="$PWD/.cache/hub" ZORO_SPARSE_CACHE="$PWD/.cache/fastembed" .venv/bin/python main.py --config config/tep_studio.yaml ingest
```

검색 워커는 `rag/.venv/bin/python`이 있으면 해당 환경을, 없으면 관제 서버의 Python을 사용합니다. Python 경로는 `PROTOPILOT_RAG_PYTHON`, 검색 대기 시간은 `PROTOPILOT_RAG_TIMEOUT_SECONDS`(기본 150초)로 설정할 수 있습니다.
