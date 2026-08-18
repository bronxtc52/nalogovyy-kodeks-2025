#!/usr/bin/env python3
"""Baseline-прогон поиска по eval/qa-ru.jsonl на полнотекстовом индексе FTS5.

Это ЧЕСТНЫЙ пол, а не рекламная цифра: BM25 по словам, без эмбеддингов и реранкера.
Векторный прогон (BGE-M3) появится вместе с эмбеддингами в версии 0.2.0.

Использование:
    python3 scripts/build_sqlite.py && python3 scripts/eval_fts.py
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOP = {
    "что", "как", "какие", "какая", "какой", "каков", "кто", "когда", "где", "и", "в", "во",
    "на", "по", "для", "от", "до", "за", "при", "о", "об", "с", "со", "у", "к", "это", "есть",
    "быть", "или", "же", "ли", "не", "нужен", "зачем", "новый", "предусмотрены", "применяются",
    "применяется", "является", "относится", "обязан", "встать", "своими", "словами", "теме",
}
K_VALUES = (1, 5, 10, 20)


def query_terms(question: str) -> str:
    words = [w for w in re.findall(r"[\w\-]+", question.lower()) if len(w) > 2 and w not in STOP]
    return " OR ".join(f'"{w}"' for w in words) or '"налог"'


def main() -> int:
    database = os.path.join(REPO, "dist", "tax.sqlite")
    if not os.path.exists(database):
        print("нет dist/tax.sqlite — сначала python3 scripts/build_sqlite.py", file=sys.stderr)
        return 2
    connection = sqlite3.connect(database)
    items = [json.loads(line) for line in open(os.path.join(REPO, "eval", "qa-ru.jsonl"), encoding="utf-8")]

    results = {kind: {k: 0 for k in K_VALUES} for kind in ("manual", "title-derived")}
    totals = {kind: 0 for kind in results}
    misses: list[dict] = []
    max_k = max(K_VALUES)

    for item in items:
        rows = connection.execute(
            "SELECT c.article FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 2.0, 1.0) LIMIT ?",
            (query_terms(item["question"]), max_k),
        ).fetchall()
        ranked: list[str] = []
        for (article,) in rows:
            if article not in ranked:
                ranked.append(article)
        expected = set(item["expected_articles"])
        totals[item["kind"]] += 1
        for k in K_VALUES:
            if expected & set(ranked[:k]):
                results[item["kind"]][k] += 1
        if not expected & set(ranked[:max_k]):
            misses.append({"id": item["id"], "question": item["question"],
                           "expected": item["expected_articles"], "got": ranked[:5]})

    report = {
        "engine": "sqlite fts5 bm25 (unicode61), без эмбеддингов и реранкера",
        "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "by_kind": {
            kind: {f"recall@{k}": round(results[kind][k] / totals[kind], 3) for k in K_VALUES}
            for kind in results if totals[kind]
        },
        "counts": totals,
        "misses": misses,
    }
    with open(os.path.join(REPO, "eval", "results-fts.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    write_results_md(report)
    print(json.dumps({k: v for k, v in report.items() if k != "misses"}, ensure_ascii=False, indent=2))
    print(f"промахов вне top-{max_k}: {len(misses)}")
    return 0


def write_results_md(report: dict) -> None:
    """RESULTS.md генерируется из прогона, чтобы цифры в тексте не разъезжались с фактом."""
    names = {"manual": "вопросы бухгалтера", "title-derived": "выведенные из заголовков"}
    lines = [
        "# Результаты baseline-прогона",
        "",
        f"Движок: {report['engine']}. Чанков в индексе: {report['chunks']}.",
        "Файл генерируется скриптом `scripts/eval_fts.py`; править руками бессмысленно —",
        "следующий прогон перезапишет.",
        "",
        "## Recall по наборам",
        "",
        "| Набор | вопросов | recall@1 | recall@5 | recall@10 | recall@20 |",
        "|---|---|---|---|---|---|",
    ]
    for kind, metrics in report["by_kind"].items():
        lines.append(
            f"| {names.get(kind, kind)} | {report['counts'][kind]} | "
            + " | ".join(str(metrics[f"recall@{k}"]) for k in K_VALUES) + " |"
        )
    lines += [
        "",
        "## Как это читать",
        "",
        "- Это **пол качества**, а не витрина: голый BM25 по словам, без эмбеддингов и реранкера.",
        "  Векторный прогон появится вместе с эмбеддингами в версии 0.2.0, и цифры будут рядом,",
        "  а не вместо этих.",
        "- Набор «выведенные из заголовков» лёгок по построению: вопрос собран из заголовка статьи,",
        "  поэтому лексическое совпадение почти гарантировано. Он нужен как регрессия на разметку,",
        "  а не как доказательство качества поиска.",
        "- Набор «вопросы бухгалтера» сформулирован своими словами; ожидаемые статьи подтверждены",
        "  совпадением по заголовку при сборке набора, а не выписаны по памяти.",
        "- Метрика — доля вопросов, где хотя бы одна ожидаемая статья попала в top-K по статьям",
        "  (чанки схлопываются до статей до подсчёта).",
        "",
        f"## Промахи вне top-{max(K_VALUES)} ({len(report['misses'])})",
        "",
    ]
    if report["misses"]:
        lines += ["| Вопрос | ожидалось | нашлось |", "|---|---|---|"]
        for miss in report["misses"]:
            lines.append(
                f"| {miss['question']} | {', '.join(miss['expected'])} | {', '.join(miss['got']) or '—'} |"
            )
    else:
        lines.append("Промахов нет.")
    lines += [
        "",
        "Промахи не прячем: они и есть рабочий список на улучшение разметки и нарезки.",
        "",
    ]
    with open(os.path.join(REPO, "eval", "RESULTS.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
