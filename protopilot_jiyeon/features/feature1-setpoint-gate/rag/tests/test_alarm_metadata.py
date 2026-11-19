import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from zoro_rag.alarm import AlarmContextBuilder, AlarmNormalizer
from zoro_rag.schemas import AlarmEvent, AlarmResult, AlarmSignal

ROOT = Path(__file__).parents[1]


def context_for(data):
    event = AlarmEvent.model_validate(data)
    normalizer = AlarmNormalizer(ROOT / "zoro_rag_knowledge")
    return AlarmContextBuilder().build(event, normalizer.normalize(event))


def test_demo_metadata_round_trips_through_alarm_result():
    raw = json.loads((ROOT / "examples/idv8/alarm_event.json").read_text(encoding="utf-8"))
    context = context_for(raw)
    output = AlarmResult(event_id=raw["event_id"], status="insufficient_evidence", alarm_context=context)
    restored = AlarmResult.model_validate_json(output.model_dump_json())
    actual = restored.alarm_context
    assert actual.snapshot_id == raw["snapshot_id"]
    assert actual.active_objective.objective_id == raw["active_objective"]["objective_id"]
    # 주입 외란(정답)은 결과에 남기지 않는다. 나머지 시뮬레이터 정보는 보존
    assert actual.simulation_context == {k: v for k, v in raw["simulation_context"].items() if "disturbance" not in k}
    assert {"disturbance_id", "disturbance_basis", "recent_changes[]"} <= set(actual.withheld_ground_truth_fields)
    assert actual.recent_changes == [] and actual.known_disturbances == []
    for tag, measurement in actual.measurements.items():
        assert measurement.quality == "good" and measurement.unit and measurement.measured_at
        assert measurement.value == actual.observed_variables[tag]
    alarm = actual.active_alarms[0]
    assert alarm.sample_id == actual.measurements["XMEAS_23"].sample_id
    assert alarm.activated_at != alarm.measured_at
    assert alarm.deadband_clear_value == 34.5
    assert not actual.missing_information


def test_legacy_numbers_and_measurement_timestamp_still_work():
    context = context_for({
        "event_id": "OLD", "timestamp": "2026-11-19T10:00:00Z",
        "alarms": [{"alarm_id": "P_HIGH", "tag": "XMEAS(7)", "current_value": 2810,
                    "unit": "kPa(g)", "timestamp": "2026-11-19T09:00:00Z"}],
        "process_state": {"XMEAS(7)": 2790},
    })
    alarm = context.active_alarms[0]
    assert alarm.stale and alarm.measured_at == alarm.timestamp
    assert alarm.activated_at is None and alarm.sample_id is None
    assert context.observed_variables["XMEAS_7"] == 2790
    assert context.measurements["XMEAS_7"].quality == "unknown"
    assert context.measurements["XMEAS_7"].unit is None
    assert any("Conflicting" in message for message in context.missing_information)


def test_bad_stale_and_future_measurements_are_preserved_and_flagged():
    context = context_for({
        "event_id": "QUALITY", "timestamp": "2026-11-19T10:00:00Z",
        "alarms": [{"alarm_id": "P_HIGH", "tag": "XMEAS(7)"}],
        "process_state": {
            "XMEAS(7)": {"value": 2810, "unit": "kPa(g)", "quality": "BAD", "measured_at": "2026-11-19T09:00:00Z"},
            "XMEAS(9)": {"value": 121, "unit": "degC", "quality": "uncertain", "measured_at": "2026-11-19T10:01:00Z"},
        },
    })
    assert context.observed_variables["XMEAS_7"] == 2810
    assert context.measurements["XMEAS_7"].stale
    assert context.measurements["XMEAS_9"].stale is None
    messages = " ".join(context.missing_information)
    assert "bad" in messages and "uncertain" in messages and "stale" in messages and "later than" in messages


def test_units_and_samples_are_not_silently_merged():
    context = context_for({
        "event_id": "CONFLICT", "timestamp": "2026-11-19T10:00:00Z",
        "alarms": [{"alarm_id": "P_HIGH", "tag": "XMEAS(7)", "current_value": 2810,
                    "unit": "kPa(g)", "sample_id": "sample-A"}],
        "process_state": {"XMEAS(7)": {"value": 2810000, "unit": "Pa(g)", "sample_id": "sample-B"}},
    })
    assert context.observed_variables["XMEAS_7"] == 2810000
    assert context.active_alarms[0].current_value == 2810
    messages = " ".join(context.missing_information)
    assert "Unit mismatch" in messages and "Different analysis sample IDs" in messages
    assert "Conflicting alarm and process_state" not in messages


def test_alias_tags_do_not_overwrite_each_other():
    with pytest.raises(ValueError, match="Duplicate process_state"):
        context_for({"event_id": "DUP", "alarms": [{"alarm_id": "P_HIGH"}],
                     "process_state": {"XMEAS(7)": 2800, "XMEAS_7": 2810}})


@pytest.mark.parametrize("direction,clear", [("high", 2810), ("low", 2790)])
def test_invalid_deadband_direction_is_rejected(direction, clear):
    with pytest.raises(ValidationError, match="deadband_clear_value"):
        AlarmSignal(alarm_id="P", direction=direction, threshold=2800, deadband_clear_value=clear)


def test_activation_time_is_not_used_as_measurement_time():
    context = context_for({"event_id": "ACT", "timestamp": "2026-11-19T10:00:00Z",
                           "alarms": [{"alarm_id": "P", "current_value": 2810,
                                       "activated_at": "2026-11-19T09:00:00Z"}]})
    assert context.active_alarms[0].measured_at is None
    assert context.active_alarms[0].stale is None


def test_conflicting_measurement_times_are_rejected():
    with pytest.raises(ValidationError, match="must agree"):
        AlarmSignal(alarm_id="P", timestamp="2026-11-19T09:00:00Z", measured_at="2026-11-19T10:00:00Z")


def test_nested_snapshot_is_supported_without_silent_conflicts():
    event = AlarmEvent(event_id="SNAP", alarms=[{"alarm_id": "P"}], simulation_context={"snapshot_id": "snapshot-1"})
    assert event.snapshot_id == "snapshot-1"
    with pytest.raises(ValidationError, match="conflicts"):
        AlarmEvent(event_id="SNAP", alarms=[{"alarm_id": "P"}], snapshot_id="snapshot-2", simulation_context={"snapshot_id": "snapshot-1"})


def test_null_process_measurement_is_not_treated_as_available():
    context = context_for({"event_id": "NULL", "alarms": [{"alarm_id": "P", "tag": "XMEAS(7)"}],
                           "process_state": {"XMEAS(7)": {"value": None}}})
    assert any("No current measurement" in message for message in context.missing_information)
