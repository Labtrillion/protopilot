"""Build TEP evidence indexes and return cited context for Zoro."""
import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent
KNOWLEDGE = ROOT / "Zoro-RAG-Knowledge"
IDV_CATALOG = ROOT.parent / "zoro_rag_knowledge" / "05_Disturbance_knowledge" / "idv_catalog.json"
STATE = ROOT / "index"
INDEX = STATE / "corpus.json"
EMBEDDING_MODEL = "BAAI/bge-m3"
RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"
IDV_PATTERN = re.compile(r"\bidv\s*[-_( ]?\s*([0-9]+)\s*\)?", re.I)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[가-힣]+", re.I)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_source(data, kind):
    stripped = data.lstrip()
    if kind == "pdf":
        return stripped.startswith(b"%PDF")
    if kind == "html":
        return b"<html" in stripped[:4096].lower()
    return not stripped.startswith(b"<") and len(stripped) >= 50


def download_sources(sources):
    for source_id, source in sources.items():
        if "path" not in source:
            continue
        path = KNOWLEDGE / source["path"]
        if path.exists() and valid_source(path.read_bytes(), source["type"]):
            continue
        for url in source["downloads"]:
            try:
                request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=20) as response:
                    data = response.read(25 * 1024 * 1024 + 1)
                if len(data) > 25 * 1024 * 1024 or not valid_source(data, source["type"]):
                    raise ValueError("unexpected source format or size")
                atomic_write(path, data)
                break
            except (OSError, URLError, ValueError) as error:
                print(f"{source_id}: {error}", file=sys.stderr)


def split_source(text, limit=1800):
    text = " ".join(text.split())
    while len(text) > limit:
        boundary = text.rfind(" ", limit // 2, limit)
        boundary = limit if boundary < 0 else boundary
        yield text[:boundary]
        text = text[boundary:].strip()
    if text:
        yield text


def source_sections(path, kind):
    if kind == "pdf":
        from pypdf import PdfReader

        for number, page in enumerate(PdfReader(path).pages, 1):
            yield {"section": f"PDF page {number}", "pdf_page": number}, page.extract_text() or ""
    elif kind == "html":
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        area = soup.find("main") or soup.find("article") or soup.body or soup
        for unwanted in area.select("script,style,nav,header,footer"):
            unwanted.decompose()
        title, anchor, parts = path.stem, None, []
        for tag in area.find_all(["h1", "h2", "h3", "h4", "p", "table", "li", "pre"]):
            if tag.find_parent(["table", "li"]):
                continue
            if tag.name.startswith("h"):
                if parts:
                    yield {"section": title, "anchor": anchor}, "\n".join(parts)
                title = tag.get_text(" ", strip=True)
                anchor = tag.get("id") or tag.parent.get("id")
                parts = []
            elif tag.name == "table":
                for row in tag.find_all("tr"):
                    parts.append(" | ".join(cell.get_text(" ", strip=True)
                                            for cell in row.find_all(["td", "th"])))
            else:
                parts.append(tag.get_text(" ", strip=True))
        if parts:
            yield {"section": title, "anchor": anchor}, "\n".join(parts)
    else:
        title, anchor, parts = path.stem, None, []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if re.match(r"^#{1,6}\s", line):
                if parts:
                    yield {"section": title, "anchor": anchor}, "\n".join(parts)
                title = line.lstrip("# ").strip()
                anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
                parts = []
            else:
                parts.append(line)
        if parts:
            yield {"section": title, "anchor": anchor}, "\n".join(parts)


def tokenize(text):
    text = re.sub(r"\b(idv|xmeas|xmv)\s*[-_( ]?\s*(\d+)\s*\)?", r"\1\2", text.lower())
    words = TOKEN_PATTERN.findall(text)
    for word in tuple(words):
        if len(word) > 2 and "가" <= word[0] <= "힣":
            words.extend(word[i:i + 2] for i in range(len(word) - 1))
    return words


def search_text(record):
    return " ".join([record["title"], record["title"], record["text"],
                     *record.get("tags", []), *(f"IDV({i})" for i in record.get("ids", []))])


def sparse_index(records):
    counts = [Counter(tokenize(search_text(record))) for record in records]
    frequency = Counter(token for count in counts for token in count)
    average = sum(sum(count.values()) for count in counts) / len(counts)
    vocabulary = {word: i for i, word in enumerate(sorted(frequency))}
    vectors = []
    for count in counts:
        norm = 1.2 * (0.25 + 0.75 * sum(count.values()) / average)
        vectors.append({
            str(vocabulary[word]): round(
                math.log1p((len(counts) - frequency[word] + 0.5) / (frequency[word] + 0.5))
                * (tf * 2.2) / (tf + norm), 7
            ) for word, tf in count.items()
        })
    return vocabulary, vectors


def sop_chunks(path):
    """Keep each authored step together with its inherited operating conditions."""
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: expected an SOP object")

    def field(obj, key):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path.name}: missing text in {key}")
        return value.strip()

    def lines(obj, key):
        values = obj.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
            raise ValueError(f"{path.name}: invalid {key}")
        return [x.strip() for x in values]

    sop_id, version, title = (field(doc, key) for key in ("sop_id", "version", "title"))
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{path.name}: expected a non-empty steps array")
    source_id = f"team-sop/{sop_id}"
    source = {"title": title, "version": version, "url": doc.get("source_url") or path.name}
    records = []
    seen = set()
    for position, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"{path.name}: invalid step {position}")
        step_id = field(step, "step_id")
        if step_id in seen:
            raise ValueError(f"{path.name}: duplicate step {step_id}")
        seen.add(step_id)
        label = field(step, "title")
        actions = lines(step, "procedure")
        if not actions:
            raise ValueError(f"{path.name}: empty procedure in {step_id}")
        context = {key: lines(doc, key) + lines(step, key)
                   for key in ("preconditions", "constraints", "stop_conditions")}
        contents = [*context["preconditions"], *actions, *context["constraints"],
                    *context["stop_conditions"]]
        records.append({
            "id": f"{source_id}/{step_id}", "kind": "sop_step",
            "title": f"{title} · {label}", "text": "\n".join(contents),
            "procedure": actions, **context, "references": step.get("references", []),
            "source": {"id": source_id, "section": label, "sop_id": sop_id,
                       "step_id": step_id, "step_number": position, "file": path.name},
        })
    return source_id, source, records


