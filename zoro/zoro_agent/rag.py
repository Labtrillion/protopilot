"""경계 A-2: RAG/KG 결과 정규화 (지연 → 에이전트).

외부 RAG 형식은 바뀔 수 있으므로 어댑터 한 곳만 고치면 되도록 분리한다.
- action_id/조건은 카탈로그 정확 일치 또는 합의된 별칭으로만 매핑한다. 나머지는 unmapped 로 보존.
- retrieval_score 는 검색 관련도일 뿐, 실행 가능성·안전성으로 쓰지 않는다 (옵션 구성기에서 미사용).
"""
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from .common import Model
from .registry import ActionCatalog


class Evidence(Model):
    evidence_id: str
    doc_id: str
    doc_version: str | None = Field(description="null = 출처가 버전을 제공하지 않음 (불확실성으로 보고)")
    doc_title: str | None = None
    clause: str | None = Field(description="조항/섹션 번호. null = 미제공")
    text: str = Field(description="원문 발췌")
    source_uri: str | None = None
    economic: bool = False
    sop_id: str | None = None
    sop_version: str | None = None
    retrieval_score: float | None = Field(
        default=None, description="검색 관련도. 실행 가능성·안전성·정답 확률이 아님"
    )


class RagCandidate(Model):
    raw_ref: str = Field(description="RAG가 반환한 원래 참조 문자열")
    action_id: str | None = Field(description="등록 action_id. null = 등록 행동과 매핑되지 않음")
    mapping: Literal["exact", "alias", "unmapped"]
    purpose: str
    evidence_ids: list[str]
    condition_ids: list[str] = Field(default=[], description="등록 조건으로 매핑된 적용·확인 조건")
    unmapped_condition_refs: list[str] = Field(default=[], description="매핑 못한 조건 → unknown 으로 취급")


class UnresolvedItem(Model):
    """검색 결과만으로 판단할 수 없는 내용."""

    topic: str
    detail: str
    evidence_ids: list[str] = []


class NormalizedRagResult(Model):
    schema_version: Literal["rag_normalized.v0"] = "rag_normalized.v0"
    query_id: str
    adapter: str
    evidence: list[Evidence]
    candidates: list[RagCandidate]
    unresolved: list[UnresolvedItem] = []
    warnings: list[str] = []

    def evidence_ids(self) -> set[str]:
        return {e.evidence_id for e in self.evidence}

    def candidates_for(self, action_id: str) -> list[RagCandidate]:
        return [c for c in self.candidates if c.action_id == action_id]

    def without_economic_evidence(self) -> NormalizedRagResult:
        return self.model_copy(update={"evidence": [e for e in self.evidence if not e.economic]})


class RagAdapter(Protocol):
    name: str

    def normalize(self, raw: dict[str, Any], catalog: ActionCatalog) -> NormalizedRagResult: ...


class ExampleRagV0Adapter:
    """임시 원본 형식 `{query_id, retrieved[], kg{suggested_actions[], notes[]}}` 용 어댑터.

    지연님 RAG 출력 형식이 확정되면 새 어댑터를 추가하고 이 클래스는 그대로 둔다.
    """

    name = "example_rag_v0"

    def normalize(self, raw: dict[str, Any], catalog: ActionCatalog) -> NormalizedRagResult:
        warnings: list[str] = []
        evidence: list[Evidence] = []
        chunk_to_ev: dict[str, str] = {}
        for hit in raw.get("retrieved", []):
            chunk = hit["chunk_id"]
            ev_id = f"EV:{chunk}"
            chunk_to_ev[chunk] = ev_id
            doc = hit.get("doc") or {}
            if not doc.get("version"):
                warnings.append(f"{ev_id}: 문서 버전 미제공")
            evidence.append(
                Evidence(
                    evidence_id=ev_id,
                    doc_id=doc.get("id", "UNKNOWN"),
                    doc_version=doc.get("version"),
                    doc_title=doc.get("title"),
                    clause=hit.get("section"),
                    text=hit.get("content", ""),
                    source_uri=hit.get("uri"),
                    economic=hit.get("kind") == "cost_model" or doc.get("kind") == "cost_model",
                    retrieval_score=hit.get("score"),
                )
            )

        def map_support(refs: list[str], owner: str) -> list[str]:
            out = []
            for r in refs:
                if r in chunk_to_ev:
                    out.append(chunk_to_ev[r])
                else:
                    warnings.append(f"{owner}: 근거 참조 {r!r} 가 검색 결과에 없음 → 제외")
            return out

        kg = raw.get("kg") or {}
        candidates: list[RagCandidate] = []
        for s in kg.get("suggested_actions", []):
            ref = s["ref"]
            action_id, mapping = catalog.resolve_action_ref(ref)
            if mapping == "unmapped":
                warnings.append(f"후보 {ref!r}: 등록 행동과 매핑되지 않음 (임의 매핑 안 함)")
            cond_ids, unmapped_conds = [], []
            for c in s.get("conditions", []):
                cid = catalog.resolve_condition_ref(c)
                (cond_ids if cid else unmapped_conds).append(cid or c)
            candidates.append(
                RagCandidate(
                    raw_ref=ref,
                    action_id=action_id,
                    mapping=mapping,
                    purpose=s.get("purpose", ""),
                    evidence_ids=map_support(s.get("support", []), ref),
                    condition_ids=cond_ids,
                    unmapped_condition_refs=unmapped_conds,
                )
            )
        unresolved = [
            UnresolvedItem(
                topic=n.get("topic", ""),
                detail=n.get("text", ""),
                evidence_ids=map_support(n.get("support", []), f"note:{n.get('topic')}"),
            )
            for n in kg.get("notes", [])
        ]
        return NormalizedRagResult(
            query_id=raw.get("query_id", "UNKNOWN"),
            adapter=self.name,
            evidence=evidence,
            candidates=candidates,
            unresolved=unresolved,
            warnings=warnings,
        )


