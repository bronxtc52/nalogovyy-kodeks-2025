#!/usr/bin/env python3
"""Сборка data/chunks.jsonl из Markdown-корпуса.

Разбиение — по юридической структуре (пункт статьи), а не по числу символов.
К каждому чанку добавляется контекстный заголовок (паттерн contextual retrieval):
короткий префикс о месте фрагмента в кодексе заметно поднимает recall.

Использование:
    python3 scripts/build_chunks.py [--out data/chunks.jsonl]
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmlib import load_frontmatter, sha256  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHOR_RE = re.compile(r'^<a id="p-([^"]+)"></a>')
MAX_CHARS = 6000  # предохранитель: сверхдлинный пункт режем по абзацам
CHARS_PER_TOKEN = 3.2  # эмпирическая оценка для русского текста; поле названо approx_*


def approx_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def article_points(body: str) -> list[dict]:
    """Восстанавливает пункты из тела статьи по anchor-меткам."""
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("# ")), 0)
    content = []
    for line in lines[start + 1:]:
        if line.strip() == "---":
            break
        if line.startswith(">"):
            continue
        content.append(line)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(content)) if p.strip()]

    points: list[dict] = []
    for paragraph in paragraphs:
        match = ANCHOR_RE.match(paragraph)
        if match:
            points.append({"point": match.group(1), "paragraphs": [ANCHOR_RE.sub("", paragraph).strip()]})
        elif points:
            points[-1]["paragraphs"].append(paragraph)
        else:
            points.append({"point": None, "paragraphs": [paragraph]})
    return points


def split_long(paragraphs: list[str]) -> list[list[str]]:
    groups: list[list[str]] = [[]]
    size = 0
    for paragraph in paragraphs:
        if size and size + len(paragraph) > MAX_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(paragraph)
        size += len(paragraph)
    return [g for g in groups if g]


def main() -> int:
    out_path = os.path.join(REPO, "data", "chunks.jsonl")
    if "--out" in sys.argv:
        out_path = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))

    chunks: list[dict] = []
    for record in manifest["files"]:
        path = os.path.join(REPO, record["file"])
        fields, body = load_frontmatter(open(path, encoding="utf-8").read())
        points = article_points(body)
        parent_id = fields["article_uid"]
        labels = [p["point"] for p in points]

        for point in points:
            groups = split_long(point["paragraphs"])
            for index, group in enumerate(groups, start=1):
                text = "\n\n".join(group)
                if not text:
                    continue
                if point["point"]:
                    suffix = f"p-{point['point']}" + (f"--{index}" if len(groups) > 1 else "")
                    label = f"пункт {point['point']}"
                    anchor = f"p-{point['point']}"
                else:
                    suffix = "full" + (f"--{index}" if len(groups) > 1 else "")
                    label = "текст статьи"
                    anchor = None
                header_bits = [f"{fields['article_label']} НК РК ({fields['heading']})"]
                if fields.get("razdel"):
                    header_bits.append(
                        f"раздел {fields['razdel']}"
                        + (f" «{fields['razdel_title']}»" if fields.get("razdel_title") else "")
                    )
                if fields.get("glava"):
                    header_bits.append(f"глава {fields['glava']}")
                header_bits.append(f"{label}, редакция с {fields['effective_from']}")
                chunks.append({
                    "chunk_id": f"{parent_id}/{suffix}",
                    "doc_id": fields["doc_id"],
                    "edition_id": fields["edition_id"],
                    "lang": fields["lang"],
                    "article": fields["article"],
                    "article_label": fields["article_label"],
                    "heading": fields["heading"],
                    "point": point["point"],
                    "anchor": anchor,
                    "path": fields["path"],
                    "razdel": fields["razdel"],
                    "razdel_title": fields["razdel_title"],
                    "glava": fields["glava"],
                    "glava_title": fields["glava_title"],
                    "paragraf": fields["paragraf"],
                    "context_header": ", ".join(header_bits),
                    "text": text,
                    "revision_id": fields["revision_id"],
                    "effective_from": fields["effective_from"],
                    "effective_to": fields["effective_to"],
                    "effective_note": fields.get("effective_note"),
                    "parent_id": parent_id,
                    "siblings": [f"{parent_id}/p-{p}" for p in labels if p and p != point["point"]],
                    "cross_references": fields["cross_references"],
                    "file": record["file"],
                    "source_url": fields["source_url"],
                    "source_updated_at": fields["source_updated_at"],
                    "text_sha256": sha256(text),
                    "approx_tokens": approx_tokens(text),
                })

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    sizes = sorted(c["approx_tokens"] for c in chunks)
    stats = {
        "chunks": len(chunks),
        "articles": len({c["parent_id"] for c in chunks}),
        "approx_tokens_median": sizes[len(sizes) // 2],
        "approx_tokens_p95": sizes[int(len(sizes) * 0.95)],
        "approx_tokens_max": sizes[-1],
    }
    with open(os.path.join(REPO, "data", "chunks.stats.json"), "w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
