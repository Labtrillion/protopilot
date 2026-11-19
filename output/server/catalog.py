"""Channel catalog and the instrument-to-equipment mapping from the TEP flowsheet."""

from tep_studio import TEP_SCHEMA

GROUPS = (
    ("measurements", "기본 계측"),
    ("additional_measurements", "확장 계측"),
    ("disturbance_monitors", "외란 진단"),
    ("process_monitors", "공정 진단"),
    ("concentration_monitors", "조성 진단"),
    ("manipulated_variables", "조작값"),
)
MEAS_UNITS = (
    "feeds feeds feeds feeds compressor reactor reactor reactor reactor purge "
    "separator separator separator separator stripper stripper product stripper "
    "reboiler compressor reactor condenser"
).split() + ["gc_feed"] * 6 + ["gc_purge"] * 8 + ["gc_product"] * 5
ACTUATORS = "feeds feeds feeds feeds compressor purge separator product reboiler reactor condenser reactor".split()
DISTURBANCES = (
    "feeds feeds feeds reactor condenser feeds feeds feeds feeds feeds reactor condenser "
    "reactor reactor condenser stripper reactor condenser plant plant feeds feeds feeds feeds "
    "feeds feeds reactor condenser"
).split()
STREAMS = {
    "stream 1": "feeds", "stream 2": "feeds", "stream 3": "feeds", "stream 4": "feeds",
    "stream 5": "stripper", "stream 6": "reactor", "stream 7": "reactor", "stream 8": "compressor",
    "stream 9": "purge", "stream 10": "separator", "stream 11": "product",
}
KOREAN = (
    "A 원료 유량|D 원료 유량|E 원료 유량|A/C 원료 유량|재순환 가스 유량|반응기 입구 유량|"
    "반응기 가스 압력|반응기 액위|반응기 온도|퍼지 가스 유량|분리기 온도|분리기 액위|"
    "분리기 가스 압력|분리기 하부 유량|스트리퍼 액위|스트리퍼 가스 압력|제품 유량|스트리퍼 온도|"
    "재비기 스팀 유량|압축기 동력|반응기 냉각수 출구 온도|응축기 냉각수 출구 온도"
).split("|")
MV_LABELS = (
    "D 원료 밸브|E 원료 밸브|A 원료 밸브|A/C 원료 밸브|압축기 바이패스 밸브|퍼지 밸브|"
    "분리기 하부 밸브|제품 출구 밸브|재비기 스팀 밸브|반응기 냉각수 밸브|응축기 냉각수 밸브|반응기 교반기"
).split("|")
SETPOINTS = {
    "reactor_pressure": ("반응기 압력", "kPa(g)", "reactor", 2000, 2895),
    "reactor_temperature": ("반응기 온도", "°C", "reactor", 90, 150),
    "reactor_level": ("반응기 액위", "%", "reactor", 50, 100),
    "separator_level": ("분리기 액위", "%", "separator", 30, 100),
    "stripper_level": ("스트리퍼 액위", "%", "stripper", 30, 100),
    "production_rate": ("제품 유량", "m³/h", "stripper", 1, 40),
    "pct_g": ("제품 G 조성 목표", "mol %", "stripper", 1, 95),
}

# Downs & Vogel Table 6 operating limits. The engine separately reports actual shutdowns.
PV_TAGS = {
    "reactor_pressure": 7, "reactor_temperature": 9, "reactor_level": 8,
    "separator_level": 12, "stripper_level": 15, "production_rate": 17, "pct_g": 40,
}

OPERATING_LIMITS = {
    "XMEAS(7)": (None, 2895), "XMEAS(8)": (50, 100), "XMEAS(9)": (None, 150),
    "XMEAS(12)": (30, 100), "XMEAS(15)": (30, 100),
}
TRIPS = {1: 7, 2: 8, 3: 8, 4: 9, 5: 12, 6: 12, 7: 15, 8: 15}


def monitor_unit(variable):
    name = variable.name.removeprefix("delay_free_")
    if variable.stream in STREAMS:
        return STREAMS[variable.stream]
    if name.startswith("idv"):
        return DISTURBANCES[int(name[3:5]) - 1]
    for prefix, unit in (
        ("reactor_effluent", "reactor"), ("reactor", "reactor"), ("condenser", "condenser"),
        ("separator", "separator"), ("stripper_underflow", "product"),
        ("stripper", "stripper"), ("feed_", "feeds"), ("purge", "purge"),
    ):
        if name.startswith(prefix):
            return unit
    return "plant"


def channel(group, var):
    unit = monitor_unit(var)
    label = var.description
    if group == "measurements":
        unit = MEAS_UNITS[var.index]
        if var.index < len(KOREAN):
            label = KOREAN[var.index]
        else:
            locations = {"gc_feed": "반응기 입구", "gc_purge": "퍼지", "gc_product": "제품"}
            label = f"{locations[unit]} {var.component} 조성"
    if group == "manipulated_variables":
        unit = ACTUATORS[var.index]
        label = MV_LABELS[var.index]
    unit_label = {"degC": "°C", "m3/h": "m³/h", "kscmh": "kscm/h", "kPa gauge": "kPa(g)"}.get(var.unit, var.unit)
    if group == "concentration_monitors":
        unit_label = "mol fraction"  # Native XMEASCOMP values are in [0, 1].
    return {
        "id": var.legacy_symbol, "name": var.name, "label": label, "description": var.description,
        "unit": unit_label,
        "group": group, "area": unit, "period": var.sample_period_hours,
        "delay": (var.sample_period_hours or 0) * var.delay_samples,
    }


CHANNELS = {entry["id"]: entry for group, _ in GROUPS for v in getattr(TEP_SCHEMA, group) if (entry := channel(group, v))}
METADATA = {
    "channels": list(CHANNELS.values()),
    "groups": [{"id": group, "label": label, "count": len(getattr(TEP_SCHEMA, group))} for group, label in GROUPS],
    "disturbances": [
        {"id": v.legacy_symbol, "label": v.description, "area": DISTURBANCES[v.index]}
        for v in TEP_SCHEMA.disturbances
    ],
    "setpoints": [
        {"id": key, "label": label, "unit": unit, "area": area, "min": lo, "max": hi, "pv": f"XMEAS({PV_TAGS[key]})"}
        for key, (label, unit, area, lo, hi) in SETPOINTS.items()
    ],
}
