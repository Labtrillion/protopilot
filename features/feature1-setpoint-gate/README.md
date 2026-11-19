# Feature 1 · 설정값 변경 판단

Zoro-1의 `noul`로 변경이 필요한 설정값을 판별하고, 통과한 항목은 `choice`로 허용된 변경 방향과 크기를 고른다.

- API 진입점은 `POST /api/setpoint-decision`이다. [서버 라우팅](../../server/app.py)
- 설정값 후보, 판단 기준, 시뮬레이션 분기 흐름은 [기존 설정값 판단 코드](../../server/setpoint_decision.py)에서 확인한다.
- 회귀 테스트는 [설정값 판단 테스트](../../tests/test_setpoint_decision.py)에 있다.
