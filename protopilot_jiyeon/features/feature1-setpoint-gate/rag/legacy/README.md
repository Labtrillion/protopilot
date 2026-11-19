# 이전 Zoro RAG · TEP 증상 기반 진단 검색

이전 검색기와 Studio 평가 자료입니다. 외란 번호와 정의는 상위 RAG의 [공용 IDV 카탈로그](../zoro_rag_knowledge/05_Disturbance_knowledge/idv_catalog.json)와 [외란 가이드](../zoro_rag_knowledge/03_Control_and_Variables/TEP_Disturbance_Knowledge.md)를 사용합니다.

관측한 증상과 계측 태그로 공정 문헌과 정성적 진단 가이드 6개를 조회한다. 입력에는 해당 시점까지 관측된 이력만 전달한다. 공개 문헌에 등장하는 외란 번호와 실습 조건은 과거 사례의 속성이다.

```powershell
python rag.py query "반응기 온도가 반복 진동하는데 압력 변화는 작다"
python rag.py query "원료 조성 분석값이 계단처럼 늦게 바뀐다"
python rag.py serve --port 8765
```

Python 호출은 `EvidenceRAG().retrieve(observation, k=5)`, HTTP 호출은 `GET /retrieve?q=...&k=5`를 사용한다. 시나리오 매개변수와 명시적 시나리오 문자 질의는 오류로 처리한다. 실행 정답과 주입 스케줄은 평가기가 소유한다. 공개 재현 시험 로그는 validation/에만 보관한다. 이 폴더는 검색 대상 밖에 있다. 공개 IDV 사전의 직접 조회는 가능하다.

`kind=diagnostic_guide` 결과에는 관측 패턴, 경쟁 가설, 판별에 필요한 관측, 판단의 한계 및 관련 태그가 들어간다. `supporting_sources`는 해석에 활용한 문헌이고, `scope=project_interpretation`은 가설과 판별 질문을 프로젝트에서 작성했음을 뜻한다. 점수는 검색 관련도를 나타낸다. 문헌 실습에는 `published_case`가 붙는다. 등록된 문헌 요약 13개, 진단 가이드 6개, 비용 근거 6개, 경제성 평가 규칙 3개, 팀 SOP 단계 8개를 기본 BM25 인덱스에 포함했다. 원본 문서나 가이드를 추가한 뒤에는 `python rag.py build`로 재생성한다.

전체 구조와 청크 36개 목록, 검색 유형 필터, SOP 단독 대비 비용 평가 실행법은 [ARCHITECTURE.md](ARCHITECTURE.md)에 있다.

## SOP 단계 단위 청킹

기본 SOP는 [TEP-Studio-SOP.pdf](TEP-Studio-SOP.pdf)에 있고, Studio 실행·재현·비교법은 [STUDIO.md](STUDIO.md)에 있다. 팀 SOP는 `Zoro-RAG-Knowledge/SOP/` 폴더에 문서당 JSON 한 개로 저장한다. 아래는 필드 형식 예시다. 대괄호 안 문구를 팀의 실제 SOP 내용으로 채운 뒤 `python rag.py build`를 실행하고 검색 서버를 재시작한다.

```json
{
  "sop_id": "[문서 식별자]",
  "version": "1.0",
  "title": "[문서 제목]",
  "preconditions": ["[문서 전체 적용 조건]"],
  "constraints": [],
  "stop_conditions": [],
  "steps": [{
    "step_id": "S1",
    "title": "[단계 제목]",
    "preconditions": ["[이 단계의 진입 조건]"],
    "procedure": ["[원문 행동 1]", "[원문 행동 2]"],
    "constraints": ["[이 단계의 제약]"],
    "stop_conditions": ["[중단 또는 보류 조건]"]
  }]
}
```

각 검색 결과는 한 단계의 순서 있는 행동, 문서 전체와 해당 단계의 조건, 제약, 중단 조건을 함께 반환한다. 선행 단계 완료가 필수이면 해당 단계의 진입 조건에 명시한다. 출처에 문서 ID·버전·단계 ID·원본 파일명을 기록한다. 선택 필드 `source_url`로 원본 링크를 지정할 수 있다. 같은 SOP ID는 한 번에 한 버전을 인덱싱한다. PDF 입력은 기존 페이지별 파서를 사용하므로 자유 형식 PDF의 단계와 조건을 자동으로 연결하려면 별도 구조화가 필요하다.

## 원본과 하이브리드 검색

인터넷이 가능한 Python 환경에서 아래 순서로 실행한다.

```powershell
python -m pip install -r requirements.txt
python rag.py download --strict
python rag.py build --mode hybrid
python rag.py serve --mode hybrid --port 8765
```

`download`는 원본 포맷을 확인하고 `Zoro-RAG-Knowledge/sources.json`의 경로에 저장한다. Manca PDF 다운로드가 거절되면 보고서 게시 페이지에서 **Download full-text PDF**를 이용해 `Zoro-RAG-Knowledge/04_Alarm_and_Safety/TEP_Alarm_Technical_Report.pdf`에 둘 수 있다. PDF는 페이지별, HTML·Markdown은 섹션별로 파싱한다. 원본이 들어오면 `build`가 요약과 함께 인덱싱하고 원본 SHA-256과 원본 청크 수를 기록한다.

하이브리드 모드는 BGE-M3 임베딩과 BM25 희소 벡터를 Qdrant에 저장하고 RRF로 결합한다. Qwen3-Reranker-0.6B가 후보를 재정렬한다. 첫 실행에 모델 다운로드가 필요하다. 로컬 Qdrant 인덱스는 `index/qdrant`에 저장된다. 서버를 쓰려면 `--qdrant-url` 또는 `QDRANT_URL`을 지정하고 필요 시 `QDRANT_API_KEY`를 설정한다.

## 자료

- Downs & Vogel, *A plant-wide industrial process control problem* (1993), 표 6·8 및 공정 비교. [원본 PDF](https://users.abo.fi/khaggblo/RS/Downs.pdf)
- APMonitor, *Tennessee Eastman Challenge Process Instructions*, 실습 사례와 구현별 외란. [실습 문서](https://apmonitor.com/apps/tennessee-eastman/instructions.php)
- PSE-Research, *Tennessee-Eastman-profBraatz*, 제어 및 계측 태그. [README](https://github.com/PSE-Research/Tennessee-Eastman-profBraatz/blob/master/README.md)
- Gianluca Manca, *TEP Alarm Management Dataset Technical Report*, v1.1, §3·§5. [보고서 게시 페이지](https://www.researchgate.net/publication/355917633_Tennessee-Eastman-Process_Alarm_Management_Dataset_-_Technical_Report)

