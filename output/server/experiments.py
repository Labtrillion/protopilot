"""Mount the pinned TEP experiment workspace alongside the live console."""

import base64
import json
from dataclasses import replace
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace

from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate
from flask import Response, request
from tep_studio.agent.tools import TepToolset
from tep_studio.ui import ScenarioConfig, build_dataset, run_scenario
from tep_studio.ui.app import _INDEX_STRING
from tep_studio.ui.callbacks import register_callbacks
from tep_studio.ui.chat_panel import _run_figure
from tep_studio.ui.layout import build_layout
from tep_studio.ui.store import RunStore

from .assistant import respond
from .scenario import DEFAULT_SEED, KERNEL_REVISION

EXPERIMENTS_PATH = "/experiments/"
TEXTAREA_STYLE = {"width": "100%", "minHeight": "8rem", "fontFamily": "monospace"}
DATASET_MIME = {"csv": "text/csv", "json": "application/json", "parquet": "application/vnd.apache.parquet"}


class ExperimentStore(RunStore):
    def __init__(self, capacity=50):
        super().__init__(capacity=capacity)
        self.lock = RLock()

    def put(self, result):
        scenario = result.scenario.to_dict()
        config_hash = sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record = {**(result.record or {}), "kernel_revision": KERNEL_REVISION, "run_id": result.run_id,
                  "scenario_config": scenario, "scenario_config_hash": config_hash,
                  "terminated": result.terminated, "metrics": result.metrics}
        with self.lock:
            return super().put(replace(result, record=record))

    def get(self, run_id):
        with self.lock:
            return super().get(run_id)

    def ids(self):
        with self.lock:
            return super().ids()

    def evict(self, run_id):
        with self.lock:
            super().evict(run_id)


def _assistant_tab():
    return html.Section([
        dcc.Store(id="proto-assistant-history", data=[]),
        html.H2("Zoro-1 Assistant"),
        html.P("예: IDV4를 1시간 후 적용하고 4시간 폐루프 실행을 정상 운전과 비교해줘."),
        dcc.Textarea(id="proto-assistant-input", placeholder="시뮬레이션 실행·비교 요청", style=TEXTAREA_STYLE),
        html.Button("분석 요청", id="proto-assistant-send", className="tep-btn tep-btn--primary"),
        dcc.Loading(html.Div(id="proto-assistant-log", role="log", **{"aria-live": "polite"})),
    ])


def _json_tab():
    example = ScenarioConfig(name="baseline", horizon=4, seed=DEFAULT_SEED).to_json()
    return html.Section([
        html.H2("JSON 시나리오"),
        html.P("실험 설정 JSON의 운전 모드, 초기 상태, 외란별 시작 시각, 튜닝 및 스텝 시험을 적용합니다."),
        dcc.Upload(html.Button("JSON 파일 열기", className="tep-btn tep-btn--secondary"), id="proto-json-upload", accept=".json,application/json"),
        dcc.Textarea(id="proto-json-config", value=example, style={**TEXTAREA_STYLE, "minHeight": "22rem"}),
        html.Button("JSON 시나리오 실행", id="proto-json-run", className="tep-btn tep-btn--primary"),
        html.Div(id="proto-json-notice", role="status"),
        dcc.Loading(html.Div(id="proto-json-result", role="status")),
    ])


