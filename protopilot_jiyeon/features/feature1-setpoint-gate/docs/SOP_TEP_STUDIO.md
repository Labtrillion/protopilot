# TEP Studio 센서별 대응 SOP (생성 문서)

`scripts/build_tep_studio_sop.py` 가 `server/tep_control.py`(tep-rust tepsim-control 이식)에서 생성합니다. RAG 용 원본은 `rag/examples/tep_studio/knowledge/tep_studio_braatz_sop.json` 입니다.

원본 코드: <https://github.com/jkitchin/tep-rust/blob/0735ad3b5bb7fdd367a8a31da1db8a209dac2a15/crates/tepsim-control/src/lib.rs>

## 1. 계측별 제어 루프와 대응

| 계측 | 이름 | 단위 | 제어 경로 (코드) | P/PI | 주기 s | 대응 |
|---|---|---|---|---|---|---|
| XMEAS(1) | A Feed (stream 1) | kscmh | CONTRL13 → SETPT(3) → CONTRL3 → XMV(3) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(2) | D Feed (stream 2) | kg/hr | CONTRL14 → SETPT(1) → CONTRL1 → XMV(1) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(3) | E Feed (stream 3) | kg/hr | CONTRL15 → SETPT(2) → CONTRL2 → XMV(2) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(4) | A and C Feed (stream 4) | kscmh | CONTRL17 → SETPT(4) → CONTRL4 → XMV(4) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(5) | Recycle Flow (stream 8) | kscmh | CONTRL5 → XMV(5) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(6) | Reactor Feed Rate (stream 6) | kscmh | - | - | - | 관측·운영자 검토 |
| XMEAS(7) | Reactor Pressure | kPa gauge | - | - | - | 오버라이드 확인 → SETPT(19) case 시뮬레이션 |
| XMEAS(8) | Reactor Level | % | CONTRL17 → SETPT(4) → CONTRL4 → XMV(4) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(9) | Reactor Temperature | Deg C | CONTRL18 → SETPT(10) → CONTRL10 → XMV(10) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(10) | Purge Rate (stream 9) | kscmh | CONTRL19 → SETPT(6) → CONTRL6 → XMV(6) | P | 3 | 오버라이드 확인 → SETPT(19) case 시뮬레이션 |
| XMEAS(11) | Product Sep Temp | Deg C | - | - | - | 관측·운영자 검토 |
| XMEAS(12) | Product Sep Level | % | CONTRL7 → XMV(7) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(13) | Prod Sep Pressure | kPa gauge | - | - | - | 오버라이드 확인 → SETPT(19) case 시뮬레이션 |
| XMEAS(14) | Prod Sep Underflow (stream 10) | m3/hr | - | - | - | 관측·운영자 검토 |
| XMEAS(15) | Stripper Level | % | CONTRL8 → XMV(8) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(16) | Stripper Pressure | kPa gauge | - | - | - | 관측·운영자 검토 |
| XMEAS(17) | Stripper Underflow (stream 11) | m3/hr | CONTRL11 → XMV(11) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(18) | Stripper Temperature | Deg C | CONTRL20 → SETPT(16) → CONTRL16 → SETPT(9) → CONTRL9 → XMV(9) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(19) | Stripper Steam Flow | kg/hr | CONTRL20 → SETPT(16) → CONTRL16 → SETPT(9) → CONTRL9 → XMV(9) | P | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(20) | Compressor Work | kW | - | - | - | 관측·운영자 검토 |
| XMEAS(21) | Reactor Cooling Water Outlet Temp | Deg C | CONTRL18 → SETPT(10) → CONTRL10 → XMV(10) | PI | 3 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(22) | Separator Cooling Water Outlet Temp | Deg C | - | - | - | 관측·운영자 검토 |
| XMEAS(23) | Reactor feed analysis, component A | mol% | CONTRL13 → SETPT(3) → CONTRL3 → XMV(3) | PI | 360 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(24) | Reactor feed analysis, component B | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(25) | Reactor feed analysis, component C | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(26) | Reactor feed analysis, component D | mol% | CONTRL14 → SETPT(1) → CONTRL1 → XMV(1) | PI | 360 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(27) | Reactor feed analysis, component E | mol% | CONTRL15 → SETPT(2) → CONTRL2 → XMV(2) | PI | 360 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(28) | Reactor feed analysis, component F | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(29) | Purge gas analysis, component A | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(30) | Purge gas analysis, component B | mol% | CONTRL19 → SETPT(6) → CONTRL6 → XMV(6) | PI | 360 | 오버라이드 확인 → SETPT(19) case 시뮬레이션 |
| XMEAS(31) | Purge gas analysis, component C | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(32) | Purge gas analysis, component D | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(33) | Purge gas analysis, component E | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(34) | Purge gas analysis, component F | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(35) | Purge gas analysis, component G | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(36) | Purge gas analysis, component H | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(37) | Product analysis, component D | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(38) | Product analysis, component E | mol% | CONTRL20 → SETPT(16) → CONTRL16 → SETPT(9) → CONTRL9 → XMV(9) | PI | 900 | 루프 동작·밸브 포화 확인 (상위 목표 변경 안 함) |
| XMEAS(39) | Product analysis, component F | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(40) | Product analysis, component G | mol% | - | - | - | 관측·운영자 검토 |
| XMEAS(41) | Product analysis, component H | mol% | - | - | - | 관측·운영자 검토 |

최상위 목표(어떤 루프도 덮어쓰지 않는 SETPT): [5, 7, 8, 11, 13, 14, 15, 17, 18, 19, 20]. 이 중 벤치마크 공유 목표로 고정: [7, 8, 11, 17, 18, 20]. 감독기 조정 대상: [19].

## 2. SETPT(19) case 와 하위 루프 즉시 변화

속도형 증분(lib.rs `Loop::increment`)에서 목표를 Δ 바꾸면 비례항이 한 번 `K·Δe` 만큼 움직인다. `Δe = Δ·100/span`, 하위 목표로 가는 출력은 `ΔSETPT(inner) = Δu·span_inner/100`(lib.rs:1050).

| ΔSETPT(19) mol% | ΔSETPT(6) kscmh (즉시) | ΔXMV(6) %p (즉시) | ΔSETPT(6) / 360 s (오차 유지 시) |
|---|---|---|---|
| +0.5 | -0.1067 | -13.01 | -0.00606 |
| -0.5 | +0.1067 | +13.01 | +0.00606 |
| +1 | -0.2133 | -26.03 | -0.01212 |
| -1 | +0.2133 | +26.03 | +0.01212 |
| +1.5 | -0.3200 | -39.04 | -0.01818 |
| -1.5 | +0.3200 | +39.04 | +0.01818 |
| +2 | -0.4267 | -52.05 | -0.02424 |
| -2 | +0.4267 | +52.05 | +0.02424 |

현재 XMV(6) 에 즉시 변화를 더한 값이 0~100% 를 벗어나면 그 case 는 시험하지 않습니다. 기동 시 XMV(6) 은 40.064% 이므로 −2.0 mol% 까지는 범위 안입니다.

## 3. 한계

- 제어기 증분은 측정값이 아직 변하지 않았다는 가정의 즉시 변화다. 공정 응답은 시뮬레이션으로만 확인한다.
- [가설] 표시는 공정 영향 추정이며 코드에서 나온 사실이 아니다.
- SETPT(19) 후보 폭·순서와 허용 범위는 예시 설정이다. 다른 최상위 목표(5, 13, 14, 15)는 격자가 정해지지 않았다.
- 배포된 Studio WASM 이 이 소스 커밋과 같은 빌드인지는 확인할 수 없다.
