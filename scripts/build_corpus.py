#!/usr/bin/env python3
"""Сборка Markdown-корпуса НК РК из выгрузки Учет.kz.

Вход  — каталог выгрузки (articles/*.md, manifest.json, audit/api-toc.json).
Выход — code/ru/**/st-NNN.md, index.md, manifest.json, provenance/*.

Markdown в code/ru — источник истины репозитория; всё остальное (chunks, sqlite)
производится из него отдельными скриптами.

Использование:
    python3 scripts/build_corpus.py [путь-к-выгрузке]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmlib import dump_frontmatter, sha256, sha256_bytes, slugify  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXPORT = os.path.join(
    os.path.dirname(REPO), "export-uchet-nalogovyy-kodeks-2025"
)

DOC_ID = "kz-tax-code-2025"
EDITION_ID = "nalogovyy-kodeks-2025"
LANG = "ru"
REVISION_ID = "2026-01-01"
CODE_EFFECTIVE_FROM = "2026-01-01"

# Даты введения в действие — из статьи 848 «Порядок введения в действие настоящего Кодекса».
# Кодекс вводится с 01.01.2026, кроме перечисленных исключений.
EFFECTIVE_OVERRIDES = {
    "189": ("2026-07-01", "Статья 848, пункт 1, подпункт 1): вводится в действие с 1 июля 2026 года."),
    "92": ("2027-01-01", "Статья 848, пункт 1, подпункт 2): вводится в действие с 1 января 2027 года."),
}
GLAVA_EFFECTIVE_OVERRIDES = {
    90: ("2027-01-01", "Статья 848, пункт 1, подпункт 2): глава 90 вводится в действие с 1 января 2027 года."),
}
PARTIAL_EFFECTIVE_NOTES = {
    "391": "Статья 848, пункт 1, подпункт 3): пункты 2 и 3 вводятся в действие с 1 января 2028 года.",
    "351": "Статья 848, пункт 1, подпункт 4): подпункт 1) пункта 3 вводится в действие с 1 января 2030 года.",
}

PART_ORDER = {"Общая часть": 1, "Особенная часть": 2}
POINT_RE = re.compile(r"^(\d{1,3})\.\s+")
EMPTY_BODY_MARKER = "[Источник вернул пустое тело статьи.]"
HEAD_NUM_RE = re.compile(r"^(?:Раздел|Глава|Параграф)\s+(\d+)\.?\s*(.*)$", re.S)
ARTICLE_REF_RE = re.compile(
    r"стат(?:ь[а-яё]{1,3}|ей)\s+((?:\d+(?:\s*(?:,|и)\s*)?)+)", re.I
)


def heading_kind(text: str) -> str:
    if text.endswith("часть"):
        return "part"
    if text.startswith("Раздел"):
        return "razdel"
    if text.startswith("Глава"):
        return "glava"
    if text.startswith("Параграф"):
        return "paragraf"
    return "note"


def split_heading(text: str) -> tuple[int | None, str]:
    match = HEAD_NUM_RE.match(text)
    if not match:
        return None, text
    return int(match.group(1)), match.group(2).strip()


def hierarchy(entries: list[dict]) -> list[dict]:
    """Раскладывает плоский section_context каждой статьи в часть/раздел/главу/параграф.

    В выгрузке у 88 статей на месте заголовка раздела стоит пояснительная сноска
    (раздел 6, индивидуальный подоходный налог) — номер восстанавливаем по позиции
    между разделами 5 и 7, название помечаем как отсутствующее в источнике.
    """
    result = []
    last_razdel_number = 0
    for entry in entries:
        node = {
            "part": None, "razdel": None, "razdel_title": None, "razdel_note": None,
            "razdel_title_missing": False, "glava": None, "glava_title": None,
            "paragraf": None, "paragraf_title": None,
        }
        for head in entry["section_context"]:
            kind = heading_kind(head)
            if kind == "part":
                node["part"] = head
            elif kind == "razdel":
                number, title = split_heading(head)
                node["razdel"], node["razdel_title"] = number, title
                last_razdel_number = number or last_razdel_number
            elif kind == "glava":
                number, title = split_heading(head)
                node["glava"], node["glava_title"] = number, title
            elif kind == "paragraf":
                number, title = split_heading(head)
                node["paragraf"], node["paragraf_title"] = number, title
            else:
                node["razdel_note"] = head
                node["razdel"] = last_razdel_number + 1
                node["razdel_title_missing"] = True
        result.append(node)
    return result


def normalize_body(raw: str) -> list[str]:
    """Абзацы источника: снимаем табуляцию и мягкие переносы, схлопываем пробелы."""
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]
    paragraphs = []
    for chunk in chunks:
        text = re.sub(r"[ \t ]+", " ", chunk.replace("\n", " ")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def split_points(paragraphs: list[str]) -> list[dict]:
    """Структурное разбиение статьи на пункты (нумерация «N.» в начале абзаца).

    Абзацы без номера принадлежат текущему пункту; текст до первого пункта — преамбула.
    Номер принимается только если он продолжает последовательность, иначе «1.» внутри
    цитаты или перечисления рвало бы статью на части.
    """
    points: list[dict] = []
    expected: int | None = None
    for paragraph in paragraphs:
        match = POINT_RE.match(paragraph)
        number = int(match.group(1)) if match else None
        starts = number is not None and (
            (expected is None and number <= 3) or number == expected
        )
        if starts:
            points.append({"point": str(number), "paragraphs": [paragraph]})
            expected = number + 1
        elif points:
            points[-1]["paragraphs"].append(paragraph)
        else:
            if not points:
                points.append({"point": None, "paragraphs": [paragraph]})
            else:
                points[-1]["paragraphs"].append(paragraph)
    return points


def cross_references(text: str, self_number: str, known: set[str]) -> list[str]:
    found: set[str] = set()
    for group in ARTICLE_REF_RE.findall(text):
        for number in re.findall(r"\d+", group):
            if number in known and number != self_number:
                found.add(number)
    return sorted(found, key=int)


def read_export_article(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    head = text.split("---\n", 2)[1]

    def field(name: str) -> str | None:
        match = re.search(rf'^{name}: "(.*)"$', head, re.M)
        return match.group(1) if match else None

    body = text.split("<!-- EXPORT:SOURCE:BEGIN -->", 1)[1]
    body = body.split("<!-- EXPORT:SOURCE:END -->", 1)[0]
    return {
        "source_article_id": field("source_article_id"),
        "article_number": field("article_number"),
        "article_title": field("article_title"),
        "source_updated_at": field("source_updated_at"),
        "source_updated_at_raw": field("source_updated_at_raw"),
        "retrieved_at": field("retrieved_at"),
        "source_text_sha256": field("source_text_sha256"),
        "source_html_sha256": field("source_html_sha256"),
        "source_url": field("source_url"),
        "raw_body": body,
    }


def main() -> int:
    export = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXPORT)
    if not os.path.isdir(export):
        print(f"нет каталога выгрузки: {export}", file=sys.stderr)
        return 2

    src_manifest = json.load(open(os.path.join(export, "manifest.json"), encoding="utf-8"))
    toc = json.load(open(os.path.join(export, "audit", "api-toc.json"), encoding="utf-8"))
    entries = toc["entries"]
    by_id = {f["source_article_id"]: f for f in src_manifest["files"]}
    title_warnings = {
        str(w.get("source_article_id")): w
        for w in src_manifest["anomalies"].get("api_title_warnings", [])
    }
    nodes = hierarchy(entries)
    known_numbers = {e["number"] for e in entries}

    code_root = os.path.join(REPO, "code", LANG)
    if os.path.isdir(code_root):
        shutil.rmtree(code_root)

    records: list[dict] = []
    rank = Counter()
    for entry, node in zip(entries, nodes):
        file_entry = by_id[entry["source_article_id"]]
        source = read_export_article(os.path.join(export, file_entry["file"]))
        paragraphs = normalize_body(source["raw_body"])
        if paragraphs == [EMPTY_BODY_MARKER]:
            # выгрузка ставит эту заглушку там, где API источника отдал пустое тело
            paragraphs = []
        points = split_points(paragraphs)
        plain = "\n\n".join(paragraphs)
        number = entry["number"]
        rank[number] += 1

        part = node["part"] or "Общая часть"
        part_slug = f"{PART_ORDER.get(part, 9):02d}-{slugify(part, 40)}"
        if node["razdel"] is None:
            razdel_slug = "razdel-00-vne-razdelov"
        elif node["razdel_title_missing"]:
            razdel_slug = f"razdel-{node['razdel']:02d}-bez-nazvaniya-v-istochnike"
        else:
            razdel_slug = f"razdel-{node['razdel']:02d}-{slugify(node['razdel_title'], 50)}"
        glava_slug = (
            f"glava-{node['glava']:02d}-{slugify(node['glava_title'], 50)}"
            if node["glava"] is not None else "glava-00-vne-glav"
        )
        directory = os.path.join(code_root, part_slug, razdel_slug, glava_slug)
        name = f"st-{int(number):03d}.md" if rank[number] == 1 else f"st-{int(number):03d}--{rank[number]}.md"
        relative = os.path.relpath(os.path.join(directory, name), REPO)

        effective_from, effective_note = CODE_EFFECTIVE_FROM, None
        if number in EFFECTIVE_OVERRIDES:
            effective_from, effective_note = EFFECTIVE_OVERRIDES[number]
        elif node["glava"] in GLAVA_EFFECTIVE_OVERRIDES:
            effective_from, effective_note = GLAVA_EFFECTIVE_OVERRIDES[node["glava"]]
        elif number in PARTIAL_EFFECTIVE_NOTES:
            effective_note = PARTIAL_EFFECTIVE_NOTES[number]

        path_parts = [part]
        if node["razdel"] is not None:
            path_parts.append(
                f"Раздел {node['razdel']}"
                + (f". {node['razdel_title']}" if node["razdel_title"] else "")
            )
        if node["glava"] is not None:
            path_parts.append(f"Глава {node['glava']}. {node['glava_title']}")
        if node["paragraf"] is not None:
            path_parts.append(f"Параграф {node['paragraf']}. {node['paragraf_title']}")
        path_parts.append(f"Статья {number}")

        uid = f"{DOC_ID}/{LANG}/st-{int(number):03d}" + ("" if rank[number] == 1 else f"-{rank[number]}")
        fields = {
            "doc_id": DOC_ID,
            "edition_id": EDITION_ID,
            "article_uid": uid,
            "article": number,
            "article_label": f"Статья {number}",
            "heading": entry["title"],
            "lang": LANG,
            "part": part,
            "razdel": node["razdel"],
            "razdel_title": node["razdel_title"],
            "glava": node["glava"],
            "glava_title": node["glava_title"],
            "paragraf": node["paragraf"],
            "paragraf_title": node["paragraf_title"],
            "path": " > ".join(path_parts),
            "status": "active",
            "revision_id": REVISION_ID,
            "effective_from": effective_from,
            "effective_to": None,
            "point_count": sum(1 for p in points if p["point"]),
            "paragraph_count": len(paragraphs),
            "cross_references": cross_references(plain, number, known_numbers),
            "source_name": "Учет.kz",
            "source_url": source["source_url"],
            "source_article_id": entry["source_article_id"],
            "source_updated_at": source["source_updated_at"],
            "retrieved_at": source["retrieved_at"],
            "source_text_sha256": source["source_text_sha256"],
            "text_sha256": sha256(plain),
            "tags": [DOC_ID, f"{DOC_ID}/razdel-{node['razdel'] or 0}", f"{DOC_ID}/article"],
        }
        if effective_note:
            fields["effective_note"] = effective_note
        if node["razdel_title_missing"]:
            fields["razdel_title_missing_in_source"] = True
            fields["razdel_note"] = node["razdel_note"]
        if rank[number] > 1 or number == "419":
            fields["duplicate_number_rank"] = rank[number]
        if not plain:
            fields["source_body_empty"] = True
            fields["status"] = "source_empty"
        if entry["source_article_id"] in title_warnings:
            fields["source_title_mismatch"] = True
            fields["source_api_title"] = title_warnings[entry["source_article_id"]].get("api_title")

        lines = [dump_frontmatter(fields), "", f"# Статья {number}. {entry['title']}", ""]
        lines.append(f"> [!info] {' > '.join(path_parts[:-1])}")
        lines.append(f"> Источник: [Учет.kz]({source['source_url']}) · обновлено {source['source_updated_at_raw']} · редакция {REVISION_ID}")
        if effective_note:
            lines.append(f"> Особый порядок введения в действие: {effective_note}")
        lines.append("")
        if not plain:
            lines += [
                "> [!warning] Пустое тело в источнике",
                "> Источник отдаёт эту статью с пустым текстом (см. anomalies в manifest.json).",
                "",
            ]
        for point in points:
            for position, paragraph in enumerate(point["paragraphs"]):
                lines.append("")
                if position == 0 and point["point"]:
                    lines.append(f'<a id="p-{point["point"]}"></a>{paragraph}')
                else:
                    lines.append(paragraph)
            lines.append("")
        lines.append("---")
        depth = relative.count(os.sep)
        lines.append(f"[Содержание]({'../' * depth}index.md) · "
                     f"[Глава](_glava.md) · [Раздел](../_razdel.md)")
        lines.append("")
        content = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))

        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(REPO, relative), "w", encoding="utf-8") as handle:
            handle.write(content)

        records.append({
            "file": relative,
            "article_uid": uid,
            "article": number,
            "heading": entry["title"],
            "part": part,
            "razdel": node["razdel"],
            "razdel_title": node["razdel_title"],
            "glava": node["glava"],
            "glava_title": node["glava_title"],
            "paragraf": node["paragraf"],
            "paragraf_title": node["paragraf_title"],
            "point_count": fields["point_count"],
            "source_article_id": entry["source_article_id"],
            "source_url": source["source_url"],
            "source_text_sha256": source["source_text_sha256"],
            "text_sha256": fields["text_sha256"],
            "file_sha256": sha256(content),
            "bytes": len(content.encode("utf-8")),
            "effective_from": effective_from,
            "duplicate_number_rank": rank[number],
            "status": fields["status"],
        })

    write_level_files(records)
    write_index(records)
    write_manifest(records, src_manifest, toc, export)
    copy_provenance(export)
    print(f"написано статей: {len(records)}")
    return 0


def write_level_files(records: list[dict]) -> None:
    """_razdel.md и _glava.md — метаданные уровня и локальное содержание."""
    razdels: dict[str, list[dict]] = {}
    glavas: dict[str, list[dict]] = {}
    for record in records:
        razdel_dir = os.path.dirname(os.path.dirname(record["file"]))
        glava_dir = os.path.dirname(record["file"])
        razdels.setdefault(razdel_dir, []).append(record)
        glavas.setdefault(glava_dir, []).append(record)

    for directory, items in razdels.items():
        first = items[0]
        title = first["razdel_title"] or "название отсутствует в источнике"
        fields = {
            "doc_id": DOC_ID, "edition_id": EDITION_ID, "kind": "razdel", "lang": LANG,
            "part": first["part"], "razdel": first["razdel"], "razdel_title": first["razdel_title"],
            "article_count": len(items),
            "glava_numbers": sorted({str(i["glava"]) for i in items if i["glava"]}, key=int),
        }
        label = f"Раздел {first['razdel']}" if first["razdel"] else "Вне разделов"
        body = [dump_frontmatter(fields), "", f"# {label}. {title}", "", f"Статей: {len(items)}.", ""]
        for glava_number in fields["glava_numbers"]:
            sample = next(i for i in items if str(i["glava"]) == glava_number)
            link = os.path.relpath(os.path.dirname(sample["file"]), directory)
            body.append(f"- Глава {glava_number}. {sample['glava_title']} — [[{link}/_glava|содержание]]")
        body.append("")
        open(os.path.join(REPO, directory, "_razdel.md"), "w", encoding="utf-8").write("\n".join(body))

    for directory, items in glavas.items():
        first = items[0]
        fields = {
            "doc_id": DOC_ID, "edition_id": EDITION_ID, "kind": "glava", "lang": LANG,
            "part": first["part"], "razdel": first["razdel"], "glava": first["glava"],
            "glava_title": first["glava_title"], "article_count": len(items),
            "article_numbers": [i["article"] for i in items],
        }
        label = f"Глава {first['glava']}. {first['glava_title']}" if first["glava"] else "Вне глав"
        body = [dump_frontmatter(fields), "", f"# {label}", ""]
        current = object()
        for item in items:
            if item["paragraf"] != current:
                current = item["paragraf"]
                if current is not None:
                    body += ["", f"## Параграф {current}. {item['paragraf_title']}", ""]
            name = os.path.basename(item["file"])[:-3]
            body.append(f"- [[{name}|Статья {item['article']}. {item['heading']}]]")
        body.append("")
        open(os.path.join(REPO, directory, "_glava.md"), "w", encoding="utf-8").write("\n".join(body))

    meta = {
        "doc_id": DOC_ID, "edition_id": EDITION_ID, "kind": "edition_meta", "lang": LANG,
        "document_title": "Налоговый кодекс Республики Казахстан",
        "revision_id": REVISION_ID, "effective_from": CODE_EFFECTIVE_FROM,
        "article_count": len(records), "source_name": "Учет.kz",
    }
    open(os.path.join(REPO, "code", LANG, "_meta.md"), "w", encoding="utf-8").write(
        "\n".join([
            dump_frontmatter(meta), "",
            "# Налоговый кодекс РК — редакция 2026-01-01 (русский текст)", "",
            f"Статей: {len(records)}. Полное дерево — [[../../index|index.md]].",
            "Введён в действие с 1 января 2026 года (статья 848), отдельные нормы — позже.",
            "",
        ])
    )


def write_index(records: list[dict]) -> None:
    lines = [
        dump_frontmatter({
            "doc_id": DOC_ID, "edition_id": EDITION_ID, "kind": "index", "lang": LANG,
            "article_count": len(records), "revision_id": REVISION_ID,
        }),
        "", "# Налоговый кодекс РК — содержание", "",
        f"Статей: {len(records)}. Редакция {REVISION_ID}. Схема полей — [SCHEMA.md](SCHEMA.md).",
        "",
    ]
    part = razdel = glava = paragraf = object()
    for record in records:
        if record["part"] != part:
            part, razdel, glava, paragraf = record["part"], object(), object(), object()
            lines += ["", f"## {part}", ""]
        if record["razdel"] != razdel:
            razdel, glava, paragraf = record["razdel"], object(), object()
            title = record["razdel_title"] or "название отсутствует в источнике"
            label = f"Раздел {razdel}. {title}" if razdel else "Вне разделов"
            lines += ["", f"### {label}", ""]
        if record["glava"] != glava:
            glava, paragraf = record["glava"], object()
            label = f"Глава {glava}. {record['glava_title']}" if glava else "Вне глав"
            lines += ["", f"#### {label}", ""]
        if record["paragraf"] != paragraf:
            paragraf = record["paragraf"]
            if paragraf is not None:
                lines += ["", f"*Параграф {paragraf}. {record['paragraf_title']}*", ""]
        lines.append(f"- [Статья {record['article']}. {record['heading']}]({record['file']})")
    lines.append("")
    open(os.path.join(REPO, "index.md"), "w", encoding="utf-8").write("\n".join(lines))


def write_manifest(records, src_manifest, toc, export: str) -> None:
    numbers = Counter(r["article"] for r in records)
    manifest = {
        "manifest_version": 1,
        "doc_id": DOC_ID,
        "document_title": "Налоговый кодекс Республики Казахстан",
        "edition_id": EDITION_ID,
        "revision_id": REVISION_ID,
        "effective_from": CODE_EFFECTIVE_FROM,
        "languages": [LANG],
        "encoding": "UTF-8",
        "line_ending": "LF",
        "index_file": "index.md",
        "corpus_root": f"code/{LANG}",
        "source": {
            "name": "Учет.kz",
            "url": src_manifest["source_url"],
            "retrieved_at": src_manifest["started_at"],
            "toc_sha256": toc["toc_sha256"],
            "export_manifest_sha256": sha256_bytes(
                open(os.path.join(export, "manifest.json"), "rb").read()
            ),
        },
        "counts": {
            "articles": len(records),
            "toc_entries": toc["entry_count"],
            "unique_article_numbers": len(numbers),
            "duplicate_article_numbers": sorted(n for n, c in numbers.items() if c > 1),
            "empty_source_bodies": [r["article"] for r in records if r["status"] == "source_empty"],
            "razdels": len({(r["part"], r["razdel"]) for r in records}),
            "glavas": len({(r["part"], r["razdel"], r["glava"]) for r in records}),
        },
        "anomalies": {
            "razdel_title_missing_in_source": sorted(
                {r["razdel"] for r in records if r["razdel_title"] is None and r["razdel"]}
            ),
            "articles_without_razdel": [r["article"] for r in records if r["razdel"] is None],
            "api_title_warnings": src_manifest["anomalies"].get("api_title_warnings", []),
        },
        "files": records,
    }
    with open(os.path.join(REPO, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def copy_provenance(export: str) -> None:
    target = os.path.join(REPO, "provenance")
    os.makedirs(target, exist_ok=True)
    shutil.copyfile(os.path.join(export, "audit", "api-toc.json"), os.path.join(target, "source-toc.json"))
    shutil.copyfile(os.path.join(export, "manifest.json"), os.path.join(target, "source-manifest.json"))
    shutil.copyfile(os.path.join(export, "VALIDATION.md"), os.path.join(target, "source-validation.md"))


if __name__ == "__main__":
    raise SystemExit(main())
