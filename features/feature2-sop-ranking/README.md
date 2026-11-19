# Feature 2 · 미변경 설정값 대안 평가

`POST /api/setpoint-decision`의 `feature2` 결과에는 선정 후보, 분기 지표, 평가 근거 ID와 최종 권고가 들어갑니다.

1. [후보 선별](sop_ranking/score_selection.py)은 Feature 1 기준안이 변경한 설정값을 제외하고, 남은 설정값마다 Zoro `score`를 하나씩 받습니다. 허용 범위 안에서 ±10% 조정이 가능한 항목 중 점수가 높은 두 개를 고릅니다. 각 항목의 −10%와 +10%를 교차해 최대 4개 조합을 비교합니다. 허용 범위를 벗어나는 방향은 제외합니다. `choice`는 첫 시험 조합의 순서에만 쓰며, 두 요청 모두 Feature 1이 전달한 RAG 근거를 `alarm_rag`로 받습니다.
2. [분기 비교](sop_ranking/evaluate.py)는 같은 체크포인트에서 모든 조합에 Feature 1 기준안의 변경을 고정하고, 선정된 두 항목을 각 조합의 방향으로 ±10% 조정합니다. Feature 1 기준안은 비교 기준으로 따로 평가합니다.
3. [효율성 판정](sop_ranking/efficiency.py)은 RAG의 생산량·품질·복귀 적격 기준과 기존 벤치마크의 비용·추종 정확도 비교를 사용합니다. 비용과 정확도가 동률이면 생산 손실이 작은 쪽을 선택합니다. 더 나은 적격 대안이 없으면 기준안을 권고하고, 적격 결과가 없으면 운영자 검토로 반환합니다.
4. 결과 화면은 Feature 1 기준안과 Feature 2 대안의 전체 계획을 Zoro로 따로 채점하고, 이 원점수에 temperature 0.15로 softmax를 적용한 상대 적합도를 표시합니다.

기존 알람 대응 화면의 `POST /api/zoro-agent`와 퍼지 시나리오 예제는 [알람 에이전트 데모](docs/AGENT_DEMO.md)를 참고하세요.
