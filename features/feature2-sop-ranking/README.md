# Feature 2 · SOP 대응 선택 및 근거 평가

Zoro-1의 `choice`로 SOP 대응이나 다음 행동을 고르고, `score`로 선택한 후보의 근거 또는 적합성을 평가해 후속 판단에 반영한다.

- API 진입점은 `POST /api/zoro-agent`이다. [서버 라우팅](../../server/app.py), [알람 체크포인트 연결](../../server/zoro_bridge.py)
- 후보 선택과 점수 검증은 [에이전트 판단 흐름](../../zoro/zoro_agent/agent.py)과 [Zoro-1 요청·응답 타입](../../zoro/zoro_agent/zoro.py)에서 확인한다.
- 회귀 테스트는 [서버 연결 테스트](../../tests/test_zoro_bridge.py)와 [에이전트 테스트](../../zoro/tests/test_core.py)에 있다.
