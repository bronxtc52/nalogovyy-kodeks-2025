#!/usr/bin/env python3
"""Сборка локальной базы поиска tax.sqlite из data/chunks.jsonl.

Только стандартная библиотека: таблица чанков + полнотекстовый индекс FTS5.
Эмбеддинги (BGE-M3) сюда осознанно не входят — это артефакт версии 0.2.0,
и он публикуется в GitHub Releases, а не в git-истории.

Использование:
    python3 scripts/build_sqlite.py [--out dist/tax.sqlite]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    article TEXT NOT NULL,
    article_label TEXT NOT NULL,
    heading TEXT NOT NULL,
    point TEXT,
    anchor TEXT,
    path TEXT NOT NULL,
    razdel INTEGER,
    razdel_title TEXT,
    glava INTEGER,
    glava_title TEXT,
    paragraf INTEGER,
    context_header TEXT NOT NULL,
    text TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    effective_from TEXT,
    effective_to TEXT,
    effective_note TEXT,
    cross_references TEXT,
    file TEXT NOT NULL,
    source_url TEXT NOT NULL,
    approx_tokens INTEGER
);
CREATE INDEX chunks_article ON chunks(article);
CREATE INDEX chunks_razdel ON chunks(razdel);
CREATE INDEX chunks_effective ON chunks(effective_from);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    context_header, text, content='chunks', content_rowid='rowid', tokenize='unicode61'
);
"""


def main() -> int:
    out = os.path.join(REPO, "dist", "tax.sqlite")
    if "--out" in sys.argv:
        out = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)

    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))
    connection = sqlite3.connect(out)
    connection.executescript(DDL)

    rows = 0
    with open(os.path.join(REPO, "data", "chunks.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            connection.execute(
                "INSERT INTO chunks VALUES (:chunk_id,:parent_id,:doc_id,:lang,:article,"
                ":article_label,:heading,:point,:anchor,:path,:razdel,:razdel_title,:glava,"
                ":glava_title,:paragraf,:context_header,:text,:revision_id,:effective_from,"
                ":effective_to,:effective_note,:cross_references,:file,:source_url,:approx_tokens)",
                {**chunk, "cross_references": json.dumps(chunk["cross_references"], ensure_ascii=False)},
            )
            rows += 1
    connection.execute(
        "INSERT INTO chunks_fts(rowid, context_header, text) "
        "SELECT rowid, context_header, text FROM chunks"
    )
    for key, value in {
        "doc_id": manifest["doc_id"],
        "edition_id": manifest["edition_id"],
        "revision_id": manifest["revision_id"],
        "effective_from": manifest["effective_from"],
        "articles": str(manifest["counts"]["articles"]),
        "chunks": str(rows),
        "source_name": manifest["source"]["name"],
        "source_url": manifest["source"]["url"],
        "source_retrieved_at": manifest["source"]["retrieved_at"],
        "disclaimer": "Неофициальная машинно-читаемая копия. Официальный источник — ЭКБ НПА.",
    }.items():
        connection.execute("INSERT INTO meta VALUES (?,?)", (key, value))
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    print(f"{out}: {rows} чанков, {os.path.getsize(out) // 1024} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
