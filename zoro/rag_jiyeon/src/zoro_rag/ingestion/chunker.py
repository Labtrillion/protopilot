from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from zoro_rag.ingestion.metadata import document_id, identifiers, infer_metadata
from zoro_rag.schemas import Chunk, DocumentBlock


def token_count(text: str) -> int:
    return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))


class StructureAwareChunker:
    def __init__(self, target_tokens: int = 450, max_tokens: int = 650, overlap_tokens: int = 60):
        if not 0 <= overlap_tokens < max_tokens or not 0 < target_tokens <= max_tokens:
            raise ValueError("Require 0 <= overlap < max and 0 < target <= max")
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def _split_oversized(self, block: DocumentBlock) -> list[DocumentBlock]:
        heading_tokens = token_count(" > ".join(block.heading_path))
        budget = self.max_tokens - heading_tokens
        if budget < 1:
            raise ValueError(f"Heading exceeds chunk budget: {block.source_file}")
        if token_count(block.text) <= budget or block.block_type == "record":
            return [block]
        lines = block.text.splitlines()
        if block.block_type == "table" and len(lines) > 2:
            header, rows = lines[0], lines[1:]
            parts, current = [], [header]
            for row in rows:
                if token_count("\n".join(current + [row])) > budget and len(current) > 1:
                    parts.append("\n".join(current)); current = [header, row]
                else:
                    current.append(row)
            if len(current) > 1:
                parts.append("\n".join(current))
        else:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", block.text)
            parts, current = [], []
            for sentence in sentences:
                if current and token_count(" ".join(current + [sentence])) > budget:
                    parts.append(" ".join(current)); current = [sentence]
                else:
                    current.append(sentence)
            if current:
                parts.append(" ".join(current))
        bounded = []
        for part in parts:
            if token_count(part) <= budget or block.block_type == "table":
                bounded.append(part)
                continue
            tokens = list(re.finditer(r"\w+|[^\w\s]", part, re.UNICODE))
            step = budget - min(self.overlap_tokens, budget - 1)
            for start in range(0, len(tokens), step):
                end = min(start + budget, len(tokens))
                bounded.append(part[tokens[start].start():tokens[end - 1].end()])
                if end == len(tokens):
                    break
        return [block.model_copy(update={"text": part}) for part in bounded if part.strip()]

    def chunk(self, blocks: list[DocumentBlock]) -> list[Chunk]:
        expanded = [item for block in blocks for item in self._split_oversized(block)]
        groups: list[list[DocumentBlock]] = []
        current: list[DocumentBlock] = []
        for block in expanded:
            atomic = block.block_type in {"table", "record"}
            boundary = current and (block.source_file != current[-1].source_file or block.heading_path != current[-1].heading_path or block.page != current[-1].page)
            too_large = current and token_count("\n\n".join(x.text for x in current + [block])) > min(self.target_tokens, self.max_tokens - token_count(" > ".join(block.heading_path)))
            if boundary or too_large or (atomic and current):
                groups.append(current); current = []
            current.append(block)
            if atomic:
                groups.append(current); current = []
        if current:
            groups.append(current)

        # Fold tiny extraction artifacts (often isolated PDF headers/page numbers)
        # into a neighboring group from the same source instead of indexing noise.
        compacted: list[list[DocumentBlock]] = []
        index = 0
        while index < len(groups):
            group = groups[index]
            size = token_count("\n\n".join(item.text for item in group))
            next_size = token_count("\n\n".join(item.text for item in groups[index + 1])) if index + 1 < len(groups) else self.max_tokens
            previous_size = token_count("\n\n".join(item.text for item in compacted[-1])) if compacted else self.max_tokens
            def same_context(other):
                return (other[0].source_file, other[0].heading_path, other[0].page) == (group[0].source_file, group[0].heading_path, group[0].page)
            if size < 20 and index + 1 < len(groups) and same_context(groups[index + 1]) and size + next_size + token_count(" > ".join(group[0].heading_path)) <= self.max_tokens:
                groups[index + 1] = group + groups[index + 1]
            elif size < 20 and compacted and same_context(compacted[-1]) and size + previous_size + token_count(" > ".join(group[0].heading_path)) <= self.max_tokens:
                compacted[-1].extend(group)
            else:
                compacted.append(group)
            index += 1
        groups = compacted

        chunks: list[Chunk] = []
        for group in groups:
            text = "\n\n".join(block.text for block in group).strip()
            first = group[0]
            # Heading context is part of the searchable passage (not a fabricated fact).
            heading = " > ".join(first.heading_path)
            if heading and not text.startswith(heading):
                text = heading + "\n\n" + text
            identity = first.source_file + "\n" + str(first.page) + "\n" + text
            if first.metadata.get("procedure"):
                identity += "\n" + json.dumps(first.metadata["procedure"], ensure_ascii=False, sort_keys=True)
            stable = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            idvs, variables = identifiers(text)
            equipment, evidence_types, inferred = infer_metadata(text, first.source_file)
            chunks.append(Chunk(
                chunk_id=stable,
                document_id=document_id(first.source_file),
                document_title=Path(first.source_file).stem,
                source_file=first.source_file,
                source_type=first.metadata.get("source_type", "original_technical_reference"),
                document_version=first.metadata.get("document_version"),
                procedure=first.metadata.get("procedure", {}),
                section=" > ".join(first.heading_path) or None,
                page=first.page,
                source_url=first.metadata.get("source_url"),
                disturbance_ids=idvs,
                variable_ids=variables,
                equipment=equipment,
                evidence_types=evidence_types,
                inferred_metadata=inferred,
                parsing_warnings=sorted({warning for block in group for warning in block.metadata.get("parsing_warnings", [])}),
                token_count=token_count(text),
                text=text,
            ))
        return chunks
