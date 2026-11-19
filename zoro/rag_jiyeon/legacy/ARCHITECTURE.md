# Zoro TEP RAG · 구조와 평가 방법

## 실행 흐름

1. 시뮬레이터가 현재 시각까지 관측한 태그·비용·알람을 제공한다. 모델 입력에 실행 정답, 외란 스위치, 미래 관측을 넣지 않는다.
2. 증상으로 진단 가이드를 찾고 같은 증상으로 적용 후보 SOP 단계를 검색한다. 증가한 비용 컬럼 또는 태그로 비용 근거를 검색한다. 각 검색은 `kind` 필터로 분리할 수 있다.
3. Zoro는 관측 ID와 문헌·SOP 근거 ID를 인용해 가설, 추가 확인, 허용된 후보 조치와 예상 비용 영향을 제안한다. SOP 단계의 전제조건 충족 여부는 현재 관측으로 별도 확인한다.
4. 평가기가 같은 실행 조건에서 SOP 단독 정책과 SOP+Zoro 정책을 시뮬레이션한다. 각 로그에서 비용·생산·제약 결과를 계산한다. 평가 파일과 로그는 검색 코퍼스 외부에 보관한다.

Studio 기본 운전과 경제성 평가는 [STUDIO.md](STUDIO.md) 및 [TEP-Studio-SOP.pdf](TEP-Studio-SOP.pdf)를 사용한다. 아래의 기존 APMonitor 비교기는 APMonitor 필드 형식 전용이다.

## 검색과 청킹

기본 검색은 BM25다. 선택형 하이브리드는 Qdrant에서 BM25 희소 벡터와 BGE-M3 밀집 벡터의 순위를 RRF로 결합한 뒤 Qwen3-Reranker로 후보를 정렬한다. 하이브리드 통합 실행은 모델 설치 환경에서 검증해야 한다.

| 자료 | 청크 단위 | 보존하는 내용 |
|---|---|---|
| 문헌 요약 | 표·주제·공개 실습 한 건 | 공정 정의, 태그, 한계, 실험 조건과 출처. Case 5·6은 문헌값이다. |
| 정성적 진단 | 관측 패턴 하나 | 경쟁 가설, 판별 질문, 측정 태그, 한계, 근거 문헌. 가설은 프로젝트 해석이다. |
| 비용 모델 | 총비용 정의 하나 + 비용 항목별 하나 | 비용률의 의미, 관련 측정값, 단위와 출처. 총비용과 항목별 로그를 연결한다. |
| 팀원 SOP | 문서 버전 내 한 단계 | 전제조건·제약·중단조건은 문서 공통값과 단계값을 함께 반환한다. 조치 순서와 단계 출처를 보존한다. |
| 경제성 평가 | 비교 설계·지표·적격성 각 하나 | SOP 단독 기준선, 적분·차이의 부호, 조기 종료와 생산·품질 기준. 이 세 청크는 프로젝트 평가 규칙이다. |
| 선택적 원본 파일 | PDF 페이지, HTML·Markdown 섹션 | 1,800자 이내 하위 청크와 출처 위치. PDF 텍스트 추출 품질은 원본에 의존한다. 자유 형식 PDF를 SOP 단계로 해석하지 않는다. |
| 외란 지식 사전 | IDV 번호로 직접 조회 | 공개 정의와 구현별 정의. 이 20항목은 검색 청크 수에서 제외한다. |

## 경제성 비교 실행

`python compare.py pair.json`은 APMonitor 형식의 CSV 두 개와 평가 조건을 읽는다. 시간은 h, 생산 유량은 m³/h, 각 비용률은 $/h다. 총비용과 네 항목의 누적값은 실제 표본 간격에 대해 사다리꼴 적분으로 근사한다. 절감액은 SOP 단독 누적 비용에서 SOP+Zoro 누적 비용을 뺀 값이다. 절감률의 분모는 SOP 단독 누적 비용이다. 생산량당 비용의 단위는 $/m³다.

아래 수치는 입력 형식을 설명하는 예시다. 실험 전에 팀의 생산·품질 목표, 공정 운전 한계, 로그 정밀도와 비교 기간을 정해 기입한다. 두 CSV는 실행 전체 또는 동일하게 잘라낸 평가 창의 시작·끝 표본을 포함해야 한다. `bounds`의 범위는 모든 표본에 적용되므로 평가 구간 중 목표나 허용 범위가 바뀌는 시험은 구간별 계약으로 평가한다.

```json
{
  "sop_only_csv": "baseline.csv",
  "sop_zoro_csv": "zoro.csv",
  "contract": {
    "pair_id": "pair-001", "seed": 42,
    "model_version": "CSV의 model_version 값",
    "initial_state": "동일 초기 상태 스냅샷 ID",
    "disturbance_trace": "동일한 외란 실현값 또는 재생 로그 ID",
    "sop_version": "팀 SOP 버전",
    "allowed_actions": "공통 허용 조작 목록 ID",
    "targets": "공통 생산·품질 목표 ID",
    "start_h": 0, "end_h": 24, "max_gap_h": 0.02,
    "min_production_m3": 500, "rounding_tolerance_usd_h": 0.05,
    "bounds": {"g_mass_pct": [45,55], "xmeas07_reactor_press_kpag": [null,2895]}
  }
}
```