def build():
    sources = read_json(KNOWLEDGE / "sources.json")
    records = read_json(KNOWLEDGE / "evidence.json")
    records.extend(read_json(KNOWLEDGE / "economics.json"))
    literature = {record["id"]: record for record in records}
    for guide in read_json(KNOWLEDGE / "diagnostic_guides.json"):
        citations = [literature[key]["source"] for key in guide["references"]]
        records.append({
            "id": guide["id"], "kind": "diagnostic_guide", "title": guide["title"],
            "text": guide["observation"], "tags": [*guide["tags"], guide["keywords"]],
            "source": citations[0], "references": citations,
            "interpretation": {key: guide[key] for key in
                               ("hypotheses", "check", "limitation", "tags")},
        })
    states = {}
    for source_id, source in sources.items():
        if "path" not in source:
            continue
        path = KNOWLEDGE / source["path"]
        states[source_id] = {"status": "verified_summary", "expected_path": source["path"],
                             "url": source["url"], "raw_chunks": 0}
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if not valid_source(raw, source["type"]):
            raise ValueError(f"Invalid source file: {path}")
        number = 0
        for section, content in source_sections(path, source["type"]):
            for chunk in split_source(content):
                number += 1
                record = {"id": f"raw-{source_id}-{number}", "kind": "source_excerpt",
                          "title": section["section"], "text": chunk,
                          "source": {"id": source_id, **section},
                          "ids": sorted({int(n) for n in IDV_PATTERN.findall(chunk)})}
                records.append(record)
        states[source_id].update(status="original_indexed" if number else "original_no_text",
                                  raw_chunks=number, sha256=digest(raw))

    for path in sorted((KNOWLEDGE / "SOP").glob("*.json")):
        source_id, source, chunks = sop_chunks(path)
        if source_id in sources:
            raise ValueError(f"{path.name}: duplicate SOP ID; index one active version per SOP")
        for chunk in chunks:
            for ref in chunk["references"]:
                if not isinstance(ref, dict) or ref.get("id") not in sources:
                    raise ValueError(f"{path.name}: unknown reference in {chunk['id']}")
        sources[source_id] = source
        records.extend(chunks)
        states[source_id] = {"status": "team_sop", "steps": len(chunks), "version": source["version"]}
    vocabulary, vectors = sparse_index(records)
    index = {"schema": 4, "records": records, "sources": sources, "source_status": states,
             "vocabulary": vocabulary, "sparse": vectors}
    atomic_write(INDEX, json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode())
    return index