def _layout():
    layout = build_layout()
    tabs = next(component for component in layout._traverse() if getattr(component, "id", None) == "tabs")
    dataset = next(tab for tab in tabs.children if tab.value == "dataset")
    dataset_ids = {component.id for component in dataset._traverse() if isinstance(getattr(component, "id", None), str)}
    tabs.children.remove(dataset)
    next(tab for tab in tabs.children if tab.value == "assistant").children = _assistant_tab()
    tabs.children.append(dcc.Tab(label="JSON 시나리오", value="json-scenario", children=_json_tab()))
    simulate = next(tab.children for tab in tabs.children if tab.value == "simulate")
    config_body = simulate.children[0].children
    mode = next(child for child in config_body if any(
        getattr(field, "id", None) == "mode-select" for field in child._traverse()
    ))
    advanced = [child for child in config_body if isinstance(child, html.Details)]
    config_body.remove(mode)
    for child in advanced:
        config_body.remove(child)
    position = next(index for index, child in enumerate(config_body) if getattr(child, "id", None) == "run-btn")
    config_body.insert(position, html.Details(
        [html.Summary("고급 설정"), mode, *advanced],
        style={"marginBottom": "12px"},
    ))
    for component in layout._traverse():
        if isinstance(component, dcc.Link) and component.href in {"/", "/about", "/mcp"}:
            component.href = EXPERIMENTS_PATH + component.href.lstrip("/")
        children = getattr(component, "children", None)
        if isinstance(children, list):
            for index, child in enumerate(children):
                if getattr(child, "id", None) == "scenario-upload":
                    child.style = {"display": "none"}
                    children.insert(index, html.Button("Load scenario in JSON", id="proto-open-json",
                                                       className="tep-btn tep-btn--secondary"))
                    break
    header = layout.children[4]
    header.children[0].children[0].children = "ProtoPilot · TEP 실험"
    header.children[0].children[1].children = "독립 시뮬레이션 · 실행 비교 · 실험 기록"
    header.children[1].children.insert(0, html.A("라이브 관제로 돌아가기", href="/"))
    return layout, dataset_ids


def _register_demo_callbacks(app, store, dataset_ids):
    def visible(register):
        def callback(*dependencies, **kwargs):
            if any(isinstance(getattr(dep, "component_id", None), str) and dep.component_id in dataset_ids
                   for dep in dependencies):
                return lambda function: function
            return register(*dependencies, **kwargs)
        return callback

    register_callbacks(SimpleNamespace(callback=visible(app.callback),
                                       clientside_callback=visible(app.clientside_callback)), store)

    @app.callback(
        Output("compare-table", "data"), Output("compare-table", "columns"),
        Input("session-runs", "data"), Input("compare-var", "value"),
    )
    def update_compare(rows, _variables):
        keys = ("run_id", "name", "loop_type", "terminated", "final_time", "peak_reactor_pressure",
                "iae_reactor_pressure", "ise_reactor_pressure", "time_to_shutdown")
        return rows or [], [{"name": key, "id": key} for key in keys]

    @app.callback(Output("record-run", "options"), Input("session-runs", "data"), Input("record-run", "id"))
    def update_records(rows, _id):
        return [{"label": f"{row['name']} ({row['run_id']})", "value": row["run_id"]} for row in rows or []]


def _config(text):
    raw = json.loads(text)
    if not isinstance(raw, dict) or set(raw) - (set(ScenarioConfig.__dataclass_fields__) | {"schema_version"}):
        raise ValueError("TEP 실험 설정 JSON을 확인하세요.")
    if raw.get("schema_version", 1) != 1:
        raise ValueError("지원하는 시나리오 스키마 버전은 1입니다.")
    return ScenarioConfig.from_dict({"seed": DEFAULT_SEED, **raw})


