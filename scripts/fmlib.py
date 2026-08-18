"""Минимальные помощники: slug, YAML-frontmatter (запись и чтение), хэши.

Только стандартная библиотека — скрипты должны работать в CI без установки зависимостей.
Frontmatter, который мы пишем, ограничен скалярами и плоскими списками строк, поэтому
собственного парсера достаточно и PyYAML не требуется.
"""

from __future__ import annotations

import hashlib
import re

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
    "і": "i", "ғ": "g", "қ": "q", "ң": "ng", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "ә": "a",
}


def slugify(text: str, limit: int = 60) -> str:
    out = []
    for ch in text.lower():
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum()):
            out.append(ch)
        else:
            out.append("-")
    s = re.sub(r"-+", "-", "".join(out)).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0].strip("-") or s[:limit].strip("-")
    return s


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def dump_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {yaml_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _parse_scalar(raw: str):
    raw = raw.strip()
    if raw == "null" or raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "[]":
        return []
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def load_frontmatter(text: str) -> tuple[dict, str]:
    """Возвращает (поля frontmatter, тело документа)."""
    if not text.startswith("---\n"):
        raise ValueError("нет frontmatter")
    end = text.index("\n---\n", 3)
    head, body = text[4:end + 1], text[end + 5:]
    fields: dict = {}
    key = None
    for line in head.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and key is not None:
            fields.setdefault(key, [])
            if not isinstance(fields[key], list):
                fields[key] = []
            fields[key].append(_parse_scalar(line[4:]))
            continue
        match = re.match(r"^([A-Za-z0-9_]+):(.*)$", line)
        if not match:
            raise ValueError(f"не разобрана строка frontmatter: {line!r}")
        key, rest = match.group(1), match.group(2)
        fields[key] = [] if rest.strip() == "" else _parse_scalar(rest)
    return fields, body
