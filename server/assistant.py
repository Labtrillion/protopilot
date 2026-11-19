"""Zoro-1 scenario routing with a local, deterministic fallback."""

import json
import logging
import math
import os
import re
from time import perf_counter
from dataclasses import dataclass
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .recording import encode, timestamp
from .scenario import DEFAULT_SEED

ROUTES = {
    "run_scenario": "Run one simulated process scenario, including faults or setpoint changes.",
    "run_sweep": "Run matched scenarios or a baseline/control comparison across conditions or seeds.",
    "compare_runs": "Compare previously stored simulations by run ID.",
    "get_run": "Read the summary of one existing experiment by run ID.",
    "get_run_series": "Read time-series data for a named variable in an existing run.",
    "list_runs": "List existing experiment IDs or outcomes.",
    "describe_plant": "Explain simulator variables, constraints or supported features.",
}
FALLBACK = "로컬 규칙"
TRACE_LOCK = Lock()


@dataclass
class AssistantTurn:
    text: str
    run_ids: list[str]
    history: list[dict]


def _request(url, payload, *, timeout):
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("ZORO_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, data=encode(payload).encode(), headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(512 * 1024))


def _zoro_endpoint():
    base = os.environ.get("ZORO_BASE_URL", "").rstrip("/")
    if not base:
        raise ValueError("ZORO_BASE_URL 설정 없음")
    endpoint = f"{base}/v1" if not base.endswith("/v1") else base
    return endpoint, os.environ.get("ZORO_MODEL", "trillionlabs/Zoro-1")


def zoro_decide(state, questions, *, timeout=5):
    """Ask Zoro-1's typed-decision head; return its raw `answers` after checking each fits its question."""
    endpoint, model = _zoro_endpoint()
    decision_request = {"state": state, "questions": questions}
    try:
        decision = _request(f"{endpoint}/systemone", {"model": model, **decision_request, "reasoning": False},
                            timeout=timeout)
    except HTTPError as error:
        if error.code not in (404, 405):
            raise
        decision = _request(f"{endpoint}/decide", {"request": decision_request, "reasoning": False}, timeout=timeout)
    answers = decision["answers"]
    for name, question in questions.items():
        answer = answers[name]
        if question["type"] == "noul":
            value = float(answer["noul"])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Zoro-1 판단 형식 오류")
        elif answer["choice"] not in question["criteria"]:
            raise ValueError("Zoro-1 판단 형식 오류")
    return answers


def zoro_choices(state, questions):
    decision = {"answers": zoro_decide(state, questions)}
    choices = {}
    for name, question in questions.items():
        answer = decision["answers"][name]
        choice, confidence = answer["choice"], float(answer["confidence"])
        if choice not in question["criteria"] or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Zoro-1 판단 형식 오류")
        if confidence < 0.5:
            raise ValueError("Zoro-1 판단 신뢰도가 낮음")
        choices[name] = {"choice": choice, "confidence": confidence}
    return choices


def append_trace(path, trace):
    try:
        with TRACE_LOCK, path.open("a", encoding="utf-8") as stream:
            stream.write(encode(trace) + "\n")
    except OSError:
        logging.exception("Could not write Zoro-1 execution trace")
        return False
    return True


def _zoro_plan(text, history, tools):
    endpoint, model = _zoro_endpoint()
    state = {"request": text, "recent_turns": history[-4:]}
    question = {"type": "choice", "instructions": "Choose the simulation tool that fulfills the request.", "criteria": ROUTES}
    answer = zoro_choices(state, {"tool": question})["tool"]
    route, confidence = answer["choice"], answer["confidence"]
    if route in {"describe_plant", "list_runs"}:
        return route, {}, confidence
    schema = next(spec["input_schema"] for spec in tools.tool_specs() if spec["name"] == route)
    catalog = tools.describe_plant() if route.startswith("run_") else {"stored_runs": tools.store.ids()}
    reply = _request(f"{endpoint}/chat/completions", {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                f"Construct arguments for {route}. Return one JSON object with no code fence or commentary. "
                f"JSON schema: {encode(schema)}. Use only known names. Default to seed {DEFAULT_SEED}. "
                f"Scenario catalog: {encode(catalog)}"
            )},
            {"role": "user", "content": encode({"request": text, "recent_turns": history[-4:]})},
        ],
        "temperature": 0,
        "max_tokens": 1536,
        "chat_template_kwargs": {"enable_thinking": False},
    }, timeout=20)
    content = reply["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^<think>.*?</think>\s*", "", content, flags=re.DOTALL)
    content = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", content)
    arguments = json.loads(content)
    if not isinstance(arguments, dict):
        raise ValueError("Zoro-1 도구 인수 형식 오류")
    return route, arguments, confidence


