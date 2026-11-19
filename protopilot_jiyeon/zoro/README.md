# Zoro-1 클라이언트

`client.py`는 `POST /v1/systemone`과 이전 `/v1/decide` 서버 호환을 담당합니다. `schemas.py`에는 `noul`, `choice`, `score`의 요청과 응답 타입이 있습니다. 모델 주소는 `ZORO_BASE_URL`, 인증은 `ZORO_API_KEY`로 설정합니다.

업무 흐름은 [설정값 판단](../features/feature1-setpoint-gate/README.md)과 [후속 대안 평가](../features/feature2-sop-ranking/README.md)에 있습니다.
