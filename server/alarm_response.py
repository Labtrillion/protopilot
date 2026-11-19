"""Retrieve a demo SOP and compare its responses on isolated TEP branches."""

import json
import os
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from urllib.error import URLError
from uuid import uuid4

import numpy as np
from tep_studio import TennesseeEastmanProcess

from .assistant import FALLBACK, append_trace, zoro_choices
from .catalog import CHANNELS, OPERATING_LIMITS
from .plant import COOLING_TAGS, control_action, finite_metrics
from .recording import timestamp
from .scenario import KERNEL_REVISION, STEP_HOURS, adjustment
from .sop_align import align, compact_for

SOPS = json.loads(Path(__file__).with_name("demo-sops.json").read_text(encoding="utf-8"))
HORIZON_HOURS = 3.75
CANDIDATES = {
    "hold": ("현재 생산 목표 유지", None),
    "reduce_now": ("즉시 5% 감량", 0),
    "reduce_30": ("30분 후 5% 감량", 0.5),
    "reduce_60": ("60분 후 5% 감량", 1.0),
}
COOLING_LABELS = {
    "XMEASADD(5)": "반응기 냉각수 입구 온도",
    "XMEASADD(6)": "반응기 냉각수 유량",
    "XMV(10)": "냉각수 밸브 명령",
    "XMV(10):actual": "냉각수 밸브 실제 개도",
}


def find_sop(alarm):
    """Curated demo SOP for the alarm tag (fallback when tep-sops has no aligned SOP)."""
    if alarm["kind"] == "trip":
        return next(sop for sop in SOPS if sop.get("kind") == "trip")
    return next((sop for sop in SOPS if alarm["tag"] in sop.get("tags", ())
                 and ("side" not in sop or sop["side"] == alarm.get("side"))), None)


def select_sop(checkpoint):
    """[ADDED] Align the alarm with tep-sops from the checkpoint's observed history.
    Order: confident IDV SOP -> sensor-alarm SOP for the tag/side/severity -> curated demo SOP."""
    alarm = checkpoint.alarm
    if alarm["kind"] == "trip":
        return find_sop(alarm), None
    alignment = align(alarm, list(checkpoint.history))
    sop = compact_for(alignment) or find_sop(alarm)
    alignment["selected_sop"] = sop["id"] if sop else None
    return sop, alignment


def evidence(checkpoint, sop, alignment=None):
    alarm_tag = checkpoint.alarm["tag"]
    tags = (*COOLING_TAGS, "XMV(10):actual") if sop["id"] == "TEP-COOL-01" else (
        "XMEAS(7)", "XMEAS(9)", "XMEAS(12)", "XMEAS(17)",
    )
    # [ADDED] readings for the conditions that made the aligned IDV SOP hold
    if alignment and alignment.get("idv_selected"):
        held = [c["tag"] for r in alignment["top_detail"] if r["holds"] for c in r["conditions"]]
        tags = (*held, *tags)
    entries = []
    for tag in dict.fromkeys((alarm_tag, *tags)):
        if tag not in checkpoint.observed:
            continue
        channel = CHANNELS[tag.split(":")[0]]
        entries.append({
            "tag": tag, "label": COOLING_LABELS.get(tag, channel["label"]),
            "value": checkpoint.observed[tag], "unit": channel["unit"],
        })
    return entries


def choose_response(checkpoint, sop, readings, alignment=None):
    started = perf_counter()
    choices = {key: CANDIDATES[key][0] for key in sop["candidates"]}
    state = {
        "alarm": checkpoint.alarm,
        "related_alarms": checkpoint.related_alarms,
        "readings": readings,
        "sop": {key: sop[key] for key in ("id", "revision", "title", "checks")},
        "current_production_target": checkpoint.controller.setpoints.production_rate,
    }
    if alignment and alignment.get("idv_selected"):
        # [ADDED] why this SOP was aligned: held SOP predicates with their latest values
        state["alignment"] = {
            "held_rules": alignment["ranking"][0]["held_rules"],
            "conditions": [{k: c.get(k) for k in ("tag", "operator", "latest", "fraction")}
                           for r in alignment["top_detail"] if r["holds"] for c in r["conditions"]],
            "runner_up": [r["protocol_id"] for r in alignment["ranking"][1:3]],
        }
    if sop["id"] == "TEP-COOL-01":
        state["trend"] = checkpoint.cooling_trend
    questions = {
        "diagnosis": {
            "type": "choice", "criteria": sop["diagnoses"],
            "instructions": (
                "Choose the diagnostic check supported by the alarm measurements and SOP. "
                "Use trends and valve-command vs actual-opening/flow when present. "
                "Select insufficient when the evidence does not distinguish causes."
            ),
        },
        "response": {
            "type": "choice", "criteria": choices,
            "instructions": (
                "Choose the SOP response to prioritize in a simulated comparison based on these readings. "
                "An instrument-only alarm should be verified while holding the current target. "
                "Prefer a conservative choice when evidence is inconclusive."
            ),
        },
    }
    reason = None
    try:
        selected = zoro_choices(state, questions)
        model = "Zoro-1"
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, URLError, TimeoutError) as error:
        diagnosis = "insufficient"
        if sop["id"] == "TEP-PRESSURE-01":
            diagnosis = "verify_signal" if checkpoint.alarm["source"] == "센서 시험" else "verify_process"
        selected = {"diagnosis": {"choice": diagnosis, "confidence": None},
                    "response": {"choice": "hold", "confidence": None}}
        model = FALLBACK
        reason = type(error).__name__ if os.environ.get("ZORO_BASE_URL") else "not_configured"
    diagnosis, response = selected["diagnosis"], selected["response"]
    return {
        "model": model, "fallback": model == FALLBACK, "fallback_reason": reason,
        "decision_ms": round((perf_counter() - started) * 1000, 2),
        "diagnosis": {**diagnosis, "label": sop["diagnoses"][diagnosis["choice"]]},
        "response": {**response, "label": choices[response["choice"]]},
    }