def _local_plan(text, history):
    raw = text.strip()
    if raw.startswith("{"):
        config = json.loads(raw)
        if isinstance(config, dict):
            return "run_scenario", {"config": config}
    normalized = raw.lower()
    ids = re.findall(r"\b(?:open|closed)_[a-f0-9]{8}\b", normalized)
    if not ids and re.search(r"이전|직전|previous|last", normalized):
        ids = [run_id for turn in history[-6:] for run_id in turn.get("run_ids", [])]
    if ids:
        return ("compare_runs", {"run_ids": ids}) if len(ids) > 1 else ("get_run", {"run_id": ids[0]})
    if re.search(r"목록|list runs|실행 이력", normalized):
        return "list_runs", {}
    if not re.search(r"실행|시뮬레이션|비교|\brun\b|\bsimulate\b|\bidv\s*\(?\d|baseline", normalized):
        return "describe_plant", {}
    mode = re.search(r"(?:mode|모드)\s*([1-6])(?!\d)", normalized)
    onset = re.search(r"(\d+(?:\.\d+)?)\s*(?:시간|hours?\b|h\b)\s*(?:후|부터|이후|onset)", normalized)
    duration = normalized if onset is None else normalized[:onset.start()] + normalized[onset.end():]
    horizon = re.search(r"(\d+(?:\.\d+)?)\s*(?:시간|hours?\b|h\b)", duration)
    seed = re.search(r"(?:seed|시드)\s*(\d+)", normalized)
    faults = list(dict.fromkeys(re.findall(r"\bidv\s*\(?\s*0*(\d{1,2})(?!\d)", normalized)))
    config = {
        "name": "agent_" + ("_".join(f"IDV{i}" for i in faults) if faults else "baseline"),
        "mode": f"mode{mode[1]}" if mode else "mode1",
        "loop_type": "open" if re.search(r"개루프|open[ -]loop", normalized) else "closed",
        "horizon": float(horizon[1]) if horizon else 12.0,
        "seed": int(seed[1]) if seed else DEFAULT_SEED,
        "disturbances": [{"idv": f"idv_{int(i):02d}", "start_time": float(onset[1]) if onset else 1.0} for i in faults],
    }
    if faults and re.search(r"비교|compare", normalized):
        baseline = {**config, "name": "agent_baseline", "disturbances": []}
        return "run_sweep", {"configs": [baseline, config], "seeds": [config["seed"]]}
    return "run_scenario", {"config": config}


def _summary(result, route, run_ids):
    if result.get("ok") is False:
        return str(result.get("error", "실험 요청을 확인하세요."))
    if route == "describe_plant":
        return "Tennessee Eastman 공정은 6개 운전 모드, 기본 계측 41개, 조작값 12개, 외란 28개, 설정값 9개를 지원합니다. 실험에서 실행할 시간과 외란을 지정할 수 있습니다."
    runs = result.get("runs")
    if route == "get_run_series" or route == "compare_runs":
        return "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```"
    if isinstance(runs, list):
        return "실행 " + str(len(runs)) + "건\n\n" + "\n".join(
            f"- `{run['run_id']}` · {run.get('name', '')} · 최대 반응기 압력 {run.get('peak_reactor_pressure', '—')} kPa(g)"
            for run in runs if "run_id" in run)
    if run_ids:
        return (f"`{run_ids[-1]}` · 종료 {result.get('final_time', '—')} h · "
                f"최대 반응기 압력 {result.get('peak_reactor_pressure', '—')} kPa(g) · "
                f"{'트립 발생' if result.get('terminated') else '트립 없음'}")
    return "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```"


def respond(text, history, tools, trace_path):
    started = perf_counter()
    model, confidence, fallback_reason = "Zoro-1", None, None
    route, ids = "describe_plant", []
    decision_ms = None
    try:
        try:
            route, arguments, confidence = _zoro_plan(text, history, tools)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, URLError, TimeoutError) as error:
            model = FALLBACK
            fallback_reason = type(error).__name__ if os.environ.get("ZORO_BASE_URL") else "not_configured"
            route, arguments = _local_plan(text, history)
        decision_ms = round((perf_counter() - started) * 1000, 2)
        result = tools.dispatch(route, arguments)
        if route == "run_scenario" and result.get("ok"):
            ids = [result["run_id"]]
        if route == "run_sweep":
            ids = [run["run_id"] for run in result.get("runs", result.get("completed_runs", []))]
        answer = _summary(result, route, ids)
    except (TypeError, ValueError, KeyError, RuntimeError) as error:
        result, ids, answer = {"ok": False}, [], str(error)
    trace = {"time_utc": timestamp(), "decision_id": uuid4().hex, "model": model,
             "fallback": model == FALLBACK, "fallback_reason": fallback_reason, "confidence": confidence,
             "decision_ms": decision_ms, "tool": route, "result": "ok" if result.get("ok", True) else "error",
             "run_ids": ids, "runs": [
                 {"summary": run.summary(), "scenario": run.scenario.to_dict()}
                 for run_id in ids if (run := tools.store.get(run_id)) is not None
             ]}
    if not append_trace(trace_path, trace):
        answer += "\n\n판단 로그를 저장할 수 없습니다."
    return AssistantTurn(f"**{model} · `{route}`**\n\n{answer}", ids,
                         [*history[-7:], {"request": text[:2000], "run_ids": ids}])
