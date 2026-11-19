from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zoro_rag import ZoroRAG
from zoro_rag.evidence import EvidenceExtractor

ROOT = Path(__file__).resolve().parents[2]


def dump(value, output: str | None = None) -> None:
    text = value.model_dump_json(indent=2) if hasattr(value, "model_dump_json") else json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def print_results(results, question: str = "") -> None:
    evidence = EvidenceExtractor().extract(results, focus=question)
    if not evidence:
        print("검색 결과가 없습니다.")
        return
    print(f"\n검색 결과 {len(evidence)}개 · 문헌 근거 (자동 생성 진단 아님)")
    for number, (item, hit) in enumerate(zip(evidence, results), 1):
        location = item.provenance["source_file"]
        if item.source_page is not None:
            location += f" · p.{item.source_page}"
        if item.source_section:
            location += f" · {item.source_section}"
        print(f"\n[{number}] {item.statement}\n출처: {location}")
        if hit.reranker_score is not None:
            print(f"관련도 점수: {hit.reranker_score:.4f} (Qwen 리랭커)")
        quote = item.supporting_quote
        if len(quote) > 1200:
            quote = quote[:1200].rsplit(" ", 1)[0] + " … (전체 원문은 query --json)"
        print(f"청크: {item.source_chunk_id}\n{quote}")
        for warning in item.provenance.get("parsing_warnings", []):
            print(f"자료 경고: {warning}")
    print()


def interactive(rag: ZoroRAG) -> None:
    rag.check_index()
    print("Zoro RAG · 공정 문헌 검색")
    print("질문을 입력하세요. /quit 종료 · /stats 인덱스 정보")
    print("예: What is the reactor pressure shutdown limit? (영문 원문은 영어 질의 권장)")
    print("첫 검색에는 모델 로딩 시간이 필요하며, 다음 질문부터 모델을 재사용합니다.")
    while True:
        try:
            question = input("\n검색> ").strip()
        except EOFError:
            print()
            return
        if question.lower() in {"/quit", "/exit", "quit", "exit"}:
            return
        if not question:
            continue
        if question == "/stats":
            dump(rag.stats())
            continue
        try:
            print_results(rag.query(question), question)
        except (RuntimeError, ValueError) as exc:
            print(f"오류: {exc}", file=sys.stderr)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상의 정수를 입력하세요.")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Zoro RAG: 기본 실행은 IDV(8) 문서 색인 → 알람 JSON 처리 → 결과 저장")
    parser.add_argument("--config", default=str(ROOT / "config/demo_idv8.yaml"))
    parser.add_argument("--input", dest="event_input", help="알림 JSON 경로 (기본: examples/idv8/alarm_event.json 데모)")
    parser.add_argument("--output", dest="event_output", default=str(ROOT / "artifacts/idv8/result.json"), help="알람 결과 JSON 저장 경로")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("interactive", help="점검용 질문 검색")
    ingest = commands.add_parser("ingest", help="지식 문서를 색인")
    ingest.add_argument("--rebuild", action="store_true")
    inspect = commands.add_parser("inspect-chunks", help="청크 CSV와 파싱 상태 확인")
    inspect.add_argument("--output", default="artifacts/chunk_preview.csv")
    alarm = commands.add_parser("alarm", help="알람 JSON에서 근거 검색")
    alarm.add_argument("--input", required=True)
    alarm.add_argument("--output")
    query = commands.add_parser("query", help="질문으로 한 번 검색")
    query.add_argument("--question", required=True)
    query.add_argument("--top-k", type=positive_int)
    query.add_argument("--json", action="store_true", help="전체 검색 결과를 JSON으로 출력")
    query.add_argument("--output", help="전체 검색 결과 JSON 저장")
    commands.add_parser("stats", help="모델 로딩 없이 인덱스 확인")
    evaluate = commands.add_parser("evaluate", help="초안 평가 사례 실행")
    evaluate.add_argument("--cases", default=str(ROOT / "evaluation/cases.json"))
    evaluate.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is not None and args.event_input:
        parser.error("--input은 기본 실행에 사용하거나 alarm 명령 뒤에 지정하세요.")
    try:
        with ZoroRAG(args.config) as rag:
            if args.command is None:
                path = Path(args.event_input) if args.event_input else ROOT / "examples/idv8/alarm_event.json"
                if not args.event_input:
                    print("입력을 지정하지 않아 IDV(8) 데모를 처리합니다: examples/idv8/alarm_event.json", file=sys.stderr)
                event = json.loads(path.read_text(encoding="utf-8"))
                print(f"입력: {path.resolve()}", file=sys.stderr)
                print("1/2: 지식 문서와 SOP 색인 준비 (변경된 청크만 갱신)", file=sys.stderr)
                report = rag.ingest()
                print(f"청크 {report['chunk_count']}개 · 갱신 {report['indexed']}개 · 유지 {report['unchanged']}개", file=sys.stderr)
                if not args.event_input:
                    dump(report, str(ROOT / "artifacts/idv8/ingest.json"))
                print("2/2: 알람 JSON으로 근거와 SOP 검토 계획 검색", file=sys.stderr)
                result = rag.process_alarm_event(event)
                dump(result, args.event_output)
                dump(result)
                print(f"결과 저장: {Path(args.event_output).resolve()}", file=sys.stderr)
            elif args.command == "interactive":
                interactive(rag)
            elif args.command == "ingest":
                dump(rag.ingest(rebuild=args.rebuild))
            elif args.command == "inspect-chunks":
                dump(rag.inspect_chunks(args.output))
            elif args.command == "alarm":
                event = json.loads(Path(args.input).read_text(encoding="utf-8"))
                dump(rag.process_alarm_event(event), args.output)
            elif args.command == "query":
                results = rag.query(args.question, limit=args.top_k)
                if args.json or args.output:
                    dump([item.model_dump(mode="json") for item in results], args.output)
                else:
                    print_results(results, args.question)
            elif args.command == "stats":
                dump(rag.stats())
            elif args.command == "evaluate":
                rag.check_index()
                # Keep evaluation scripts outside the runtime package.
                sys.path.insert(0, str(ROOT))
                from evaluation.evaluate import evaluate_cases
                dump(evaluate_cases(rag, Path(args.cases)), args.output)
    except KeyboardInterrupt:
        print("\n검색을 종료했습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"오류 ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0
