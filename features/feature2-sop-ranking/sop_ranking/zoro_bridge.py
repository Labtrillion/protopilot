"""Run the Zoro-1 decision agent (sop_ranking) on branches of a live alarm checkpoint.

The agent chooses which registered case to simulate next; this module executes each request on a copy of
the alarm checkpoint (model, random state, controller memory, manual moves, active disturbances) and
returns the trajectory. Active disturbances stay inside the simulator and are never passed to the agent.
"""

import math
import os
from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256

import numpy as np
from tep_studio import TennesseeEastmanProcess

from .agent import ZoroAgent
from .cases import ActiveSetpoint
from .inputs import Alarm, EventInput, Observation
from .protopilot_cases import HORIZON_S, build_alarm_catalog, build_benchmark, build_policy, sop_rag
from .registry import ActionKind
from .sim_tools import BranchOutcome, SimulationError, SimulationToolkit
from .tools import ToolExecutor
from .decision import FakeZoroClient
from zoro.client import HttpZoroClient, ZoroHttpConfig

from server.branch_metrics import branch_summary, step_cost
from server.sop_lookup import find_sop
from server.alarm_rules import AlarmMonitor, describe
from server.assistant import append_trace
from server.catalog import CHANNELS, SETPOINTS
from server.plant import control_action
from server.recording import timestamp
from server.scenario import KERNEL_REVISION, STEP_HOURS, adjustment

# 100 control steps = 0.05 h = 3 min, the TEP Studio export interval used by the benchmark.
SAMPLE_EVERY = 100
# Channels an operator console can read. Disturbance and internal diagnostic groups stay out of the agent.
ONLINE_GROUPS = {"measurements", "additional_measurements", "manipulated_variables"}


def snapshot_id(run_id, alarm_id):
    return f"{run_id}:{alarm_id}"


def online(tag):
    channel = CHANNELS.get(tag)
    return channel is not None and channel["group"] in ONLINE_GROUPS and channel.get("available_online") is not False


def measurement_tags(measurements):
    return {f"XMEAS({index + 1})": float(value) for index, value in enumerate(measurements)}


class CheckpointRunner:
    """BranchRunner: one isolated branch per request from the stored alarm checkpoint."""

    def __init__(self, checkpoints):
        self.checkpoints = checkpoints

    def run(self, snapshot, case, horizon_s):
        """Branch from the alarm checkpoint and meter it the way feat/setpoint-decision does: TEP internal operating
        cost per control step (`step_cost`), produced volume and production loss (`branch_summary`), and alarm
        minutes from an AlarmMonitor copied at the alarm (true-value channel)."""
        checkpoint = self.checkpoints.get(snapshot)
        if checkpoint is None:
            raise SimulationError(f"알람 체크포인트 {snapshot} 없음", recoverable=False)
        schedule = {}
        for change in case.setpoint_changes:
            try:
                value = adjustment({"op": "setpoint", "id": change.setpoint_id, "value": change.target_value})["value"]
            except ValueError as error:
                raise SimulationError(f"{change.setpoint_id}: {error}", recoverable=False) from error
            schedule.setdefault(round(change.apply_at_s / 3600 / STEP_HOURS), {})[change.setpoint_id] = value
        sim = TennesseeEastmanProcess()
        sim.restore(checkpoint.native)
        controller = deepcopy(checkpoint.controller)
        reference = float(controller.setpoints.production_rate)  # same loss reference for every branch
        measurements = checkpoint.measurements.copy()
        monitor = checkpoint.monitor.copy() if checkpoint.monitor is not None else AlarmMonitor(checkpoint.limits)
        offsets = checkpoint.offsets or {}
        times, samples, applied, shutdown = [0.0], [measurement_tags(measurements)], [], None
        rows, protected_steps, cost = [measurements], 0, 0.0
        try:
            steps = round(horizon_s / 3600 / STEP_HOURS)
            for step in range(steps):
                if step in schedule:
                    controller.setpoints = replace(controller.setpoints, **schedule[step])
                    applied.append({"time_s": step * STEP_HOURS * 3600, **schedule[step]})
                action, control, _ = control_action(controller, measurements, sim.time, checkpoint.manual)
                result = sim.advance(action, control_interval=STEP_HOURS, disturbances=checkpoint.idv)
                measurements = result.measurements
                rows.append(measurements)
                protected_steps += int(any(control.overrides_active.values()))
                cost += step_cost(result)
                monitor.update(sim.time, measurements, offsets, controller.setpoints, STEP_HOURS)
                terminated = result.shutdown_status["terminated"]
                if (step + 1) % SAMPLE_EVERY == 0 or terminated or step + 1 == steps:
                    times.append((step + 1) * STEP_HOURS * 3600)
                    samples.append(measurement_tags(measurements))
                if terminated:
                    shutdown = result.shutdown_status
                    break
        except (RuntimeError, ValueError) as error:
            raise SimulationError(f"이 조건에서 분기를 계산할 수 없습니다: {error}") from error
        finally:
            sim.kernel.close()
        summary = branch_summary(np.asarray(rows), protected_steps, shutdown, cost=cost, reference=reference,
                                 horizon_hours=horizon_s / 3600, alarm=monitor.summary())
        return BranchOutcome(
            times_s=times, samples=samples, tripped=shutdown is not None,
            trip_message=shutdown.get("message") if shutdown else None, applied_changes=applied,
            simulator_version=f"tep-studio@{KERNEL_REVISION}",
            # Same id for every branch of this checkpoint; it does not reveal which disturbances are active.
            disturbance_profile_id="checkpoint:" + sha256(checkpoint.native.model_bytes).hexdigest()[:12],
            seed=int(checkpoint.seed),
            operating_cost_usd=summary["operating_cost"], produced_m3=summary["produced_m3"],
            production_loss_m3=summary["production_loss_m3"], alarm_minutes=summary["alarm_minutes"],
            observed_alarm_minutes=summary["observed_alarm_minutes"],
            alarm_minutes_by_rule=summary["alarm_minutes_by_rule"],
        )


