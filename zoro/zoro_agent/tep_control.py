"""TEP Studio(Braatz 폐루프) 제어 로직의 파이썬 이식 + 설정값 변경의 하위 루프 전파 계산.

원본: jkitchin/tep-rust @ 0735ad3b5bb7fdd367a8a31da1db8a209dac2a15
      crates/tepsim-control/src/lib.rs (temain_mod.f 의 이식)
      https://github.com/jkitchin/tep-rust/blob/0735ad3b5bb7fdd367a8a31da1db8a209dac2a15/crates/tepsim-control/src/lib.rs

이식 범위: PRESET 20개 루프, 속도형 P/PI 증분, CONTRL6 퍼지 압력 오버라이드, 실행 순서(FAST/COMPOSITION/
QUALITY), CONSHAND 클램프. 공정 모델(teprob)은 이식하지 않았다. 따라서 `cascade_response` 는
"측정값이 아직 변하지 않은 상태에서 제어기가 즉시 내는 증분"만 계산한다. 공정 응답은 시뮬레이터로 확인한다.

이 배포 WASM 과 위 소스 스냅샷이 같은 빌드인지는 확인할 수 없다(STUDIO.md 와 같은 한계).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

SOURCE = ("https://github.com/jkitchin/tep-rust/blob/0735ad3b5bb7fdd367a8a31da1db8a209dac2a15/"
          "crates/tepsim-control/src/lib.rs")


def f32(x: float) -> float:
    """f32 로 반올림한 값을 f64 로 (Rust `single`)."""
    return struct.unpack("f", struct.pack("f", x))[0]


def _q(*ops) -> float:
    """f32 리터럴의 연산을 왼쪽부터 f32 로 계산. 예: _q(-83.2, '/', 5.0, '/', 3.0)."""
    v = f32(ops[0])
    for op, b in zip(ops[1::2], ops[2::2]):
        b = f32(b)
        v = f32(v * b if op == "*" else v / b)
    return v


class Period(Enum):
    FAST = 3
    COMPOSITION = 360
    QUALITY = 900

    @property
    def seconds(self) -> float:
        return float(self.value)


@dataclass(frozen=True)
class Output:
    kind: Literal["valve", "setpoint"]
    index: int  # XMV(n) 또는 SETPT(n), 1-based
    span: float | None = None  # setpoint 출력일 때 내부 루프의 span


@dataclass(frozen=True)
class Tuning:
    number: int
    measurement: int  # XMEAS(n)
    output: Output
    gain: float
    reset_h: float | None  # None = P 전용
    span: float | None  # None = 정규화 없음 (CONTRL22)
    period: Period


@dataclass(frozen=True)
class Preset:
    tuning: Tuning
    setpoint_index: int
    setpoint: float


def _p(n, meas, out, gain, reset, span, period, sp_index, sp):
    return Preset(Tuning(n, meas, out, gain, reset, span, period), sp_index, sp)


V = lambda n: Output("valve", n)  # noqa: E731
S = lambda n, span: Output("setpoint", n, f32(span))  # noqa: E731
F, C, Q = Period.FAST, Period.COMPOSITION, Period.QUALITY

# lib.rs:209-517 (temain_mod.f:246-317)
PRESET: list[Preset] = [
    _p(1, 2, V(1), f32(1.0), None, f32(5811.0), F, 1, f32(3664.0)),
    _p(2, 3, V(2), f32(1.0), None, f32(8354.0), F, 2, f32(4509.3)),
    _p(3, 1, V(3), f32(1.0), None, f32(1.017), F, 3, f32(0.25052)),
    _p(4, 4, V(4), f32(1.0), None, f32(15.25), F, 4, f32(9.3477)),
    _p(5, 5, V(5), f32(-0.083), _q(1.0, "/", 3600.0), f32(53.0), F, 5, f32(26.902)),
    _p(6, 10, V(6), f32(1.22), None, f32(1.0), F, 6, f32(0.33712)),
    _p(7, 12, V(7), f32(-2.06), None, f32(70.0), F, 7, f32(50.0)),
    _p(8, 15, V(8), f32(-1.62), None, f32(70.0), F, 8, f32(50.0)),
    _p(9, 19, V(9), f32(0.41), None, f32(460.0), F, 9, f32(230.31)),
    _p(10, 21, V(10), _q(-0.156, "*", 10.0), _q(1452.0, "/", 3600.0), f32(150.0), F, 10, f32(94.599)),
    _p(11, 17, V(11), f32(1.09), _q(2600.0, "/", 3600.0), f32(46.0), F, 11, f32(22.949)),
    _p(13, 23, S(3, 1.017), f32(18.0), _q(3168.0, "/", 3600.0), f32(100.0), C, 13, f32(32.188)),
    _p(14, 26, S(1, 5811.0), f32(8.3), _q(3168.0, "/", 3600.0), f32(100.0), C, 14, f32(6.8820)),
    _p(15, 27, S(2, 8354.0), f32(2.37), _q(5069.0, "/", 3600.0), f32(100.0), C, 15, f32(18.776)),
    _p(16, 18, S(9, 460.0), _q(1.69, "/", 10.0), _q(236.0, "/", 3600.0), f32(130.0), F, 16, f32(65.731)),
    _p(17, 8, S(4, 15.25), _q(11.1, "/", 10.0), _q(3168.0, "/", 3600.0), f32(50.0), F, 17, f32(75.000)),
    _p(18, 9, S(10, 150.0), _q(2.83, "*", 10.0), _q(982.0, "/", 3600.0), f32(150.0), F, 18, f32(120.40)),
    _p(19, 30, S(6, 1.0), _q(-83.2, "/", 5.0, "/", 3.0), _q(6336.0, "/", 3600.0), f32(26.0), C, 19, f32(13.823)),
    _p(20, 38, S(16, 130.0), _q(-16.3, "/", 5.0), _q(12408.0, "/", 3600.0), f32(1.6), Q, 20, f32(0.83570)),
    _p(22, 13, V(6), _q(-1.0, "*", 5.0), _q(1000.0, "/", 3600.0), None, F, 12, f32(2633.7)),
]

# lib.rs:943-945. CONTRL22 는 정의만 있고 실행되지 않는다(delta D-008).
FAST = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18]
COMPOSITION = [13, 14, 15, 19]
QUALITY = [20]
RUNNING = FAST + COMPOSITION + QUALITY

# lib.rs:721-731
OVERRIDE_HIGH = f32(2950.0)
OVERRIDE_LOW = f32(2300.0)
OVERRIDE_RELEASE = f32(2633.7)
OVERRIDE_RESET_VALVE = f32(40.060)
OVERRIDE_RESET_SETPOINT = f32(0.33712)


def preset(number: int) -> Preset:
    return next(p for p in PRESET if p.tuning.number == number)


@dataclass
class Loop:
    previous_error: float = 0.0

    def increment(self, t: Tuning, setpoint: float, measurement: float, dt_h: float) -> float:
        """lib.rs:144-167 속도형 증분."""
        error = (setpoint - measurement) * f32(100.0) / t.span if t.span is not None else setpoint - measurement
        if t.reset_h is not None:
            inc = t.gain * ((error - self.previous_error) + error * dt_h * t.period.seconds / t.reset_h)
        else:
            inc = t.gain * (error - self.previous_error)
        self.previous_error = error
        return inc


class Override(Enum):
    RELEASED = 0
    OPEN = 1
    SHUT = 2


def override_step(state: Override, pressure: float) -> tuple[Override, tuple]:
    """lib.rs:756-788. 반환: (새 상태, ('hold', 위치) | ('release', 밸브, 설정값) | ('run',))."""
    if pressure >= OVERRIDE_HIGH:
        return Override.OPEN, ("hold", 100.0)
    if state == Override.OPEN and pressure >= OVERRIDE_RELEASE:
        return Override.OPEN, ("hold", 100.0)
    if state == Override.OPEN:
        return Override.RELEASED, ("release", OVERRIDE_RESET_VALVE, OVERRIDE_RESET_SETPOINT)
    if pressure <= OVERRIDE_LOW:
        return Override.SHUT, ("hold", 0.0)
    if state == Override.SHUT and pressure <= OVERRIDE_RELEASE:
        return Override.SHUT, ("hold", 0.0)
    if state == Override.SHUT:
        return Override.RELEASED, ("release", OVERRIDE_RESET_VALVE, OVERRIDE_RESET_SETPOINT)
    return Override.RELEASED, ("run",)


@dataclass
class Scheme:
    """lib.rs:912-1054."""

    loops: dict[int, Loop] = field(default_factory=lambda: {p.tuning.number: Loop() for p in PRESET})
    setpoints: list[float] = field(default_factory=lambda: _default_setpoints())
    purge: Override = Override.RELEASED

    def setpoint(self, index: int) -> float:
        return self.setpoints[index - 1]

    def set_setpoint(self, index: int, value: float) -> None:
        self.setpoints[index - 1] = value

    def step(self, step: int, measurements: list[float], valves: list[float], dt_h: float) -> None:
        if step % Period.FAST.value == 0:
            for n in FAST:
                self.run(n, measurements, valves, dt_h)
        if step % Period.COMPOSITION.value == 0:
            for n in COMPOSITION:
                self.run(n, measurements, valves, dt_h)
        if step % Period.QUALITY.value == 0:
            for n in QUALITY:
                self.run(n, measurements, valves, dt_h)

    @staticmethod
    def clamp(valves: list[float]) -> None:
        for i in range(11):  # CONSHAND: 11개, 12번(교반기)은 건드리지 않음
            if valves[i] <= 0.0:
                valves[i] = 0.0
            if valves[i] >= 100.0:
                valves[i] = 100.0

    def run(self, number: int, measurements: list[float], valves: list[float], dt_h: float) -> None:
        entry = preset(number)
        if number == 6:
            self.purge, act = override_step(self.purge, measurements[12])
            if act[0] == "hold":
                valves[5] = act[1]
                return
            if act[0] == "release":
                valves[5], self.setpoints[5] = act[1], act[2]
                self.loops[6].previous_error = 0.0
                return
        sp = self.setpoints[entry.setpoint_index - 1]
        inc = self.loops[number].increment(entry.tuning, sp, measurements[entry.tuning.measurement - 1], dt_h)
        out = entry.tuning.output
        if out.kind == "valve":
            valves[out.index - 1] += inc
        else:
            self.setpoints[out.index - 1] += inc * out.span / f32(100.0)


def _default_setpoints() -> list[float]:
    sp = [0.0] * 20
    for p in PRESET:
        sp[p.setpoint_index - 1] = p.setpoint
    return sp


# ------------------------------------------------------------------ 구조 분석
def loop_reading(xmeas: int) -> Preset | None:
    """XMEAS(n) 을 읽는 실행 중 루프. CONTRL22 는 실행되지 않으므로 제외."""
    return next((p for p in PRESET if p.tuning.measurement == xmeas and p.tuning.number in RUNNING), None)


def writer_of(setpoint_index: int) -> Preset | None:
    """SETPT(n) 을 출력으로 쓰는 실행 중 상위 루프."""
    return next((p for p in PRESET if p.tuning.output.kind == "setpoint"
                 and p.tuning.output.index == setpoint_index and p.tuning.number in RUNNING), None)


def owner_of(setpoint_index: int) -> Preset | None:
    """SETPT(n) 을 목표로 읽는 실행 중 루프."""
    return next((p for p in PRESET if p.setpoint_index == setpoint_index and p.tuning.number in RUNNING), None)


def top_level_setpoints() -> list[int]:
    """어떤 루프도 덮어쓰지 않는 SETPT. 감독기가 바꿔도 상위 루프가 되돌리지 않는 것."""
    return sorted(p.setpoint_index for p in PRESET
                  if p.tuning.number in RUNNING and writer_of(p.setpoint_index) is None)


def chain_from_setpoint(setpoint_index: int) -> list[Preset]:
    """SETPT(n) 을 읽는 루프부터 밸브까지의 cascade."""
    out, p = [], owner_of(setpoint_index)
    while p is not None:
        out.append(p)
        o = p.tuning.output
        p = owner_of(o.index) if o.kind == "setpoint" else None
    return out


def chain_for_measurement(xmeas: int) -> list[Preset]:
    """XMEAS(n) 을 읽는 루프부터 밸브까지. 상위 루프가 있으면 그 위도 함께 보여 준다(outer → inner)."""
    p = loop_reading(xmeas)
    if p is None:
        return []
    outer = []
    w = writer_of(p.setpoint_index)
    while w is not None:
        outer.insert(0, w)
        w = writer_of(w.setpoint_index)
    return outer + chain_from_setpoint(p.setpoint_index)


@dataclass(frozen=True)
class KickStep:
    loop: int
    period_s: int
    control: Literal["P", "PI"]
    delta_error_pct: float
    target: str  # 'SETPT(6)' 또는 'XMV(6)'
    delta: float  # 대상의 공학 단위 변화 (XMV 는 %)
    integral_per_run: float | None  # 오차가 유지될 때 실행 1회당 추가 변화(같은 단위). P 전용이면 None


def cascade_response(setpoint_index: int, delta: float, dt_h: float = 1.0 / 3600.0) -> list[KickStep]:
    """SETPT(n) 을 delta 만큼 바꿨을 때, 측정값이 아직 그대로인 동안 각 루프가 내는 증분.

    속도형이므로 비례항은 오차 변화량만큼 한 번 움직인다: Δu = K·Δe, Δe = Δsetpoint·100/span.
    하위 목표로 가는 출력은 lib.rs:1050 처럼 Δsetpoint_inner = Δu·span_inner/100 이다.
    적분항(PI)은 오차가 남아 있는 동안 실행마다 K·e·Δt·P/τ 를 더한다. 여기서는 새로 생긴 오차분만 계산한다.
    """
    steps: list[KickStep] = []
    d_sp = delta
    for p in chain_from_setpoint(setpoint_index):
        t = p.tuning
        de = d_sp * f32(100.0) / t.span if t.span is not None else d_sp
        du = t.gain * de
        integ = t.gain * de * dt_h * t.period.seconds / t.reset_h if t.reset_h is not None else None
        if t.output.kind == "setpoint":
            scale = t.output.span / f32(100.0)
            d_sp = du * scale
            steps.append(KickStep(t.number, t.period.value, "PI" if t.reset_h else "P", de,
                                  f"SETPT({t.output.index})", d_sp, integ * scale if integ is not None else None))
        else:
            steps.append(KickStep(t.number, t.period.value, "PI" if t.reset_h else "P", de,
                                  f"XMV({t.output.index})", du, integ))
    return steps
