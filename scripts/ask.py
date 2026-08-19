#!/usr/bin/env python3
"""Вопрос к Налоговому кодексу: поиск по корпусу + ответ модели со ссылками.

Полный цикл RAG в одном файле и без единой зависимости: находим релевантные
пункты статей полнотекстовым поиском, собираем их в контекст и просим модель
ответить, ссылаясь на статьи и пункты. Без ключа работает как «сухой прогон» —
показывает, что именно нашлось и что ушло бы в модель.

Ключ берётся из окружения (`ANTHROPIC_API_KEY`) и никуда не записывается.

Использование:
    python3 scripts/build_sqlite.py                      # один раз, соберёт индекс
    python3 scripts/ask.py "Кто обязан встать на учёт по НДС?"
    python3 scripts/ask.py --top-k 12 --dry-run "Ставка НДС"
    python3 scripts/ask.py --model claude-sonnet-5 "Что такое налоговый регистр?"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5"
STOP_WORDS = {
    "что", "как", "какие", "какая", "какой", "каков", "кто", "когда", "где", "для",
    "при", "это", "есть", "или", "не", "по", "на", "в", "во", "и", "с", "со", "от",
    "до", "за", "у", "к", "об", "о", "ли", "же", "нужно", "обязан", "можно",
}

SYSTEM_PROMPT = """Ты помогаешь разобраться в Налоговом кодексе Республики Казахстан.

Отвечай только по фрагментам кодекса, приведённым в запросе. Правила:
1. Каждое утверждение сопровождай ссылкой в формате «(статья N, пункт M)».
2. Если во фрагментах нет ответа — так и скажи и назови, чего не хватает.
   Не додумывай норму и не опирайся на память.
3. Не давай налоговых консультаций и не оценивай конкретную ситуацию как
   «законную» или «незаконную» — излагай, что написано в норме.
4. Если норма вводится в действие позже общей даты, назови этот срок.
5. Отвечай на языке вопроса, кратко и по существу."""


def search(question: str, top_k: int, database: str) -> list[dict]:
    """Отбирает пункты статей полнотекстовым поиском BM25."""
    words = [w for w in re.findall(r"[\w\-]+", question.lower())
             if len(w) > 2 and w not in STOP_WORDS]
    query = " OR ".join(f'"{w}"' for w in words) or '"налог"'
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT c.chunk_id, c.article, c.article_label, c.heading, c.point, c.path, "
        "       c.context_header, c.text, c.effective_from, c.effective_note, c.source_url "
        "FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 2.0, 1.0) LIMIT ?",
        (query, top_k),
    ).fetchall()
    connection.close()
    keys = ("chunk_id", "article", "article_label", "heading", "point", "path",
            "context_header", "text", "effective_from", "effective_note", "source_url")
    return [dict(zip(keys, row)) for row in rows]


def build_context(chunks: list[dict]) -> str:
    """Контекстный заголовок идёт перед текстом — так модель не теряет, откуда фрагмент."""
    blocks = []
    for chunk in chunks:
        header = chunk["context_header"]
        if chunk["effective_note"]:
            header += f"; особый порядок введения в действие: {chunk['effective_note']}"
        blocks.append(f"<фрагмент>\n{header}\n\n{chunk['text']}\n</фрагмент>")
    return "\n\n".join(blocks)


def ask_model(question: str, context: str, model: str, api_key: str, max_tokens: int) -> str:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": (
                f"Фрагменты Налогового кодекса РК:\n\n{context}\n\n"
                f"Вопрос: {question}"
            ),
        }],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"API вернул {error.code}: {detail}") from error

    if body.get("stop_reason") == "refusal":
        return "[Модель отклонила запрос.]"
    return "\n".join(block["text"] for block in body["content"] if block["type"] == "text")


def main() -> int:
    parser = argparse.ArgumentParser(description="Вопрос к корпусу НК РК")
    parser.add_argument("question", help="вопрос на естественном языке")
    parser.add_argument("--top-k", type=int, default=8, help="сколько фрагментов подать модели")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"модель (по умолчанию {DEFAULT_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=16000, help="потолок ответа")
    parser.add_argument("--db", default=os.path.join(REPO, "dist", "tax.sqlite"), help="путь к базе поиска")
    parser.add_argument("--dry-run", action="store_true", help="показать найденное и не звать модель")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"нет базы поиска: {args.db}\nсоберите её: python3 scripts/build_sqlite.py", file=sys.stderr)
        return 2

    chunks = search(args.question, args.top_k, args.db)
    if not chunks:
        print("по этому вопросу ничего не нашлось — попробуйте переформулировать", file=sys.stderr)
        return 1

    print("Найденные нормы:")
    for chunk in chunks:
        point = f", пункт {chunk['point']}" if chunk["point"] else ""
        print(f"  {chunk['article_label']}{point}. {chunk['heading']}")
        print(f"    {chunk['source_url']}")
    print()

    context = build_context(chunks)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.dry_run or not api_key:
        if not api_key and not args.dry_run:
            print("ANTHROPIC_API_KEY не задан — показываю только контекст (сухой прогон).\n", file=sys.stderr)
        print(context)
        return 0

    print("Ответ:\n")
    print(ask_model(args.question, context, args.model, api_key, args.max_tokens))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