class PlantObserver:
    """LiveObserver: current console readings, only while the alarm's run is still the live run."""

    def __init__(self, plant, lock, run_id):
        self.plant, self.lock, self.run_id = plant, lock, run_id

    def observe(self, sensor_ids):
        with self.lock:
            if self.plant.recording.id != self.run_id:
                return [], None
            latest = self.plant.latest
        time_s = latest["time_hours"] * 3600
        values = latest["values"]
        observations = [
            Observation(sensor_id=tag, value=values[tag], unit=CHANNELS[tag]["unit"], measured_at_s=time_s, quality="good")
            for tag in sensor_ids if online(tag) and values.get(tag) is not None and math.isfinite(values[tag])
        ]
        return observations, time_s


# AlarmMonitor condition type -> EventInput alarm condition (threshold rules are split by side).
MONITOR_CONDITIONS = {"rate": "rate", "deviation": "deviation"}


def monitor_event_alarms(monitor):
    """Active alarms on the monitor's observed channel (live limits + enabled alarm-rules.json rules): the same
    alarm engine feat/setpoint-decision gives Zoro-1. Whether an alarm came from a sensor test is not said."""
    alarms = []
    for rule_id, hit in monitor.active["observed"].items():
        rule = hit["rule"]
        tag = rule.get("tag")
        if tag not in CHANNELS:
            continue
        kind = rule["condition"]["type"]
        if kind == "threshold":
            condition = "high" if "above" in hit.get("problem", "") else "low"
        else:
            condition = MONITOR_CONDITIONS.get(kind, "other")
        info = describe(rule, hit, monitor.time_hours)
        limit = hit.get("limit")
        alarms.append(Alarm(
            alarm_id=rule_id, sensor_id=tag, condition=condition, raised_at_s=hit["since_hours"] * 3600, active=True,
            message=f"{info.get('label') or tag}: {info.get('problem')}",
            threshold=float(limit) if isinstance(limit, (int, float)) and condition in ("high", "low") else None,
            unit=CHANNELS[tag]["unit"],
        ))
    return alarms


