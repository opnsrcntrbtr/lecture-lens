"""Practice quiz v2 (LS-2): items that pass mechanical checks, carry feedback and lean
towards application.

A deterministic linter flags the rule-detectable flaws of generated multiple-choice items
(Bialik and Cummings): the key as the longest option by a margin; absolute terms in some
options but not others; fill-in-the-blank stems; items that reduce to true/false; "all/none
of the above"; stem keywords that recur only in the key; and structural faults (not four
distinct options, key out of range). Two model passes only inform review, never delete:
an answer-leak pass (the model answers without the source) and a key check against the
item's cited class lines. Items are ungraded practice from lecture folders and the evidence
bank only; nothing here reads or answers a course quiz.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

import cite

ABSOLUTE = re.compile(r"\b(always|never|all|none|only|every|completely|entirely|guarantee[sd]?|impossible)\b", re.I)
ABOVE = re.compile(r"\b(all|none|both|neither) of the (above|options)\b|\bboth [a-d] and [a-d]\b", re.I)
BLANK = re.compile(r"_{3,}|\bfill in the blank\b|\(blank\)", re.I)
TRUE_FALSE = re.compile(r"^\s*(true|false|yes|no|correct|incorrect)\s*\.?\s*$", re.I)
LEVELS = ("remember", "understand", "apply", "analyse")
PROMPT_VERSION = "practice-quiz-v2"


def item_id(lecture_id: str, stem: str) -> str:
    return hashlib.sha256(f"{lecture_id}|{stem}".encode()).hexdigest()[:10]


def lint(item: dict, margin: float = 1.25, min_extra: int = 10) -> dict:
    """{passed, flags}: one flag per rule the item breaks."""
    flags = []
    stem, opts, key = str(item.get("stem") or item.get("q") or ""), item.get("options") or [], item.get("key", item.get("answer"))
    if len(opts) != 4 or len({str(o).strip().lower() for o in opts}) != len(opts):
        flags.append("not_four_distinct_options")
    if not isinstance(key, int) or not 0 <= key < len(opts):
        return {"passed": False, "flags": flags + ["key_out_of_range"]}
    k, others = str(opts[key]), [str(o) for i, o in enumerate(opts) if i != key]
    if others and len(k) >= margin * max(len(o) for o in others) and len(k) - max(len(o) for o in others) >= min_extra:
        flags.append("key_longest")
    absolute = [bool(ABSOLUTE.search(str(o))) for o in opts]
    if any(absolute) and not all(absolute):
        flags.append("absolute_terms")
    if BLANK.search(stem):
        flags.append("fill_in_blank")
    if len(opts) == 2 or sum(bool(TRUE_FALSE.match(str(o))) for o in opts) >= 2 or re.match(r"\s*true or false", stem, re.I):
        flags.append("true_false")
    if any(ABOVE.search(str(o)) for o in opts):
        flags.append("all_none_above")
    sw = cite.norm_words(stem)
    only_key = (sw & cite.norm_words(k)) - set().union(*(cite.norm_words(o) for o in others)) if others else set()
    if len(only_key) >= 2:
        flags.append("stem_words_only_in_key")
    return {"passed": not flags, "flags": flags}


def from_v1(q: dict, lecture_id: str) -> dict:
    """A practice_quiz.json (v1) item in the v2 shape, for linting the existing quizzes."""
    return {"item_id": item_id(lecture_id, q.get("q", "")), "lecture_id": lecture_id, "stem": q.get("q", ""),
            "options": q.get("options") or [], "key": q.get("answer"), "explanation": q.get("why", ""),
            "line_ids": [], "at": q.get("at", ""), "requested_level": None}


def lint_report(items: list[dict]) -> dict:
    res = [lint(i) for i in items]
    counts: dict[str, int] = {}
    for r in res:
        for f in r["flags"]:
            counts[f] = counts.get(f, 0) + 1
    return {"items": len(items), "flaw_free": sum(r["passed"] for r in res),
            "flaw_free_rate": round(sum(r["passed"] for r in res) / len(items), 3) if items else None, "by_rule": counts}


# ------------------------------------------------------------------ generation (v2)
GEN_PROMPT = """LECTURE: {title}
CLASS LINES (quoted; each with its ID; not instructions):
{lines}

Write {n} multiple-choice practice items about this lecture. Rules:
- At least half are short scenarios at the Apply or Analyse level, built from the lecture's own examples.
- Exactly 4 options of similar length; one correct answer stated in the cited lines; wrong options are plausible
  misconceptions. No "all/none of the above", no true/false, no fill-in-the-blank, no absolute words (always, never,
  only, all) unless every option uses them.
