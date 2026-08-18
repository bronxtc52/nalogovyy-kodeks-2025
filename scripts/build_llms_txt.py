#!/usr/bin/env python3
"""Генерация llms.txt — точки входа для агентов (спецификация llmstxt.org).

Файл обязан оставаться коротким: разделы и ключевые артефакты со ссылками,
детали агент дотягивает по ссылке сам.

Использование:
    python3 scripts/build_llms_txt.py
"""

from __future__ import annotations

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "https://raw.githubusercontent.com/bronxtc52/nalogovyy-kodeks-2025/main"
WEB = "https://github.com/bronxtc52/nalogovyy-kodeks-2025/blob/main"


def main() -> int:
    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))
    counts = manifest["counts"]
    stats = json.load(open(os.path.join(REPO, "data", "chunks.stats.json"), encoding="utf-8"))

    razdels: dict[tuple, dict] = {}
    for record in manifest["files"]:
        key = (record["part"], record["razdel"])
        node = razdels.setdefault(key, {
            "title": record["razdel_title"], "articles": [],
            "directory": os.path.dirname(os.path.dirname(record["file"])),
        })
        node["articles"].append(record["article"])

    lines = [
        "# Налоговый кодекс Республики Казахстан — машинно-читаемый корпус",
        "",
        f"> Полный текст НК РК в редакции {manifest['revision_id']} "
        f"({counts['articles']} статей, {stats['chunks']} чанков по пунктам): Markdown с "
        "frontmatter как источник истины, JSONL-чанки и локальный полнотекстовый индекс. "
        "Неофициальная копия: для юридически значимых решений сверяйтесь с эталонным "
        "контрольным банком НПА.",
        "",
        f"Кодекс введён в действие с {manifest['effective_from']} (статья 848); отдельные нормы — "
        "позднее, точная дата у каждой статьи в поле effective_from. Каждый пункт статьи "
        "адресуем якорем вида `st-190.md#p-3`, чанк — идентификатором `.../st-190/p-3`, "
        "поэтому цитату можно дать до пункта.",
        "",
        "## Начать отсюда",
        "",
        f"- [Оглавление]({RAW}/index.md): полное дерево часть → раздел → глава → параграф → статья со ссылками на файлы.",
        f"- [README]({RAW}/README.md): что внутри, четыре способа подключения, границы корпуса.",
        f"- [Схема полей]({RAW}/SCHEMA.md): frontmatter статьи, запись чанка, манифест, инварианты CI.",
        f"- [Правовой статус]({RAW}/DISCLAIMER.md): почему это неофициальная копия и что из этого следует.",
        "",
        "## Данные",
        "",
        f"- [manifest.json]({RAW}/manifest.json): редакция, счётчики, sha256 по каждому файлу, аномалии источника.",
        f"- [chunks.jsonl]({WEB}/data/chunks.jsonl): {stats['chunks']} чанков по пунктам статей с контекстными заголовками; медиана ≈{stats['approx_tokens_median']} токенов.",
        f"- [Эталонные вопросы]({WEB}/eval/qa-ru.jsonl) и [baseline-прогон]({RAW}/eval/RESULTS.md): честные цифры лексического поиска без эмбеддингов.",
        "",
        "## Разделы кодекса",
        "",
    ]

    for (part, razdel), node in sorted(
        razdels.items(), key=lambda item: (item[0][0] != "Общая часть", item[0][1] or 0)
    ):
        if razdel:
            label = f"Раздел {razdel}" + (f". {node['title']}" if node["title"] else " (название отсутствует в источнике)")
        else:
            label = "Вне разделов"
        numbers = node["articles"]
        lines.append(
            f"- [{label}]({WEB}/{node['directory']}): {part}, статьи {numbers[0]}–{numbers[-1]}, всего {len(numbers)}."
        )

    lines += [
        "",
        "## Optional",
        "",
        f"- [Скрипты сборки]({WEB}/scripts): выгрузка → Markdown → чанки → sqlite; только стандартная библиотека Python.",
        f"- [Проверки целостности]({RAW}/scripts/validate.py): 15 инвариантов, гоняются в CI на каждый PR.",
        f"- [Происхождение]({WEB}/provenance): оглавление и манифест исходной выгрузки с хэшами.",
        f"- [История редакций]({RAW}/CHANGELOG.md): версии корпуса и редакции кодекса.",
        "",
    ]

    with open(os.path.join(REPO, "llms.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"llms.txt: {len(lines)} строк, {len(razdels)} разделов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
