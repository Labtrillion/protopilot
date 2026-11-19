# Feature 1 · 설정값 변경 판단

`setpoint_gate/`는 운전 상태에 맞는 SOP 근거와 허용 범위로 대안을 구성합니다. Zoro-1의 `noul`로 임계값을 통과한 상위 두 설정값을 고르고, `choice`로 각각의 변경 방향과 크기를 정합니다. 통과한 항목이 부족하거나 모델이 유지를 고르면 실제 변경은 두 개보다 적을 수 있습니다.

- [설정값 판단 코드](setpoint_gate/setpoint_decision.py)는 `POST /api/setpoint-decision`으로 실행합니다.
- [RAG](rag_jiyeon/README.md)는 독립 검색기, 라이브 센서 SOP 인덱스, 운전 지식, 기존 검증 자료와 [라이브 비교용 평가 기준](rag_jiyeon/evaluation-policy.json)을 관리합니다.
- 응답의 `handoff`에는 체크포인트, 기준안 변경값, 전체 대안, 평가 기준과 출처가 들어갑니다. [Feature 2](../feature2-sop-ranking/README.md)가 이 정보를 사용합니다.
- [SOP 생성 스크립트](scripts/build_tep_studio_sop.py)는 RAG의 TEP Studio 예제 지식과 [센서별 대응표](docs/SOP_TEP_STUDIO.md)를 생성합니다.