비교기는 CSV의 모델 버전, 비용 내역 합계, 관측 기간, 표본 간격, 셧다운, 생산량과 지정된 계측 상·하한을 확인한다. 시드·외란·초기 상태·조작 권한·목표·SOP 버전의 일치는 로그 생성자가 계약에 선언하는 항목이다. 표본 사이의 미기록 제약 위반을 확인하려면 시뮬레이터의 별도 이벤트 로그가 필요하다. 두 정책이 모두 적격일 때만 절감액과 절감률을 출력한다. 한쪽이 실패하면 부분 비용, 생산량과 실패 사유를 보고한다. 측정 대상은 벤치마크 공정 운전비이며 모델 호출·인프라 비용은 별도로 합산해야 순절감액을 구할 수 있다.

한 쌍의 결과로는 그 실행에서의 차이를 말할 수 있다. 팀 발표에서는 사전 선정한 여러 외란·시드를 같은 정책 쌍으로 반복하고, 전체 실행 수와 실패 수를 먼저 제시한다. 양쪽이 적격인 쌍의 비용 차이와 변동 범위를 함께 제시한다.

## 검색 예시

```powershell
python rag.py query "압축기 전력비 XMEAS(20)" --kind cost_model
python rag.py query "반응기 온도 반복 진동" --kind diagnostic_guide
python rag.py query "반응기 온도 반복 진동" --kind sop_step
python rag.py query "생산 감축과 조기 종료" --kind evaluation_rule
```

기본 배포본의 팀 SOP 8단계를 단계별로 검색한다. 진단 가이드는 관측 근거로 반환한다.

## 기본 배포본 청크 전체 목록



| 청크 ID | 종류 | 다루는 내용 |
|---|---|---|
| tep-process | process | TEP 공정 흐름과 재순환 |
| tep-constraints | constraint | 정상 운전 상한과 셧다운 한계 |
| tep-disturbance-comparison | case_study | 논문에서 제안한 복합 외란 비교 |
| ap-cost-5 | case_study | Case 5 · IDV(8) 원료 조성 변동 비용 비교 |
| ap-feed-4 | case_study | Case 4 · A/C 원료비와 분석기 지연 |
| ap-kinetics-6 | case_study | Case 6 · 느린 반응 속도 변화 |
| ap-valve-7 | case_study | Case 7 · 반응기 냉각수 밸브 고착 |
| ap-feed-8 | case_study | Case 8 · A 원료 상실과 압력 오버라이드 |
| braatz-tags | control | TEP 태그와 물리량 |
| braatz-sampling | control | 조성 분석기 샘플링과 데드타임 |
| manca-alarm | constraint | 알람 임계값과 정상화 검증 |
| manca-combined | case_study | 동시 결함 벤치마크 설계 |
| braatz-idv-compatibility | control | Braatz 코드의 외란 조합 지원 |
| cost-total | cost_model | 운전비 J의 네 항목과 총비용 |
| cost-purge | cost_model | 퍼지 손실 비용 |
| cost-product | cost_model | 제품에 섞여 나가는 D·E·F 성분 손실 비용 |
| cost-power | cost_model | 압축기 동력 비용 |
| cost-steam | cost_model | 스트리퍼 스팀 사용 비용 |
| eval-pair | evaluation_rule | SOP 단독과 SOP+Zoro 짝비교 조건 |
| eval-savings | evaluation_rule | 누적 절감액·절감률과 비용 항목별 기여 |
| eval-gates | evaluation_rule | 조기 종료·생산 감축·품질 이탈 판정 |
| guide-thermal-cycle | diagnostic_guide | 반응기 온도 반복 진동과 작은 압력 변화 |
| guide-analyzer-delay | diagnostic_guide | 원료 조성이 뒤늦게 계단처럼 바뀜 |
| guide-cooling-propagation | diagnostic_guide | 반응기와 분리기 온도가 차례로 상승 |
| guide-feed-inventory | diagnostic_guide | 원료 유량 저하와 스트리퍼 레벨 하락 |
| guide-purge-drift | diagnostic_guide | 압력 유지 중 퍼지 증가와 비용 상승 |
| guide-alarm-sequence | diagnostic_guide | 여러 알람이 짧은 간격으로 반복 발생 |
| cost-studio-table9 | cost_model | Studio 측정값에서 표 9의 네 비용 계산 |
| team-sop/tep-studio-closed-loop/S01 | sop_step | 폐루프 기동과 운전 조건 고정 |
| team-sop/tep-studio-closed-loop/S02 | sop_step | 반응기 온도 편차와 냉각 응답 |
| team-sop/tep-studio-closed-loop/S03 | sop_step | 반응기·분리기·스트리퍼 액위와 제품 유량 |
| team-sop/tep-studio-closed-loop/S04 | sop_step | 원료 유량과 지연된 조성 신호 |
| team-sop/tep-studio-closed-loop/S05 | sop_step | 퍼지 변화와 분리기 압력 우선 제어 |
| team-sop/tep-studio-closed-loop/S06 | sop_step | 제품 조성·스트리퍼 온도·스팀 |
| team-sop/tep-studio-closed-loop/S07 | sop_step | 알람 전파와 목표 복귀 판정 |
| team-sop/tep-studio-closed-loop/S08 | sop_step | 안정 운전 중 네 비용과 Zoro 비교 |
