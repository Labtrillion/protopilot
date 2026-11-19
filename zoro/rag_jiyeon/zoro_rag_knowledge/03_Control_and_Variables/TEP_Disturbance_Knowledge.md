# TEP Disturbance Knowledge Guide

Refer to `05_Disturbance_knowledge/idv_catalog.json` for the exact disturbance numbers.
IDV(1) through IDV(15) follow the definitions in Table 8 of Downs–Vogel. IDV(16) through IDV(20) are listed as unknown in that table and in the Braatz README. Implementation-specific names published by APMonitor are available in the `apmonitor` field of each entry.

IDV(4) and IDV(11) represent step and random changes, respectively, in the reactor cooling-water inlet temperature. IDV(5) and IDV(12) provide the same comparison for the condenser cooling-water inlet temperature. IDV(14) and IDV(15) represent sticking of the reactor and condenser cooling-water valves, respectively. IDV(8) is a random disturbance in the A, B, and C composition of stream 4.

Refer to Table 6 of Downs–Vogel for process constraints, APMonitor Disturbances for model-specific disturbances, APMonitor Cases 4–8 for comparable single-disturbance exercises, and Manca §§3 and 5 for the alarm benchmark.

## 한국어

정확한 외란 번호는 `05_Disturbance_knowledge/idv_catalog.json`에서 조회한다.
IDV(1)–(15)는 Downs–Vogel 표 8의 정의를 따른다. IDV(16)–(20)은 그 표와 Braatz README에서 Unknown이다. APMonitor에서 공개한 구현별 이름은 각 항목의 apmonitor 필드에서 조회할 수 있다.

IDV(4)와 IDV(11)은 반응기 냉각수 입구 온도의 스텝과 랜덤 변화다. IDV(5)와 IDV(12)는 응축기 냉각수 입구 온도의 같은 비교다. IDV(14)와 IDV(15)는 각각 반응기·응축기 냉각수 밸브 고착이다. IDV(8)은 스트림 4의 A·B·C 조성에 대한 랜덤 외란이다.

공정 제약은 Downs–Vogel 표 6, 모델별 외란은 APMonitor Disturbances, 비교 가능한 단일 외란 실습은 APMonitor Case 4–8, 알람 벤치마크는 Manca §3·§5를 조회한다.