def console_event_alarms(checkpoint):
    """Fallback for checkpoints without a monitor: the console alarm list at the checkpoint."""
    alarms = {}
    for alarm in (*checkpoint.related_alarms, checkpoint.alarm):
        if alarm.get("kind") != "warning" or alarm.get("side") not in ("high", "low"):
            continue
        alarms[str(alarm["id"])] = Alarm(
            alarm_id=str(alarm["id"]), sensor_id=alarm["tag"], condition=alarm["side"],
            raised_at_s=alarm["time_hours"] * 3600, active=True, message=alarm["label"],
            threshold=alarm["limit"], unit=CHANNELS[alarm["tag"]]["unit"],
        )
    return list(alarms.values())


def event_from_checkpoint(run_id, alarm_id, checkpoint):
    time_s = checkpoint.native.time * 3600
    alarms = (monitor_event_alarms(checkpoint.monitor) if checkpoint.monitor is not None
              else console_event_alarms(checkpoint))
    observations = [
        Observation(sensor_id=tag, value=float(value), unit=CHANNELS[tag]["unit"], measured_at_s=time_s, quality="good")
        for tag, value in checkpoint.observed.items()
        if online(tag) and value is not None and math.isfinite(value)
    ]
    setpoints = asdict(checkpoint.controller.setpoints)
    return EventInput(
        event_id=f"{run_id}-A{alarm_id}", state_version=0, sim_time_s=time_s,
        snapshot_id=snapshot_id(run_id, alarm_id), alarms=alarms, observations=observations,
        active_setpoints=[ActiveSetpoint(setpoint_id=key, value=float(value), unit=SETPOINTS[key][1], since_s=None)
                          for key, value in setpoints.items() if key in SETPOINTS],
    )


def agent_client(catalog):
    if os.environ.get("ZORO_BASE_URL"):
        return HttpZoroClient(ZoroHttpConfig.from_env())
    # Without a Zoro-1 server: a fixed order that tests every SOP case, then recommends by the benchmark rule.
    sims = [a.action_id for a in catalog.actions if a.kind == ActionKind.SIMULATION]
    return FakeZoroClient(["SIM_HOLD", *sims, *(f"RECOMMEND:{a}" for a in sims), "REQUEST_HUMAN_REVIEW"])


def run_zoro_agent(plant, lock, run_id, alarm_id, trace_path, client=None, horizon_s=HORIZON_S):
    with lock:
        checkpoint = plant.alarm_checkpoints.get(alarm_id) if run_id == plant.recording.id else None
    if checkpoint is None:
        raise LookupError("알람 발생 시점의 상태를 찾을 수 없습니다.")
    sop = find_sop(checkpoint.alarm)
    if not sop or not sop.get("candidates"):
        raise ValueError("이 알람에는 비교할 SOP 후보가 없습니다.")
    event = event_from_checkpoint(run_id, alarm_id, checkpoint)
    alarm_tags = [checkpoint.alarm["tag"]]
    setpoints = {key: float(value) for key, value in asdict(checkpoint.controller.setpoints).items()}
    ranges = {key: (unit, low, high) for key, (_, unit, _, low, high) in SETPOINTS.items()}
    units = {tag: channel["unit"] for tag, channel in CHANNELS.items()}
    limits = {tag: tuple(bounds) for tag, bounds in checkpoint.limits.items()}
    catalog = build_alarm_catalog(sop, setpoints, ranges, alarm_tags, horizon_s)
    policy = build_policy(catalog, alarm_tags, build_benchmark(setpoints, limits, units, horizon_s))
    toolkit = SimulationToolkit(CheckpointRunner({event.snapshot_id: checkpoint}), alarm_tags=alarm_tags,
                                alarm_limits=limits, units=units, observer=PlantObserver(plant, lock, run_id))
    client = client or agent_client(catalog)
    report = ZoroAgent(catalog, policy, client, ToolExecutor(toolkit.tools(), virtual=False)).run(event, sop_rag(sop, catalog))
    result = report.model_dump(mode="json")
    append_trace(trace_path, {
        "time_utc": timestamp(), "flow": "zoro_agent", "kernel_revision": KERNEL_REVISION,
        "run_id": run_id, "alarm_id": alarm_id, "sop": {"id": sop["id"], "revision": sop.get("revision")},
        "model_client": client.kind, "final": result["final"], "decisions": result["decisions"],
        "tool_requests": result["tool_requests"],
    })
    return result