class HybridSearch:
    def __init__(self, index, device=None, qdrant_url=None):
        from qdrant_client import QdrantClient, models
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.models = models
        self.records = {record["id"]: record for record in index["records"]}
        identity = json.dumps([EMBEDDING_MODEL, index["records"], index["sparse"]],
                              ensure_ascii=False).encode()
        self.collection = f"zoro_tep_{digest(identity)[:24]}"
        self.embedder = SentenceTransformer(EMBEDDING_MODEL, device=device)
        self.reranker = CrossEncoder(RERANKER_MODEL, device=device, max_length=2048)
        self.client = (QdrantClient(url=qdrant_url, api_key=os.getenv("QDRANT_API_KEY"))
                       if qdrant_url else QdrantClient(path=str(STATE / "qdrant")))
        expected = len(self.records)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config={"dense": models.VectorParams(
                    size=self.embedder.get_sentence_embedding_dimension(),
                    distance=models.Distance.COSINE)},
                sparse_vectors_config={"bm25": models.SparseVectorParams()})
        if self.client.count(self.collection, exact=True).count != expected:
            embeddings = self.embedder.encode(
                [search_text(record) for record in self.records.values()],
                normalize_embeddings=True, batch_size=8)
            points = [
                models.PointStruct(id=str(uuid5(NAMESPACE_URL, record["id"])),
                                   vector={"dense": dense.tolist(),
                                           "bm25": self.sparse_vector(sparse)},
                                   payload={"chunk_id": record["id"], "kind": record["kind"]})
                for record, dense, sparse in zip(
                    self.records.values(), embeddings, index["sparse"], strict=True)
            ]
            for start in range(0, len(points), 64):
                self.client.upsert(self.collection, points=points[start:start + 64], wait=True)

    def sparse_vector(self, weights):
        return self.models.SparseVector(indices=[int(i) for i in weights],
                                        values=list(weights.values()))

    def search(self, query, keywords, weights, limit, kind=None):
        models = self.models
        candidate_count = max(limit * 3, 12)
        embedding = self.embedder.encode(keywords, normalize_embeddings=True).tolist()
        selection = models.Filter(must=[models.FieldCondition(
            key="kind", match=models.MatchValue(value=kind))]) if kind else None
        prefetch = [models.Prefetch(query=embedding, using="dense", limit=candidate_count, filter=selection)]
        if weights:
            prefetch.append(models.Prefetch(query=self.sparse_vector(weights), using="bm25",
                                           limit=candidate_count, filter=selection))
        hits = self.client.query_points(
            self.collection, prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=candidate_count, with_payload=True).points
        records = [self.records[hit.payload["chunk_id"]] for hit in hits]
        if not records:
            return []
        scores = self.reranker.predict([(query, search_text(record)) for record in records])
        return sorted(zip(records, map(float, scores)), key=lambda item: -item[1])[:limit]


