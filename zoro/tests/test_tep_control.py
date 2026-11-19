"""tep-rust tepsim-control 의 단위 테스트를 그대로 옮긴 것 + 전파 계산 검사."""
from __future__ import annotations

import math

import pytest

from zoro_agent.tep_control import (
    COMPOSITION, FAST, OVERRIDE_HIGH, OVERRIDE_LOW, OVERRIDE_RESET_SETPOINT, OVERRIDE_RESET_VALVE, PRESET, QUALITY,
    Loop, Output, Override, Period, Scheme, Tuning, cascade_response, chain_for_measurement, f32, override_step,
    preset, top_level_setpoints)

DT = 1.0 / 3600.0


def proportional():
    return Tuning(1, 2, Output("valve", 1), 1.0, None, 5811.0, Period.FAST)


def integral(period=Period.FAST):
    return Tuning(10, 21, Output("valve", 10), -0.156 * 10.0, 1452.0 / 3600.0, 150.0, period)


# lib.rs mod tests -------------------------------------------------------------
def test_proportional_loop_stops_moving_once_error_settles():
    s = Loop()
    first = s.increment(proportional(), 4000.0, 3664.0, DT)
    assert first != 0 and s.increment(proportional(), 4000.0, 3664.0, DT) == 0.0


def test_pi_loop_keeps_moving_on_settled_error():
    s = Loop()
    s.increment(integral(), 100.0, 94.6, DT)
    second = s.increment(integral(), 100.0, 94.6, DT)
    assert second != 0 and second == s.increment(integral(), 100.0, 94.6, DT)


def test_period_scales_integral_action():
    a, b = Loop(), Loop()
    a.increment(integral(), 100.0, 94.6, DT)
    b.increment(integral(Period.COMPOSITION), 100.0, 94.6, DT)
    ratio = b.increment(integral(Period.COMPOSITION), 100.0, 94.6, DT) / a.increment(integral(), 100.0, 94.6, DT)
    assert abs(ratio - 120.0) < 1e-9


def test_span_normalises_error_and_contrl22_uses_raw_error():
    narrow = Tuning(1, 2, Output("valve", 1), 1.0, None, 1.017, Period.FAST)
    wide = Tuning(1, 2, Output("valve", 1), 1.0, None, 8354.0, Period.FAST)
    assert abs(Loop().increment(narrow, 1.0, 0.0, DT)) > abs(Loop().increment(wide, 1.0, 0.0, DT)) * 1000
    s = Loop()
    s.increment(preset(22).tuning, 2633.7, 2600.0, DT)
    assert s.previous_error == 2633.7 - 2600.0


# lib.rs mod override_tests ----------------------------------------------------
def test_override_latches_holds_releases():
    st, act = override_step(Override.RELEASED, 3000.0)
    assert (st, act) == (Override.OPEN, ("hold", 100.0))
    st, act = override_step(st, 2800.0)
    assert (st, act) == (Override.OPEN, ("hold", 100.0))
    st, act = override_step(st, 2600.0)
    assert (st, act) == (Override.RELEASED, ("release", OVERRIDE_RESET_VALVE, OVERRIDE_RESET_SETPOINT))
    assert override_step(st, 2600.0) == (Override.RELEASED, ("run",))
    st, act = override_step(Override.RELEASED, 2000.0)
    assert (st, act) == (Override.SHUT, ("hold", 0.0))
    assert override_step(Override.SHUT, 2500.0) == (Override.SHUT, ("hold", 0.0))
    assert override_step(Override.SHUT, 2700.0)[0] == Override.RELEASED
    for p in (2301.0, 2500.0, 2633.7, 2800.0, 2949.0):
        assert override_step(Override.RELEASED, p) == (Override.RELEASED, ("run",))
    assert override_step(Override.SHUT, 3000.0)[0] == Override.OPEN  # 높음 검사가 먼저
    assert override_step(Override.RELEASED, OVERRIDE_HIGH)[1] == ("hold", 100.0)
    assert override_step(Override.RELEASED, OVERRIDE_LOW)[1] == ("hold", 0.0)
    assert OVERRIDE_RELEASE_IS_SINGLE()


