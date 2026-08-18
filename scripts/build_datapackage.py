#!/usr/bin/env python3
"""Генерация datapackage.json (Frictionless Data Package) для дата-инструментов.

Описывает корпус как набор ресурсов: чанки, манифест, оглавление, эталонные вопросы.

Использование:
    python3 scripts/build_datapackage.py
"""

from __future__ import annotations

import hashlib
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHUNK_FIELDS = [
    ("chunk_id", "string", "идентификатор чанка, включает статью и пункт"),
    ("parent_id", "string", "идентификатор статьи"),
    ("doc_id", "string", "идентификатор документа"),
    ("lang", "string", "язык текста"),
    ("article", "string", "номер статьи"),
    ("heading", "string", "заголовок статьи"),
    ("point", "string", "номер пункта, null для статей без нумерации"),
    ("anchor", "string", "якорь в Markdown-файле"),
    ("path", "string", "путь в структуре кодекса"),
    ("razdel", "integer", "номер раздела"),
    ("glava", "integer", "номер главы"),
    ("paragraf", "integer", "номер параграфа"),
    ("context_header", "string", "контекстный префикс для индексации"),
    ("text", "string", "текст пункта"),
    ("revision_id", "string", "редакция корпуса"),
    ("effective_from", "date", "дата введения в действие"),
    ("effective_to", "date", "дата утраты силы"),
    ("cross_references", "array", "номера статей, на которые ссылается статья"),
    ("file", "string", "путь к Markdown-файлу статьи"),
    ("source_url", "string", "ссылка на статью в источнике"),
    ("text_sha256", "string", "хэш текста чанка"),
    ("approx_tokens", "integer", "оценка длины в токенах (символы ÷ 3.2)"),
]


def digest(path: str) -> dict:
    data = open(os.path.join(REPO, path), "rb").read()
    return {"bytes": len(data), "hash": "sha256:" + hashlib.sha256(data).hexdigest()}


def main() -> int:
    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))
    package = {
        "profile": "data-package",
        "name": "kz-tax-code-2025",
        "title": "Налоговый кодекс Республики Казахстан — машинно-читаемый корпус",
        "description": (
            f"Полный текст НК РК в редакции {manifest['revision_id']}: "
            f"{manifest['counts']['articles']} статей в Markdown с frontmatter и чанки по пунктам "
            "для RAG. Неофициальная копия, см. DISCLAIMER.md."
        ),
        "version": "0.1.0",
        "created": manifest["source"]["retrieved_at"],
        "keywords": ["kazakhstan", "tax", "legal", "rag", "llm", "markdown", "налоговый кодекс"],
        "licenses": [
            {"name": "CC0-1.0", "title": "CC0 1.0 — разметка и метаданные",
             "path": "https://creativecommons.org/publicdomain/zero/1.0/"},
            {"name": "MIT", "title": "MIT — код в scripts/", "path": "https://opensource.org/licenses/MIT"},
        ],
        "sources": [{
            "title": manifest["source"]["name"],
            "path": manifest["source"]["url"],
            "note": "Текст нормативного акта не охраняется авторским правом (ст. 8 Закона РК "
                    "«Об авторском праве и смежных правах»); оформление и комментарии портала не воспроизводятся.",
        }],
        "resources": [
            {
                "name": "chunks", "path": "data/chunks.jsonl", "format": "ndjson",
                "mediatype": "application/x-ndjson", "encoding": "utf-8",
                "description": "Чанки по пунктам статей с контекстными заголовками.",
                **digest("data/chunks.jsonl"),
                "schema": {"fields": [
                    {"name": name, "type": kind, "description": note}
                    for name, kind, note in CHUNK_FIELDS
                ]},
            },
            {
                "name": "manifest", "path": "manifest.json", "format": "json",
                "mediatype": "application/json", "encoding": "utf-8",
                "description": "Редакция, счётчики, хэши файлов, аномалии источника.",
                **digest("manifest.json"),
            },
            {
                "name": "index", "path": "index.md", "format": "md",
                "mediatype": "text/markdown", "encoding": "utf-8",
                "description": "Оглавление со ссылками на все статьи.",
                **digest("index.md"),
            },
            {
                "name": "eval-qa-ru", "path": "eval/qa-ru.jsonl", "format": "ndjson",
                "mediatype": "application/x-ndjson", "encoding": "utf-8",
                "description": "Эталонные вопросы «вопрос → ожидаемые статьи».",
                **digest("eval/qa-ru.jsonl"),
            },
        ],
    }
    with open(os.path.join(REPO, "datapackage.json"), "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("datapackage.json готов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
