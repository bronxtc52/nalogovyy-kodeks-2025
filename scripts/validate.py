#!/usr/bin/env python3
"""Проверки целостности корпуса. Ненулевой код возврата = корпус нельзя публиковать.

Гоняется в CI на каждый PR (.github/workflows/validate.yml) и вручную:
    python3 scripts/validate.py
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmlib import load_frontmatter, sha256  # noqa: E402
from build_chunks import article_points  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = [
    "doc_id", "edition_id", "article_uid", "article", "article_label", "heading", "lang",
    "part", "path", "status", "revision_id", "effective_from", "effective_to",
    "point_count", "paragraph_count", "cross_references", "source_name", "source_url",
    "source_article_id", "source_updated_at", "retrieved_at", "source_text_sha256",
    "text_sha256", "tags",
]

problems: list[str] = []
checks: list[tuple[str, str, str]] = []


def check(code: str, ok: bool, message: str) -> None:
    checks.append((code, "PASS" if ok else "FAIL", message))
    if not ok:
        problems.append(f"{code}: {message}")


def main() -> int:
    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))
    records = manifest["files"]
    on_disk = sorted(
        os.path.relpath(os.path.join(root, name), REPO)
        for root, _, names in os.walk(os.path.join(REPO, "code"))
        for name in names
        if name.startswith("st-") and name.endswith(".md")
    )

    check("V-01", len(records) == manifest["counts"]["articles"] == len(on_disk),
          f"манифест {len(records)}, файлов на диске {len(on_disk)}, счётчик {manifest['counts']['articles']}")
    check("V-02", sorted(r["file"] for r in records) == on_disk,
          "список файлов манифеста совпадает с деревом code/")

    numbers: dict[str, int] = {}
    uids: set[str] = set()
    fields_by_number: dict[str, dict] = {}
    hash_bad, field_bad, link_bad, encoding_bad, file_hash_bad = [], [], [], [], []

    for record in records:
        path = os.path.join(REPO, record["file"])
        raw = open(path, encoding="utf-8").read()
        if sha256(raw) != record["file_sha256"]:
            file_hash_bad.append(record["file"])
        if "\r" in raw or "�" in raw:
            encoding_bad.append(record["file"])
        fields, body = load_frontmatter(raw)
        missing = [key for key in REQUIRED if key not in fields]
        if missing:
            field_bad.append(f"{record['file']}: нет {missing}")
        numbers[fields["article"]] = numbers.get(fields["article"], 0) + 1
        uids.add(fields["article_uid"])
        fields_by_number.setdefault(fields["article"], fields)

        points = article_points(body)
        plain = "\n\n".join(p for point in points for p in point["paragraphs"])
        if sha256(plain) != fields["text_sha256"]:
            hash_bad.append(record["file"])

        directory = os.path.dirname(path)
        for target in (
            os.path.join(directory, "_glava.md"),
            os.path.join(directory, os.pardir, "_razdel.md"),
            os.path.join(REPO, "index.md"),
        ):
            if not os.path.exists(target):
                link_bad.append(f"{record['file']} → {os.path.relpath(target, REPO)}")

    check("V-03", not field_bad, f"обязательные поля frontmatter ({len(field_bad)} нарушений: {field_bad[:3]})")
    check("V-04", not hash_bad, f"text_sha256 совпадает с телом статьи ({len(hash_bad)} расхождений: {hash_bad[:3]})")
    check("V-05", not file_hash_bad, f"file_sha256 манифеста совпадает с файлом ({len(file_hash_bad)}: {file_hash_bad[:3]})")
    check("V-06", not encoding_bad, f"UTF-8 без CRLF и символов замены ({len(encoding_bad)}: {encoding_bad[:3]})")
    check("V-07", not link_bad, f"локальные ссылки статей разрешаются ({len(link_bad)}: {link_bad[:3]})")
    check("V-08", len(uids) == len(records), f"article_uid уникальны ({len(uids)} из {len(records)})")

    duplicates = sorted(n for n, c in numbers.items() if c > 1)
    check("V-09", duplicates == manifest["counts"]["duplicate_article_numbers"],
          f"дубликаты номеров совпадают с манифестом: {duplicates}")
    expected = {str(n) for n in range(1, 849)}
    check("V-10", set(numbers) == expected,
          f"номера статей 1–848 без пропусков (лишние: {sorted(set(numbers) - expected)[:5]}, "
          f"пропущенные: {sorted(expected - set(numbers))[:5]})")

    bad_refs = [
        f"{number}→{ref}" for number, fields in fields_by_number.items()
        for ref in (fields["cross_references"] or []) if ref not in numbers
    ]
    check("V-11", not bad_refs, f"перекрёстные ссылки указывают на существующие статьи ({len(bad_refs)}: {bad_refs[:5]})")

    index = open(os.path.join(REPO, "index.md"), encoding="utf-8").read()
    index_links = re.findall(r"\]\((code/[^)]+\.md)\)", index)
    missing_in_index = sorted(set(r["file"] for r in records) - set(index_links))
    dangling = [link for link in index_links if not os.path.exists(os.path.join(REPO, link))]
    check("V-12", not missing_in_index and not dangling,
          f"index.md ссылается на все статьи и не имеет битых ссылок "
          f"(нет в индексе: {len(missing_in_index)}, битых: {len(dangling)})")

    chunks_path = os.path.join(REPO, "data", "chunks.jsonl")
    chunk_ids: set[str] = set()
    chunk_problems: list[str] = []
    parents: set[str] = set()
    empty_articles = set(manifest["counts"]["empty_source_bodies"])
    with open(chunks_path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            chunk = json.loads(line)
            if chunk["chunk_id"] in chunk_ids:
                chunk_problems.append(f"дубль chunk_id в строке {line_number}: {chunk['chunk_id']}")
            chunk_ids.add(chunk["chunk_id"])
            parents.add(chunk["parent_id"])
            if not chunk["text"].strip():
                chunk_problems.append(f"пустой текст в строке {line_number}")
            if sha256(chunk["text"]) != chunk["text_sha256"]:
                chunk_problems.append(f"text_sha256 чанка не совпадает, строка {line_number}")
            if not chunk["effective_from"]:
                chunk_problems.append(f"нет effective_from, строка {line_number}")
            if not os.path.exists(os.path.join(REPO, chunk["file"])):
                chunk_problems.append(f"чанк ссылается на несуществующий файл, строка {line_number}")
    check("V-13", not chunk_problems, f"чанки консистентны ({len(chunk_problems)}: {chunk_problems[:3]})")
    expected_parents = {
        r["article_uid"] for r in records
        if not (r["article"] in empty_articles and r["status"] == "source_empty")
    }
    check("V-14", parents == expected_parents,
          f"каждая непустая статья покрыта чанками (нет чанков у {len(expected_parents - parents)}, "
          f"лишних родителей {len(parents - expected_parents)})")

    eval_path = os.path.join(REPO, "eval", "qa-ru.jsonl")
    if os.path.exists(eval_path):
        eval_bad = []
        with open(eval_path, encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                item = json.loads(line)
                for article in item["expected_articles"]:
                    if article not in numbers:
                        eval_bad.append(f"строка {line_number}: статья {article} не существует")
        check("V-15", not eval_bad, f"эталонные вопросы ссылаются на существующие статьи ({eval_bad[:3]})")

    width = max(len(message) for _, _, message in checks)
    print(f"Проверка корпуса {manifest['doc_id']} ({manifest['revision_id']})")
    for code, status, message in checks:
        print(f"  [{status}] {code} {message:<{width}}")
    print(f"Итог: {'PASS' if not problems else 'FAIL'} — статей {len(records)}, чанков {len(chunk_ids)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
