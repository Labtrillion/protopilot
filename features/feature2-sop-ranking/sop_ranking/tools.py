"""도구 실행 연결부와 가짜 도구.

ToolExecutor 는 이름으로 등록된 함수만 호출한다. 실제 연결 시 장하님 시뮬레이터/상태 조회,
지연님 KG 조회, 동은님 영향 추출을 같은 시그니처 `(ToolRequest) -> ToolResult` 로 감싸 등록한다.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from .tool_io import (
    ExperimentConditions,
    ImpactResult,
    RelationEdge,
    RelationQueryPayload,
    SensorQueryPayload,
    SimulationPayload,
    SimulationRun,
    ToolError,
    ToolRequest,
    ToolResult,
)
from .inputs import Observation

ToolFn = Callable[[ToolRequest], ToolResult]


class ToolExecutor:
    def __init__(self, tools: dict[str, ToolFn], virtual: bool):
        self._tools = dict(tools)
        self.virtual = virtual
        self.calls: list[ToolRequest] = []

    def available(self) -> set[str]:
        return set(self._tools)

    def execute(self, req: ToolRequest) -> ToolResult:
        fn = self._tools.get(req.tool_name)
        if fn is None:
            return ToolResult(
                tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="failed",
                error=ToolError(code="tool_not_registered", message=f"{req.tool_name} 미등록 — 실행 안 함",
                                recoverable=False),
                is_virtual=self.virtual,
            )
        self.calls.append(req)
        try:
            res = fn(req)
        except Exception as e:  # 도구 내부 오류는 실패 결과로 변환
            return ToolResult(
                tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="failed",
                error=ToolError(code="tool_exception", message=repr(e), recoverable=False),
                is_virtual=self.virtual,
            )
        if res.tool_request_id != req.tool_request_id:
            raise ValueError("tool returned mismatched tool_request_id")
        return res


class FakeToolkit:
    """시나리오 스크립트 기반 가짜 도구. 모든 결과는 is_virtual=True (실제 공정/시뮬레이터 아님)."""

    def __init__(self, script: dict[str, Any]):
        self.script = copy.deepcopy(script)
        self._sim_count = 0

    def tools(self) -> dict[str, ToolFn]:
        return {
            "sensor_query": self.sensor_query,
            "relation_query": self.relation_query,
            "run_simulation": self.run_simulation,
        }

    def _fail(self, req: ToolRequest, spec: dict) -> ToolResult:
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name,
                          status=spec.get("status", "failed"), error=ToolError(**spec["error"]),
                          finished_sim_time_s=spec.get("finished_sim_time_s"), is_virtual=True)

    def sensor_query(self, req: ToolRequest) -> ToolResult:
        key = ",".join(sorted(req.args["sensor_ids"]))
        queue = self.script.get("sensor_query", {}).get(key, [])
        spec = queue.pop(0) if queue else {"status": "succeeded", "observations": [], "note": "응답 스크립트 소진"}
        if spec.get("status", "succeeded") != "succeeded":
            return self._fail(req, spec)
        return ToolResult(
            tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="succeeded",
            payload=SensorQueryPayload(observations=[Observation(**o) for o in spec.get("observations", [])],
                                       unavailable_sensor_ids=spec.get("unavailable_sensor_ids", []),
                                       note=spec.get("note")),
            finished_sim_time_s=spec.get("finished_sim_time_s"), is_virtual=True,
        )

    def relation_query(self, req: ToolRequest) -> ToolResult:
        edges = [RelationEdge(**e) for e in self.script.get("relation_query", {}).get("edges", [])]
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name, status="succeeded",
                          payload=RelationQueryPayload(edges=edges), is_virtual=True)

    def run_simulation(self, req: ToolRequest) -> ToolResult:
        sims = self.script["simulation"]
        if req.case is None:
            return self._fail(req, {"error": {"code": "no_case", "message": "case 없는 시뮬레이션 요청",
                                              "recoverable": False}})
        spec = sims[req.case.case_id]
        if isinstance(spec, list):  # 호출마다 다른 응답
            spec = spec.pop(0)
        self._sim_count += 1
        run_id = f"RUN-{self._sim_count:03d}-{req.action_id}"
        d = sims.get("defaults", {})
        run = SimulationRun(
            run_id=run_id, snapshot_id=req.args["snapshot_id"], action_id=req.action_id,
            case_id=req.case.case_id,
            conditions=ExperimentConditions(
                snapshot_id=req.args["snapshot_id"],
                horizon_s=req.args["horizon_s"],
                simulator_version=spec.get("simulator_version", d.get("simulator_version", "unknown")),
                disturbance_profile_id=spec.get("disturbance_profile_id", d.get("disturbance_profile_id", "unknown")),
                seed=spec.get("seed", d.get("seed")),
                initial_composition_sample_id=req.args.get("composition_sample_id"),
            ),
            status=spec.get("status", "succeeded"),
            timeseries_ref=(spec.get("timeseries_ref") or "").replace("{run_id}", run_id) or None,
            error=(spec.get("error") or {}).get("message"),
        )
        impact = None
        if run.status == "succeeded" and spec.get("impact"):
            imp = copy.deepcopy(spec["impact"])
            imp["run_id"] = run_id
            for s in imp.get("evidence_spans", []):
                s["run_id"] = run_id
            if imp.get("score"):
                imp["score"]["run_id"] = run_id
            impact = ImpactResult(**imp)
        error = ToolError(**spec["error"]) if spec.get("error") else None
        status = run.status if run.status in ("succeeded", "failed", "aborted", "running") else "running"
        return ToolResult(tool_request_id=req.tool_request_id, tool_name=req.tool_name, status=status,
                          payload=SimulationPayload(run=run, impact=impact), error=error, is_virtual=True)
