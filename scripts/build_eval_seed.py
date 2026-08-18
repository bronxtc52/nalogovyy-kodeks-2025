#!/usr/bin/env python3
"""Генерация eval/qa-ru.jsonl — эталонных пар «вопрос → ожидаемые статьи».

Два вида вопросов, и они честно помечены полем kind:
  * title-derived  — перефразированный заголовок статьи (генерируются автоматически,
    по построению лёгкие для поиска; нужны как smoke-набор и регрессия);
  * manual         — вопросы бухгалтера своими словами; ожидаемые статьи проверены
    через полнотекстовый поиск при сборке, а не выписаны на память.

Использование:
    python3 scripts/build_eval_seed.py
"""

from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Вопросы бухгалтера. Каждый обязан быть подтверждён совпадением по заголовку
# статьи (title_must_match) — иначе элемент не попадёт в набор и скрипт сообщит об этом.
MANUAL = [
    ("Что кодекс понимает под роялти?", r"^Роялти$", None),
    ("Что такое налоговый регистр и зачем он нужен?", r"[Нн]алоговые регистры", None),
    ("Когда новый Налоговый кодекс вводится в действие?", r"Порядок введения в действие", None),
    ("Какие ставки корпоративного подоходного налога применяются?", r"^Ставки налога$", 5),
    ("Какая ставка налога на добавленную стоимость?", r"^Ставки налога на добавленную стоимость$", 7),
    ("Кто обязан встать на регистрационный учёт по НДС?",
     r"регистрационн\w+ учет\w* плательщика налога на добавленную стоимость", None),
    ("Каков срок исковой давности по налоговому обязательству?", r"^Сроки исковой давности", None),
    ("На каких условиях применяется специальный налоговый режим на основе упрощённой декларации?",
     r"^Условия применения специального налогового режима на основе упрощенной декларации$", 16),
    ("Как обжаловать уведомление о результатах налоговой проверки?",
     r"обжаловани\w+ уведомления о результатах налоговой проверки", None),
    ("Какие права и обязанности есть у налогоплательщика?",
     r"^Права и обязанности налогоплательщика \(налогового агента\)$", 1),
    ("Что является объектом обложения налогом на имущество?", r"^Объект налогообложения$", 12),
    ("Как исчисляется социальный налог?", r"^Порядок исчисления социального налога$", 9),
]

TITLE_QUESTION_PREFIX = "Что установлено в НК РК по теме: "


def main() -> int:
    manifest = json.load(open(os.path.join(REPO, "manifest.json"), encoding="utf-8"))
    records = [r for r in manifest["files"] if r["status"] != "source_empty"]
    items: list[dict] = []

    unresolved: list[str] = []
    for question, pattern, razdel in MANUAL:
        regex = re.compile(pattern, re.I)
        matches = [
            r for r in records
            if regex.search(r["heading"]) and (razdel is None or r["razdel"] == razdel)
        ]
        if not matches:
            unresolved.append(question)
            continue
        items.append({
            "id": f"manual-{len(items) + 1:03d}",
            "kind": "manual",
            "lang": "ru",
            "question": question,
            "expected_articles": [r["article"] for r in matches[:4]],
            "expected_headings": [r["heading"] for r in matches[:4]],
            "matched_by": pattern,
            "razdel": razdel,
        })

    step = max(1, len(records) // 40)
    for index, record in enumerate(records[::step]):
        items.append({
            "id": f"title-{index + 1:03d}",
            "kind": "title-derived",
            "lang": "ru",
            "question": TITLE_QUESTION_PREFIX + record["heading"].rstrip(".") + "?",
            "expected_articles": [record["article"]],
            "expected_headings": [record["heading"]],
            "matched_by": "heading",
        })

    path = os.path.join(REPO, "eval", "qa-ru.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    kinds = {"manual": 0, "title-derived": 0}
    for item in items:
        kinds[item["kind"]] += 1
    print(f"{path}: {len(items)} вопросов ({kinds})")
    if unresolved:
        print("не подтверждены заголовком и потому не включены:")
        for question in unresolved:
            print(f"  - {question}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