def create_experiments(root):
    app = Dash(__name__, title="ProtoPilot · TEP 실험", url_base_pathname=EXPERIMENTS_PATH,
               suppress_callback_exceptions=True)
    app.index_string = _INDEX_STRING
    app.layout, dataset_ids = _layout()
    store = ExperimentStore()
    tools = TepToolset(store=store)
    _register_demo_callbacks(app, store, dataset_ids)

    @app.callback(Output("tabs", "value"), Input("proto-open-json", "n_clicks"), prevent_initial_call=True)
    def open_json(_clicked):
        return "json-scenario"

    @app.callback(
        Output("proto-json-config", "value"), Output("proto-json-notice", "children"),
        Input("proto-json-upload", "contents"), prevent_initial_call=True,
    )
    def load_json(contents):
        if not contents:
            raise PreventUpdate
        try:
            text = base64.b64decode(contents.split(",", 1)[1], validate=True).decode("utf-8-sig")
            _config(text)
            return text, "파일을 열었습니다."
        except (IndexError, KeyError, TypeError, ValueError) as error:
            return no_update, f"시나리오를 열 수 없습니다. {error}"

    @app.callback(
        Output("proto-json-result", "children"),
        Output("active-run", "data", allow_duplicate=True),
        Output("session-runs", "data", allow_duplicate=True),
        Input("proto-json-run", "n_clicks"), State("proto-json-config", "value"),
        State("session-runs", "data"), prevent_initial_call=True,
        running=[(Output("proto-json-run", "disabled"), True, False)],
    )
    def run_json(_clicked, text, session):
        try:
            run = run_scenario(_config(text))
            store.put(run)
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            return f"실험 실패 · {error}", no_update, no_update
        return [html.A("실행 설정 JSON 저장", href=f"{EXPERIMENTS_PATH}api/scenario/{run.run_id}"), _run_figure(run)], run.run_id, (session or []) + [run.summary()]

    @app.callback(
        Output("proto-assistant-log", "children"), Output("proto-assistant-history", "data"),
        Output("proto-assistant-input", "value"),
        Output("active-run", "data", allow_duplicate=True),
        Output("session-runs", "data", allow_duplicate=True),
        Input("proto-assistant-send", "n_clicks"), State("proto-assistant-input", "value"),
        State("proto-assistant-history", "data"), State("session-runs", "data"),
        State("proto-assistant-log", "children"), prevent_initial_call=True,
        running=[(Output("proto-assistant-send", "disabled"), True, False)],
    )
    def send_message(_clicked, text, history, session, previous):
        if not isinstance(text, str) or not text.strip():
            raise PreventUpdate
        if len(text) > 8192:
            return (previous or []) + [html.P("8,192자 이하의 요청을 입력하세요.")], no_update, no_update, no_update, no_update
        turn = respond(text.strip(), history or [], tools, root / "agent-trace.jsonl")
        runs = [run for run_id in turn.run_ids if (run := store.get(run_id)) is not None]
        blocks = (previous or [])[-24:] + [html.H3(text), dcc.Markdown(turn.text)]
        blocks.extend(_run_figure(run) for run in runs[-3:])
        return (blocks, turn.history, "", runs[-1].run_id if runs else no_update,
                (session or []) + [run.summary() for run in runs] if runs else no_update)

    @app.server.get(f"{EXPERIMENTS_PATH}api/scenario/<run_id>")
    def get_scenario(run_id):
        result = store.get(run_id)
        if result is None:
            return {"error": "실험 기록을 찾을 수 없습니다."}, 404
        return Response(result.scenario.to_json(), mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}-scenario.json"'})

    @app.server.post(f"{EXPERIMENTS_PATH}api/run")
    def api_run():
        try:
            result = run_scenario(_config(request.get_data(as_text=True)))
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            return {"error": str(error)}, 400
        store.put(result)
        return result.summary(), 201

    @app.server.get(f"{EXPERIMENTS_PATH}api/dataset")
    def dataset():
        ids = request.args.getlist("run_id")
        fmt = request.args.get("format", "csv")
        if not ids or fmt not in DATASET_MIME:
            return {"error": "실행 ID와 CSV·JSON·Parquet 중 하나를 지정하세요."}, 400
        runs = [store.get(run_id) for run_id in ids]
        if any(run is None for run in runs):
            return {"error": "실험 기록을 찾을 수 없습니다."}, 404
        body, filename = build_dataset(runs, fmt=fmt)
        return Response(body, mimetype=DATASET_MIME[fmt], headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    app.run_store = store
    return app
