"""Portable live scenarios. Commands are scheduled on the simulator's control steps."""

import math

from .catalog import CHANNELS, CONTROL_FLAGS, METADATA, SETPOINTS

LIVE_FORMAT = "protopilot-live-1"
STEP_HOURS = 0.0005
DEFAULT_SEED = 1431655765
SPEEDS = {15: 5, 60: 20, 240: 80, 960: 320}
DEFAULT_FLAGS = {"composition": True, "overrides": True, "pct_g_feedback": False}
DISTURBANCE_IDS = {entry["id"] for entry in METADATA["disturbances"]}
ADJUSTMENTS = {"mv", "setpoint", "disturbance", "offset", "limit", "pressure_test", "controller", "speed"}


def numeric(value, lower=-1e9, upper=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("유한한 숫자를 입력하세요.")
    if not lower <= value <= upper:
        raise ValueError(f"{lower:g} ~ {upper:g} 범위 값을 입력하세요.")
    return float(value)


def adjustment(body):
    op, tag, value = body.get("op"), body.get("id"), body.get("value")
    if not isinstance(op, str) or (tag is not None and not isinstance(tag, str)):
        raise ValueError("조작 유형 또는 대상 태그를 확인하세요.")
    channel = CHANNELS.get(tag, {})
    group = channel.get("group")
    if op == "mv" and group == "manipulated_variables":
        value = None if value is None else numeric(value, 0, 100)
    elif op == "setpoint" and tag in SETPOINTS:
        value = None if value is None else numeric(value, *SETPOINTS[tag][-2:])
    elif op == "disturbance" and tag in DISTURBANCE_IDS:
        value = numeric(value, 0, 1)
        if value not in (0, 1):
            raise ValueError("외란 상태는 0 또는 1이어야 합니다.")
        value = int(value)
    elif op == "offset" and group in {"measurements", "additional_measurements"}:
        value = 0 if value is None else numeric(value)
    elif op == "limit" and channel:
        low = None if body.get("low") is None else numeric(body["low"])
        value = None if value is None else numeric(value)
        if low is not None and value is not None and low >= value:
            raise ValueError("하한은 상한보다 작아야 합니다.")
        return {"op": op, "id": tag, "low": low, "value": value}
    elif op == "pressure_test":
        return {"op": op}
    elif op == "controller" and tag in CONTROL_FLAGS and isinstance(value, bool):
        pass
    elif op == "speed" and numeric(value) in SPEEDS:
        return {"op": op, "value": int(value)}
    else:
        raise ValueError("대상 태그 또는 설정값을 확인하세요.")
    return {"op": op, "id": tag, "value": value}


def live_scenario(document):
    if not isinstance(document, dict) or document.get("format") != LIVE_FORMAT:
        raise ValueError("ProtoPilot 라이브 시나리오 JSON을 선택하세요.")
    if document.get("step_hours") != STEP_HOURS:
        raise ValueError("이 시나리오의 제어 주기는 지원하지 않습니다.")
    through = document.get("through_step")
    if type(through) is not int or not 0 <= through <= 2**53 - 1:
        raise ValueError("시나리오 종료 스텝을 확인하세요.")
    seed = numeric(document.get("seed"), 1, 2**53 - 1)
    speed = adjustment({"op": "speed", "value": document.get("initial_speed")})["value"]
    flags = document.get("controller")
    if not isinstance(flags, dict) or flags.keys() != CONTROL_FLAGS.keys() or any(type(v) is not bool for v in flags.values()):
        raise ValueError("시나리오의 제어 옵션을 확인하세요.")
    changes = document.get("timeline")
    if not isinstance(changes, list):
        raise ValueError("시나리오 조작 이력이 필요합니다.")
    timeline = []
    previous = 0
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("시나리오 조작 형식을 확인하세요.")
        step = change.get("step")
        if type(step) is not int or not previous <= step <= through:
            raise ValueError("시나리오 조작 스텝은 시간순이어야 합니다.")
        timeline.append({"step": step, **adjustment(change)})
        previous = step
    return {"format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": seed,
            "initial_speed": speed, "controller": dict(flags), "through_step": through, "timeline": timeline}