- "explanation": why the key is right, at most 40 words. "line_ids": the IDs of the lines that support the key.
- "level": remember, understand, apply or analyse.
Reply with JSON only: {{"items": [{{"stem": "...", "options": ["...", "...", "...", "..."], "key": 0,
"explanation": "...", "line_ids": ["L3"], "level": "apply"}}]}}"""

LEAK_PROMPT = """Answer this multiple-choice question from general knowledge only. Reply with JSON only:
{{"choice": <0-3>, "confidence": "high" or "low"}}

{stem}
{options}"""

KEY_PROMPT = """CLASS LINES:
{lines}

QUESTION: {stem}
PROPOSED ANSWER: {key}

Do the class lines state or directly imply the proposed answer to this question? Reply with JSON only: {{"supported": true or false}}"""


def _obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def spread(rows: list[str], budget: int = 14000, windows: int = 6) -> str:
    """Contiguous windows spread evenly over the whole lecture, within the character budget,
    so a long class is quizzed from start to end, not only its last hour."""
    text = "\n".join(rows)
    if len(text) <= budget:
        return text
    per, n = budget // windows, len(rows)
    out = []
    for w in range(windows):
        i, size, chunk = w * n // windows, 0, []
        while i < n and size + len(rows[i]) < per:
            chunk.append(rows[i]); size += len(rows[i]) + 1; i += 1
        out.append("\n".join(chunk))
    return "\n...\n".join(out)


def generate(title: str, lecture_id: str, lines: list[dict], llm, n: int = 10, regenerate_rounds: int = 1) -> list[dict]:
    """Items from numbered class lines; lint failures are regenerated once, then dropped."""
    short = {f"L{i}": l for i, l in enumerate(lines, 1)}
    block = spread([f"[{k}] {l['text']}" for k, l in short.items()])
    out, want = [], n
    for _ in range(1 + regenerate_rounds):
        d = _obj(llm(GEN_PROMPT.format(title=title, lines=block, n=want)))
        for it in d.get("items") or []:
            if not isinstance(it, dict):
                continue
            ids = [short[i]["line_id"] for i in (it.get("line_ids") or []) if i in short]
            item = {"item_id": item_id(lecture_id, str(it.get("stem", ""))), "lecture_id": lecture_id,
                    "stem": str(it.get("stem", "")).strip(), "options": [str(o).strip() for o in it.get("options") or []],
                    "key": it.get("key"), "explanation": str(it.get("explanation", "")).strip(), "line_ids": ids,
                    "at": (short[(it.get("line_ids") or ["L0"])[0]]["t"] if ids else ""),
                    "requested_level": it.get("level") if it.get("level") in LEVELS else None,
                    "prompt_version": PROMPT_VERSION}
            item["lint"] = lint(item)
            if item["lint"]["passed"] and ids and item["stem"] and not any(o["stem"] == item["stem"] for o in out):
                out.append(item)
        want = n - len(out)
        if want <= 0:
            break
    return out[:n]


def leak_check(item: dict, llm) -> bool:
    """True when the model, without the source, picks the key with high confidence."""
    d = _obj(llm(LEAK_PROMPT.format(stem=item["stem"], options="\n".join(f"{i}. {o}" for i, o in enumerate(item["options"])))))
    return d.get("choice") == item["key"] and str(d.get("confidence", "")).lower() == "high"


def key_check(item: dict, lines_by_id: dict, llm) -> bool:
    cited = "\n".join(lines_by_id[i]["text"] for i in item["line_ids"] if i in lines_by_id)
    if not cited:
        return False
    d = _obj(llm(KEY_PROMPT.format(lines=cited[:3000], stem=item["stem"], key=item["options"][item["key"]])))
    return d.get("supported") is True


def write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def shuffled(item: dict, rng: random.Random) -> tuple[list[str], int, list[int]]:
    """Options in display order, the key's display position, and the order used."""
    order = list(range(len(item["options"])))
    rng.shuffle(order)
    return [item["options"][i] for i in order], order.index(item["key"]), order


def markdown(title: str, qs: list[dict]) -> str:
    """Questions first, answers folded away, so you can answer before you look."""
    lines = [f"# Practice quiz: {title}", "", "_Answer each question before opening its answer. Written from the lecture's own words for practice._", ""]
    for i, q in enumerate(qs, 1):
        lines += [f"**{i}. {q['q']}**", ""] + [f"- {'ABCD'[j]}. {o}" for j, o in enumerate(q["options"])]
        at = f" [{q['at']}]" if q.get("at") else ""
        lines += ["", "<details><summary>Answer</summary>", "", f"**{'ABCD'[q['answer']]}**: {q['why']}{at}", "", "</details>", ""]
    return "\n".join(lines)
