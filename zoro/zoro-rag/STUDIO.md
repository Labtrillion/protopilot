# TEP Studio 폐루프 SOP v1.0 실행 안내

팀이 작성한 시뮬레이션용 SOP다. 실행기는 CMU TEP Studio의 Braatz 제어기이며, 절차는 그 제어기가 실제로 실행하는 반응과 운영자가 확인할 신호를 연결한다. SOP를 읽는 사람이 3초마다 밸브를 직접 조정할 필요는 없다.

- 사람이 읽는 절차와 시험 결과는 **TEP-Studio-SOP.pdf**, 검색용 원본은 **Zoro-RAG-Knowledge/SOP/tep-studio-closed-loop.json**이다.
- 평가기는 **studio_eval.py**다. 평가용 원본 CSV 3개와 수치 결과는 **validation/**에만 둔다. 에이전트에게는 이 폴더나 원본 CSV 헤더를 전달하지 않는다.

## 기준선 재현

1. [TEP Studio](https://kitchingroup.cheme.cmu.edu/tep-rust/studio/)를 연다. Seed 4651207995, Hours 48, Step 1/3600 h, Sample every 180, Integrator Euler로 둔다.
2. **closed loop**와 **a trip ends the run**을 켠다. **driver forces IDV(12) at 8 h**를 끈다. 기동 시점에 시험할 외란을 선택하고 **Start**를 누른다. 세 번의 검증 운전에서 수동 밸브 명령이나 상위 목표 조정은 없었다.
3. **completed**, **960 of 960**을 확인하고 **Download CSV**를 누른다. 종료된 운전은 실패 이력을 보존한다. 아래 명령은 패키지를 푼 폴더에서 실행한다.

```powershell
python studio_eval.py validation/cooling_step.csv
python studio_eval.py validation/cooling_combined.csv
python rag.py query "반응기 온도 냉각 응답" --kind sop_step --k 1
```

| 재현 파일 | 외란 설정 | Studio 출력 체크섬 | 평균 $/h | 누적 $ | 생산 m³ |
|---|---|---|---:|---:|---:|
| nominal.csv | 없음 | 2f3dbc9ddc1dc8d6 | 171.159 | 8,207.07 | 1,100.06 |
| cooling_step.csv | IDV(4), 기동부터 유지 | 29038e089e029123 | 171.159 | 8,207.06 | 1,100.06 |
| cooling_combined.csv | IDV(4,5,11), 기동부터 유지 | f8ca1c29465bfe2b | 171.049 | 8,201.82 | 1,099.20 |

각 CSV 헤더에 배포 버전(tepsim-wasm 0.0.0), 시나리오 토큰과 체크섬이 있다. 세 운전은 공개 회귀 시험이다. 위의 세 비용은 모두 SOP 단독 결과다. 48시간 실행 중 첫·마지막 저장 시각은 약 0.049722 h와 47.999722 h이다. 비용·생산량 적분은 관측된 47.95시간에만 적용한다.

## 판정 기준

**목표 유지와 복귀.** 3분마다 반응기 온도 120.40±0.50 °C, 반응기·분리기·스트리퍼 액위 각각 75/50/50±5 %p를 함께 확인한다. 전체 기간 이탈이 없으면 유지, 이탈 후 네 신호가 연속 1시간 허용 범위에 들면 복귀를 확인한다. 다시 이탈하면 최종 재진입 시각을 별도로 기록한다. 모든 운전에서 네 신호가 범위를 유지했다. 반응기 온도의 ±0.10 °C 미세 추종은 별도 지표이며 팀이 정한 실험 허용폭이다.

**비용.** Downs & Vogel 표 9의 명목 환산(퍼지 44.79 kmol/천 표준 m³, 제품 9.21 kmol/m³)과 성분별 단가를 모든 정책에 고정한다. 제품의 조성·온도 의존 실제 몰밀도 및 APMonitor의 제품 밸브 유량 추정과 차이가 있을 수 있다. RAG의 cost-studio-table9 및 studio_eval.py에 식이 있다. 음의 분석기 측정값을 0으로 자르고 각 스트림의 몰분율 합을 1로 정규화한다. 비용률을 실제 표본 시각에서 사다리꼴 적분한다. 실험실 현금 지출이 아닌 벤치마크 달러다.

**공정 조건.** 팀의 데모 채점 기준은 무트립 완주, 마지막 2시간 네 주요 변수 유지, 평균 제품 유량 ≥ 22.949×0.99 m³/h, 제품 G/(G+H) 질량비 45–55%, 제품 D+E+F ≤ 2 mol%, 반응기 압력 ≤ 2895 kPa(g)다. 생산량은 정책 간 차이도 기준선 대비 1% 이내여야 한다. 압력·품질 기준은 기록된 모든 표본에 적용한다. 3분 표본 사이의 순간 이탈 여부는 이 로그로 판단할 수 없다. 실제 트립은 활성화된 시뮬레이터가 감지한다. 허용폭·관찰시간·비열등 기준은 팀 설계이며 최종 시험 전에 고정한다.

## Zoro 입력, 출력, 실행 경계

| 구분 | 계약 |
|---|---|
| 실시간 입력 | 현재까지의 XMEAS/XMV 이력, 측정 시각·단위, 적용 중인 상위 목표, 알람/보호 상태, 새 제품 시료 여부, 네 비용률, 직전 조치와 검색한 SOP 단계 ID. CSV의 fault·hours_since_onset·시나리오 토큰·미래 행은 제외한다. |
| Zoro 출력 | hold 또는 propose_setpoint, 근거 SOP ID, 대상 상위 루프, 목표값과 단위, 예상 비용 항목별 변화의 방향, 확인할 신호, 재검토 시각, 원복 조건. 예상과 실제 절감액은 별도 필드에 남긴다. |
| 최초 비교 후보 | 정상 복귀 후 가장 큰 비용 항목인 퍼지 손실에 대해 상위 퍼지 B 목표 SETPT(19), 기본 13.823 mol%를 후보로 둘 수 있다. 변경 방향의 효과는 응답·조성·압력으로 시험한다. 생산·외부 품질·반응기 온도 목표는 두 정책이 공유한다. |
| 조치 적용 | 감독기가 허용값·변화율·대기시간을 사전에 정하고 두 정책에 동일하게 적용한다. 보호 작동·오래된 시료·품질 이상은 hold/원복 사유다. 작동 중인 Scheme의 set_setpoint(19, value)에 연결하는 별도 시뮬레이터 어댑터가 필요하다. 공개 Studio 화면의 외란 선택은 이 조치를 대신할 수 없다. |
| 감독기 기록 | 정책/모델 버전, 실행기 빌드, 시드, 초기상태, 외란 스케줄, 제약 계약, 조치 제안·실제 적용·거절·원복 및 시각을 평가기가 보관한다. 평가판 두 정책 모두 같은 빌드를 사용하고 hold-only로 기본 체크섬을 먼저 재현한다. |

짝비교는 같은 시나리오에 대해 실제 Zoro 조치를 적용해 얻은 Studio 형식 CSV가 있을 때 실행한다.

```powershell
python studio_eval.py validation/cooling_step.csv --zoro-csv zoro_cooling_step.csv
```

평가기는 CSV의 시나리오, 모델 표시, 표본 시각·개수와 공정 조건을 대조한다. 외부 감독기의 실제 조치·빌드 일치는 별도 이력으로 확인한다. 누적 절감액은 SOP 단독 비용에서 SOP+Zoro 비용을 뺀 값이다. 생산량 및 $/m³도 함께 공개한다. 한쪽이 트립하거나 조건을 어긴 경우 그 쌍의 절감률은 비워 두고 실패 횟수에 포함한다. 추론/API 또는 컴퓨팅 비용을 포함한 순절감은 해당 실비를 별도로 차감한다. 검증용 한 시드와 별도로 미사용 외란·복수 시드의 짝별 차이와 실패율을 함께 제시한다. 패키지에 포함된 세 실행에는 Zoro 조치가 없다.

## 문헌과 구현의 역할

- [CMU TEP Studio](https://kitchingroup.cheme.cmu.edu/tep-rust/studio/) 및 [검토한 제어 소스](https://github.com/jkitchin/tep-rust/blob/0735ad3b5bb7fdd367a8a31da1db8a209dac2a15/crates/tepsim-control/src/lib.rs). PRESET, FAST/COMPOSITION/QUALITY, Override::step, Scheme::run, Scheme::set_setpoint를 참조했다. 이 소스 스냅샷과 배포 WASM의 동일 빌드 여부는 CSV의 버전 표지만으로 확인할 수 없다.
- [Manca, TEP Alarm Management Dataset Technical Report v1.1](https://www.researchgate.net/publication/355917633_Tennessee-Eastman-Process_Alarm_Management_Dataset_-_Technical_Report). PDF 9–10쪽은 원인·전파와 회복 지연, 14쪽은 조성 시료 주기와 지연, 28–29쪽은 알람 발생·해제 기준을 다룬다. 이 보고서의 운전점과 제어 구조는 다르므로 여기의 상위 목표·밸브 매핑은 Studio 코드에서 가져왔다.
- [Downs & Vogel (1993), Table 9](https://users.abo.fi/khaggblo/RS/Downs.pdf#page=7). 손실·유틸리티 단가와 명목 몰유량 환산의 출처다.
