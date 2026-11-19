"""Mount the pinned TEP experiment workspace alongside the live console."""

import base64
import json
from threading import RLock

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
from .scenario import DEFAULT_SEED

EXPERIMENTS_PATH = "/experiments/"
TEXTAREA_STYLE = {"width": "100%", "minHeight": "8rem", "fontFamily": "monospace"}


class ExperimentStore(RunStore):
    def __init__(self, capacity=50):
        super().__init__(capacity=capacity)
        self.lock = RLock()

    def put(self, result):
        with self.lock:
            return super().put(result)

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
    for component in layout._traverse():
        if isinstance(component, dcc.Link) and component.href in {"/", "/about", "/mcp"}:
            component.href = EXPERIMENTS_PATH + component.href.lstrip("/")
        if getattr(component, "id", None) == "tabs":
            component.children[-1].children = _assistant_tab()
            component.children.append(dcc.Tab(label="JSON 시나리오", value="json-scenario", children=_json_tab()))
    header = layout.children[4]
    header.children[0].children[0].children = "ProtoPilot · TEP 실험"
    header.children[1].children.insert(0, html.A("라이브 관제로 돌아가기", href="/"))
    header.children[1].children.append(html.A("Fortran 브라우저 실행", href="/fortran/"))
    return layout


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
    app.layout = _layout()
    store = ExperimentStore()
    tools = TepToolset(store=store)
    register_callbacks(app, store)

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
        if not ids or fmt not in {"csv", "json", "parquet"}:
            return {"error": "실행 ID와 CSV·JSON·Parquet 중 하나를 지정하세요."}, 400
        runs = [store.get(run_id) for run_id in ids]
        if any(run is None for run in runs):
            return {"error": "실험 기록을 찾을 수 없습니다."}, 404
        body, filename = build_dataset(runs, fmt=fmt)
        return Response(body, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    app.run_store = store
    return app