class JiyeonRagAdapter:
    """지연님 RAG(`rag_jiyeon`, zoro_rag.AlarmResult) 출력 → NormalizedRagResult.

    - evidence: source_document/section/page/source_version 을 그대로 옮긴다. 점수는 RRF 관련도일 뿐이다.
    - candidate_actions: SOP 단계의 simulation_review 계획에서 `candidates`(catalog_action_id) 와 `baseline` 만
      후보로 만든다. 카탈로그 정확 일치 또는 합의 별칭만 매핑한다.
    - status 가 simulator_mismatch 인 계획은 후보로 쓰지 않는다.
    - alarm_context(시뮬레이터 정보 포함)는 가져오지 않는다. 관측은 EventInput 경로로만 받는다.
    """

    name = "rag_jiyeon_alarm_result_v1"
    # 계획의 전제 중 에이전트가 자체 검사로 확인하는 것 (조건으로 매핑하지 않음)
    NATIVE_PRECONDITIONS = {"active_SETPT(19)_known": "setpoint_envelope 검사가 현재값 제공 여부를 확인"}

    def normalize(self, raw: dict[str, Any], catalog: ActionCatalog) -> NormalizedRagResult:
        warnings: list[str] = []
        evidence = []
        for e in raw.get("evidence", []):
            prov = e.get("provenance") or {}
            procedure = prov.get("procedure") or {}
            clause = e.get("source_section") or (f"p.{e['source_page']}" if e.get("source_page") else None)
            if not e.get("source_version"):
                warnings.append(f"{e['evidence_id']}: 문서 버전 미제공")
            evidence.append(Evidence(
                evidence_id=e["evidence_id"], doc_id=e.get("source_document", "UNKNOWN"),
                doc_version=e.get("source_version"), doc_title=e.get("source_document"), clause=clause,
                text=procedure.get("instruction_ko") or e.get("statement") or e.get("supporting_quote", ""),
                source_uri=e.get("source_url"), sop_id=procedure.get("document_id"),
                sop_version=procedure.get("document_version"),
                retrieval_score=prov.get("rrf_score"), economic=e.get("evidence_type") == "ECONOMIC"))
        ev_ids = {e.evidence_id for e in evidence}
        candidates: list[RagCandidate] = []
        for act in raw.get("candidate_actions", []):
            where = f"{act.get('procedure_id')}/{act.get('step_id')}"
            if act.get("status") == "simulator_mismatch":
                warnings.append(f"{where}: 다른 시뮬레이터용 계획 → 후보로 쓰지 않음")
                continue
            for reason in act.get("input_review_reasons", []):
                warnings.append(f"{where}: {reason}")
            plan = act.get("plan") or {}
            support = [act["evidence_id"]] if act.get("evidence_id") in ev_ids else []
            if not support:
                warnings.append(f"{where}: 근거 evidence_id 가 결과에 없음")
            cond_ids, unmapped = [], []
            for c in plan.get("preconditions", []):
                if c in self.NATIVE_PRECONDITIONS:
                    continue
                cid = catalog.resolve_condition_ref(c)
                (cond_ids if cid else unmapped).append(cid or c)
            refs = [(c[0], f"{plan.get('parameter')} {c[1]:+g}") for c in plan.get("candidates", [])]
            if plan.get("baseline"):
                refs.append((plan["baseline"], "기준(SOP 단독, hold)"))
            for ref, purpose in refs:
                action_id, mapping = catalog.resolve_action_ref(ref)
                if mapping == "unmapped":
                    warnings.append(f"후보 {ref!r}: 등록 행동과 매핑되지 않음 (임의 매핑 안 함)")
                candidates.append(RagCandidate(
                    raw_ref=ref, action_id=action_id, mapping=mapping, purpose=f"{where}: {purpose}",
                    evidence_ids=support, condition_ids=cond_ids, unmapped_condition_refs=unmapped))
        unresolved = [UnresolvedItem(topic="RAG 미해결 질문", detail=q) for q in raw.get("unresolved_questions", [])]
        unresolved += [UnresolvedItem(topic="RAG 누락 근거", detail=m) for m in raw.get("missing_evidence", [])]
        return NormalizedRagResult(query_id=raw.get("event_id", "UNKNOWN"), adapter=self.name, evidence=evidence,
                                   candidates=candidates, unresolved=unresolved, warnings=warnings)