class EvidenceRAG:
    def __init__(self, mode="local", device=None, qdrant_url=None):
        self.index = read_json(INDEX) if INDEX.exists() else build()
        self.catalog = {row["id"]: row for row in read_json(IDV_CATALOG)}
        if self.index.get("schema") != 4:
            self.index = build()
        self.mode = mode
        self.hybrid = HybridSearch(self.index, device, qdrant_url) if mode == "hybrid" else None

    def citation(self, reference):
        source = self.index["sources"][reference["id"]]
        url = source["url"]
        if reference.get("anchor"):
            url += "#" + reference["anchor"]
        elif reference.get("pdf_page"):
            url += f"#page={reference['pdf_page']}"
        return {**reference, "title": source["title"], "version": source["version"], "url": url}

    def fault_details(self, fault_id):
        row = self.catalog[fault_id]
        result = {**row, "source": self.citation(row["source"])}
        if "apmonitor" in row:
            implementation = row["apmonitor"]
            result["apmonitor"] = {**implementation, "source": self.citation(implementation["source"])}
        return result

    def retrieve(self, query, scenario=None, k=5, kind=None):
        query = query.strip()
        if not query or len(query) > 2000 or not 1 <= k <= 20:
            raise ValueError("Query must contain 1–2000 characters, k must be 1–20.")
        if scenario is not None or re.search(
                r"(?:\bscenario|시나리오)\s*[-:#_]?\s*[a-h](?![a-z0-9])",
                query, re.I | re.ASCII):
            raise ValueError("진단 검색에는 시나리오 이름 대신 관측한 증상이나 태그를 입력하세요.")
        requested = list(dict.fromkeys(int(n) for n in IDV_PATTERN.findall(query)))
        unknown = [n for n in requested if n not in self.catalog]
        details = [self.fault_details(n) for n in requested if n in self.catalog]
        keywords = query
        vocabulary = self.index["vocabulary"]
        weights = {str(vocabulary[word]): 1.0 for word in tokenize(keywords) if word in vocabulary}

        if unknown and not details and not IDV_PATTERN.sub("", query).strip(" ?.!"):
            hits = []
        elif self.hybrid:
            hits = self.hybrid.search(query, keywords, weights, k, kind)
        else:
            ranked = []
            for record, vector in zip(self.index["records"], self.index["sparse"], strict=True):
                if kind and record["kind"] != kind:
                    continue
                score = sum(vector.get(word, 0.0) for word in weights)
                if score:
                    ranked.append((record, score))
            hits = sorted(ranked, key=lambda item: (-item[1], item[0]["id"]))[:k]

        evidence = []
        for record, score in hits:
            evidence.append({
                "id": record["id"], "kind": record["kind"], "title": record["title"],
                "text": record["text"], "score": round(score, 6),
                "scope": {"diagnostic_guide": "project_interpretation",
                          "evaluation_rule": "project_evaluation",
                          "sop_step": "team_sop",
                          "case_study": "published_case"}.get(record["kind"], "source_reference"),
                "procedure": record.get("procedure", []),
                "preconditions": record.get("preconditions", []),
                "constraints": record.get("constraints", []),
                "source": self.citation(record["source"]),
            })
            if record["kind"] == "sop_step":
                evidence[-1]["stop_conditions"] = record["stop_conditions"]
            if record["kind"] == "diagnostic_guide":
                evidence[-1]["interpretation"] = record["interpretation"]
            if record.get("references"):
                evidence[-1]["supporting_sources"] = [self.citation(ref) for ref in record["references"]]
        return {
            "query": query, "retrieval_mode": self.mode,
            "idv_catalog": details, "unrecognized_idvs": unknown,
            "evidence_status": "available" if evidence or details else "no_match",
            "evidence": evidence,
        }


def serve(rag, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/health":
                self.reply(200, {"mode": rag.mode, "chunks": len(rag.index["records"]),
                                 "sources": rag.index["source_status"]})
                return
            if url.path != "/retrieve":
                self.reply(404, {"error": "Use /retrieve or /health"})
                return
            params = parse_qs(url.query)
            try:
                self.reply(200, rag.retrieve(params.get("q", [""])[0],
                                             params.get("scenario", [None])[0],
                                             int(params.get("k", [5])[0]), params.get("kind", [None])[0]))
            except ValueError as error:
                self.reply(400, {"error": str(error)})

        def reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", len(data))
            self.end_headers()
            self.wfile.write(data)
    with HTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Zoro RAG listening at http://127.0.0.1:{port}", flush=True)
        server.serve_forever()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download")
    download.add_argument("--strict", action="store_true")
    for name in ("build", "query", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--mode", choices=["local", "hybrid"], default="local")
        command.add_argument("--device", help="Model device, e.g. cpu or cuda")
        command.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL"))
    commands.choices["query"].add_argument("query")
    commands.choices["query"].add_argument("--k", type=int, default=5)
    commands.choices["query"].add_argument("--kind", help="Filter evidence by kind, e.g. sop_step or cost_model")
    commands.choices["serve"].add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    try:
        if args.command == "download":
            sources = read_json(KNOWLEDGE / "sources.json")
            download_sources(sources)
            states = {key: (KNOWLEDGE / source["path"]).is_file() and
                           valid_source((KNOWLEDGE / source["path"]).read_bytes(), source["type"])
                      for key, source in sources.items() if "path" in source}
            print(json.dumps(states, indent=2))
            return int(args.strict and not all(states.values()))
        if args.command == "build":
            index = build()
            if args.mode == "hybrid":
                HybridSearch(index, args.device, args.qdrant_url)
            result = {"chunks": len(index["records"]), "sources": index["source_status"],
                      "mode": args.mode}
        else:
            rag = EvidenceRAG(args.mode, args.device, args.qdrant_url)
            if args.command == "serve":
                serve(rag, args.port)
                return 0
            result = rag.retrieve(args.query, k=args.k, kind=args.kind)
    except (ImportError, OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

