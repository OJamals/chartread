"""Chunk notes with the retrieval libraries' own splitters and record where each chunk sits.

Runs in its own environment, never the repository's: the splitters pull in dependencies the
clinical code must not acquire (``corpora/realnotes/.venv-chunkers``; versions pinned in
``docs/reference/chartread-addendum-2-realnotes.md``). Standard library and the splitters only,
so nothing here can drift from what a reader of the addendum would install.

    python scripts/chartread_chunk.py --notes notes.jsonl --out chunks.jsonl

``notes.jsonl`` rows: ``{"note", "form", "text"}``. Output rows: ``{"note", "form", "setting",
"chunks": [[start, end], ...], "unlocated": n}`` in ``text`` offsets.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Each chunk's ``[start, end)`` in the note, and how many chunks could not be located.
Spans = tuple[list[list[int]], int]


def _verified(text: str, pieces: list[str], starts: list[int]) -> Spans:
    """Offsets the splitter itself reported, kept only where the text there is the chunk."""

    spans: list[list[int]] = []
    missing = 0
    for piece, at in zip(pieces, starts, strict=True):
        if at is not None and at >= 0 and text[at : at + len(piece)] == piece:
            spans.append([at, at + len(piece)])
        else:
            missing += 1
    return spans, missing


def settings() -> dict[str, Callable[[str], Spans]]:
    # Imported by name: the repository's environment never has them, and must not need to.
    splitters: Any = importlib.import_module("langchain_text_splitters")
    llama: Any = importlib.import_module("llama_index.core")
    parsers: Any = importlib.import_module("llama_index.core.node_parser")
    tiktoken: Any = importlib.import_module("tiktoken")

    def recursive(size: int | None, overlap: int | None) -> Callable[[str], Spans]:
        # LangChain's own offsets: it searches for each chunk from where the previous one ended,
        # less the overlap, so a sentence the note repeats is not located at its first copy.
        options: dict[str, Any] = {"add_start_index": True}
        if size is not None:
            options |= {"chunk_size": size, "chunk_overlap": overlap}
        splitter = splitters.RecursiveCharacterTextSplitter(**options)

        def run(text: str) -> Spans:
            docs = splitter.create_documents([text])
            return _verified(
                text, [d.page_content for d in docs], [d.metadata["start_index"] for d in docs]
            )

        return run

    def token(size: int, overlap: int) -> Callable[[str], Spans]:
        # The library's chunks, located by decoding the token prefix each window starts at; the
        # windows are rebuilt with the library's own stride and must match its output exactly.
        splitter = splitters.TokenTextSplitter(
            encoding_name="cl100k_base", chunk_size=size, chunk_overlap=overlap
        )
        encoding = tiktoken.get_encoding("cl100k_base")

        def run(text: str) -> Spans:
            pieces = splitter.split_text(text)
            ids = encoding.encode(text)
            starts: list[int] = []
            index = 0
            while index < len(ids):
                starts.append(len(encoding.decode(ids[:index])))
                if index + size >= len(ids):
                    break
                index += size - overlap
            if len(starts) != len(pieces):
                return [], len(pieces)
            return _verified(text, pieces, starts)

        return run

    def sentence(size: int | None, overlap: int | None) -> Callable[[str], Spans]:
        options: dict[str, Any] = {}
        if size is not None:
            options = {"chunk_size": size, "chunk_overlap": overlap}
        splitter = parsers.SentenceSplitter(**options)

        def run(text: str) -> Spans:
            nodes = splitter.get_nodes_from_documents([llama.Document(text=text)])
            return _verified(
                text, [n.get_content() for n in nodes], [n.start_char_idx for n in nodes]
            )

        return run

    return {
        "langchain_recursive_default": recursive(None, None),
        "langchain_recursive_1000_200": recursive(1000, 200),
        "langchain_recursive_500_50": recursive(500, 50),
        "langchain_token_512_50": token(512, 50),
        "langchain_token_256_25": token(256, 25),
        "clear_token_490_stride_128": token(490, 362),
        "llamaindex_sentence_default": sentence(None, None),
        "llamaindex_sentence_512_50": sentence(512, 50),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runners = settings()
    rows = [json.loads(line) for line in args.notes.read_text(encoding="utf-8").splitlines()]
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            for name, run in runners.items():
                spans, missing = run(row["text"])
                record = {
                    "note": row["note"],
                    "form": row["form"],
                    "setting": name,
                    "chunks": spans,
                    "unlocated": missing,
                }
                handle.write(json.dumps(record) + "\n")
    print(f"{len(rows)} notes x {len(runners)} settings -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