def OVERRIDE_RELEASE_IS_SINGLE():
    from zoro_agent.tep_control import OVERRIDE_RELEASE
    return OVERRIDE_RELEASE != 2633.7 and OVERRIDE_HIGH == 2950.0


# lib.rs mod scheme_tests ------------------------------------------------------
def test_fast_loops_first_fire_on_step_three():
    s, meas, valves = Scheme(), [1.0] * 41, [50.0] * 12
    for step in (1, 2):
        before = list(valves)
        s.step(step, meas, valves, DT)
        assert valves == before
    s.step(3, meas, valves, DT)
    assert valves != [50.0] * 12


def test_clamp_leaves_agitator_alone():
    v = [-5.0] * 12
    Scheme.clamp(v)
    assert v[:11] == [0.0] * 11 and v[11] == -5.0
    v = [150.0] * 12
    Scheme.clamp(v)
    assert v[:11] == [100.0] * 11 and v[11] == 150.0
    v = [-0.0] * 12
    Scheme.clamp(v)
    assert math.copysign(1, v[0]) == 1 and math.copysign(1, v[11]) == -1


def test_schedule_order_and_presets():
    assert FAST.index(4) < FAST.index(17) and FAST.index(9) < FAST.index(16) and FAST.index(10) < FAST.index(18)
    assert COMPOSITION == [13, 14, 15, 19] and QUALITY == [20] and 22 not in FAST + COMPOSITION + QUALITY
    assert len(PRESET) == 20
    assert preset(19).tuning.gain == f32(f32(f32(-83.2) / f32(5.0)) / f32(3.0))
    assert preset(19).setpoint == f32(13.823) and preset(19).tuning.output.index == 6


# 구조 / 전파 ------------------------------------------------------------------
def test_structure():
    assert top_level_setpoints() == [5, 7, 8, 11, 13, 14, 15, 17, 18, 19, 20]
    assert [p.tuning.number for p in chain_for_measurement(30)] == [19, 6]
    assert [p.tuning.number for p in chain_for_measurement(38)] == [20, 16, 9]
    assert chain_for_measurement(7) == []  # 반응기 압력을 읽는 루프 없음
    assert chain_for_measurement(13) == []  # CONTRL22 는 실행되지 않고 오버라이드만 읽음


def test_cascade_response_matches_scheme_execution():
    """전파 계산 = 실제 Scheme 을 한 번 돌린 결과 (측정값 고정)."""
    for delta in (-0.5, -1.0, 0.75):
        s = Scheme()
        meas = [0.0] * 41
        meas[29] = s.setpoint(19)          # XMEAS(30) 가 목표에 있음 → 오차 0
        meas[9] = s.setpoint(6)            # XMEAS(10) = 퍼지 유량 목표
        meas[12] = 2700.0                  # 분리기 압력: 오버라이드 비작동
        valves = [40.0] * 12
        s.step(360, meas, valves, DT)      # 이력 정착(오차 0)
        s.step(360, meas, valves, DT)
        sp6, v6 = s.setpoint(6), valves[5]
        s.set_setpoint(19, s.setpoint(19) + delta)
        s.step(720, meas, valves, DT)      # FAST(CONTRL6) 먼저, 그다음 COMPOSITION(CONTRL19)
        d_sp6 = s.setpoint(6) - sp6
        s.step(723, meas, valves, DT)      # 다음 FAST 에서 CONTRL6 이 새 목표에 반응
        d_v6 = valves[5] - v6
        k = cascade_response(19, delta)
        integ = k[0].integral_per_run
        assert d_sp6 == pytest.approx(k[0].delta + integ, rel=1e-9)
        assert d_v6 == pytest.approx(1.22 * (d_sp6 * 100 / 1.0), rel=1e-6)
        assert k[1].delta == pytest.approx(f32(1.22) * k[0].delta * 100, rel=1e-9)