def run_branch(checkpoint, candidate):
    label, delay_hours = CANDIDATES[candidate]
    sim = TennesseeEastmanProcess()
    sim.restore(checkpoint.native)
    controller = deepcopy(checkpoint.controller)
    measurements = checkpoint.measurements.copy()
    target = controller.setpoints.production_rate
    delay_steps = None if delay_hours is None else round(delay_hours / STEP_HOURS)
    rows = [measurements]
    protected_steps = 0
    shutdown = None
    applied = False
    try:
        if delay_steps is not None:
            target = adjustment({"op": "setpoint", "id": "production_rate", "value": target * 0.95})["value"]
        for step in range(round(HORIZON_HOURS / STEP_HOURS)):
            if step == delay_steps:
                controller.setpoints = replace(controller.setpoints, production_rate=target)
                applied = True
            action, control, _ = control_action(controller, measurements, sim.time, checkpoint.manual)
            result = sim.advance(action, control_interval=STEP_HOURS, disturbances=checkpoint.idv)
            measurements = result.measurements
            rows.append(measurements)
            protected_steps += int(any(control.overrides_active.values()))
            if result.shutdown_status["terminated"]:
                shutdown = result.shutdown_status
                break
    except (RuntimeError, ValueError):
        return {"id": candidate, "label": label, "error": "이 조건에서 분기를 계산할 수 없습니다."}
    finally:
        sim.kernel.close()
    matrix = np.asarray(rows)
    violations = np.zeros(len(rows) - 1, dtype=bool)
    for tag, (low, high) in OPERATING_LIMITS.items():
        series = matrix[1:, int(tag[6:-1]) - 1]
        if low is not None:
            violations |= series < low
        if high is not None:
            violations |= series > high
    return finite_metrics({
        "id": candidate, "label": label, "shutdown": shutdown,
        "window_hours": round((len(rows) - 1) * STEP_HOURS, 6),
        "violation_minutes": float(violations.sum() * STEP_HOURS * 60),
        "protection_minutes": protected_steps * STEP_HOURS * 60,
        "peak_pressure": float(np.max(matrix[:, 6])),
        "peak_temperature": float(np.max(matrix[:, 8])),
        "temperature_sd": float(np.std(matrix[1:, 8])),
        "peak_separator_level": float(np.max(matrix[:, 11])),
        "mean_production": float(np.mean(matrix[1:, 16])),
        "production_target": float(target), "action_applied": applied,
    })


def analyze_alarm(checkpoint, trace_path):
    alarm = checkpoint.alarm
    sop, alignment = select_sop(checkpoint)  # [CHANGED] was find_sop(alarm): tag-only lookup
    readings = evidence(checkpoint, sop, alignment) if sop else []
    digest = sha256(checkpoint.native.model_bytes).hexdigest()[:12]
    sop_summary = {key: sop[key] for key in ("id", "revision", "title", "checks")} if sop else None
    source_url = sop.get("source_url") if sop else None
    if isinstance(source_url, str) and source_url.startswith("https://doi.org/") and sop.get("source_title"):
        sop_summary.update(source_title=sop["source_title"], source_url=source_url)
    result = {
        "decision_id": uuid4().hex, "alarm": alarm, "related_alarms": checkpoint.related_alarms,
        "sop": sop_summary,
        "readings": readings, "decision": None, "branches": [], "alignment": alignment,
        "start": {"checkpoint": digest, "run_id": checkpoint.run_id, "step": checkpoint.step,
                  "time_hours": round(checkpoint.native.time, 8), "horizon_hours": HORIZON_HOURS,
                  "production_target": checkpoint.controller.setpoints.production_rate},
    }
    if sop and sop.get("candidates"):
        result["decision"] = choose_response(checkpoint, sop, readings, alignment)
        result["branches"] = [run_branch(checkpoint, key) for key in sop["candidates"]]
    trace = {
        **result, "time_utc": timestamp(), "flow": "live_alarm_response", "kernel_revision": KERNEL_REVISION,
        "simulation_context": {
            "mode": "mode1", "seed": checkpoint.seed, "step_hours": STEP_HOURS,
            "setpoints": asdict(checkpoint.controller.setpoints),
            "controller": {key: getattr(checkpoint.controller, f"enable_{key}")
                           for key in ("composition", "overrides", "pct_g_feedback")},
            "manual_overrides": {f"XMV({i + 1})": value for i, value in checkpoint.manual.items()},
            "alarm_limits": checkpoint.limits, "cooling_trend": checkpoint.cooling_trend,
            "evaluation_faults": [f"IDV({i + 1})" for i in np.flatnonzero(checkpoint.idv)],
        },
    }
    result["logged"] = append_trace(trace_path, trace)
    return result
