"""LS-3 review scheduling to a weekly minutes budget (FSRS-6, default parameters).

The learner sets weekly minutes. A workload simulation at several desired-retention values
picks the highest retention whose expected weekly minutes fit the budget; if none fits, new
cards per week are reduced until one does. Sessions are capped by the day's share of the
budget (class days are "easy days" at half share); an overdue backlog is served lowest
predicted recall first. There are no streaks. Parameters stay at the FSRS-6 defaults; the
history size at which to optimise them is a learner setting (not done here).

Got it / Missed it map to Good (3) / Again (1). The log is the source of truth: a card's
memory state is its log replayed.

The log is each lecture's reviews.jsonl, the same file the Studio and the older review
command write ({card, ts, pass}); this module adds grade, elapsed_s, stability and difficulty
to each line, which other readers ignore.

Files (practice/):
  review_settings.json  {weekly_minutes, class_weekdays, new_per_week, optimise_after}
  review_plan.json      the last simulation: chosen retention, new per week, weekly minutes
  card_overrides.json   {card_id: {suspended, deleted, q, a}}
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
from pathlib import Path

# ------------------------------------------------------------------ FSRS-6
# Default parameters w0..w20 (open-spaced-repetition py-fsrs 6.x DEFAULT_PARAMETERS).
W = (0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614,
     0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542)
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
DECAY = -W[20]
FACTOR = 0.9 ** (1 / DECAY) - 1          # so that R(S, S) = 0.9
S_MIN, MAX_INTERVAL = 0.001, 365
LEECH_LAPSES = 3
RETENTIONS = (0.95, 0.92, 0.90, 0.87, 0.85, 0.80, 0.75)
DEFAULTS = {"weekly_minutes": 60, "class_weekdays": [5], "new_per_week": 30, "optimise_after": 1000}
SECONDS = {"new": 30.0, "review": 12.0, "lapse": 20.0}   # until the log has timings


def retrievability(days: float, s: float) -> float:
    return (1 + FACTOR * max(0.0, days) / s) ** DECAY


def interval(s: float, retention: float) -> int:
    i = s / FACTOR * (retention ** (1 / DECAY) - 1)
    return int(min(MAX_INTERVAL, max(1, round(i))))


def _cd(d: float) -> float:
    return min(10.0, max(1.0, d))


def init_s(g: int) -> float:
    return max(W[g - 1], S_MIN)


def init_d(g: int, clamp: bool = True) -> float:
    d = W[4] - math.exp(W[5] * (g - 1)) + 1
    return _cd(d) if clamp else d


def next_d(d: float, g: int) -> float:
    d1 = d + (10.0 - d) * (-(W[6] * (g - 3))) / 9.0
    return _cd(W[7] * init_d(EASY, clamp=False) + (1 - W[7]) * d1)


def s_recall(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == HARD else 1
    easy = W[16] if g == EASY else 1
    return s * (1 + math.exp(W[8]) * (11 - d) * s ** -W[9] * (math.exp((1 - r) * W[10]) - 1) * hard * easy)


def s_forget(d: float, s: float, r: float) -> float:
    long = W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])
    return min(long, s / math.exp(W[17] * W[18]))


def s_short(s: float, g: int) -> float:
    inc = math.exp(W[17] * (g - 3 + W[18])) * s ** -W[19]
    if g >= HARD:
        inc = max(inc, 1.0)
    return max(s * inc, S_MIN)


def step(state: dict | None, g: int, t: dt.datetime) -> dict:
    """The memory state after one review at time t with grade g."""
    if state is None:
        st = {"s": init_s(g), "d": init_d(g), "reps": 0, "lapses": 0}
    else:
        st = dict(state)
        days = (t - state["last"]).total_seconds() / 86400
        if days < 1:
            st["s"] = s_short(state["s"], g)
        else:
            r = retrievability(days, state["s"])
            st["s"] = max(s_recall(state["d"], state["s"], r, g) if g >= HARD else s_forget(state["d"], state["s"], r), S_MIN)
        st["d"] = next_d(state["d"], g)
    st["reps"] += 1
    st["lapses"] += g == AGAIN
    st["last"] = t
    return st


def replay(events: list[dict]) -> dict | None:
    st = None
    for e in sorted(events, key=lambda e: e["t"]):
        st = step(st, e["grade"], dt.datetime.fromisoformat(e["t"]))
    return st


def due_at(st: dict, retention: float) -> dt.datetime:
    return st["last"] + dt.timedelta(days=interval(st["s"], retention))


# ------------------------------------------------------------------ cards
def card_id(question: str) -> str:
    return hashlib.sha1(" ".join(question.lower().split()).encode()).hexdigest()[:12]


CARD_FILES = ("cards.tsv", "cards_prequestions.tsv", "cards_cues.tsv", "coach_cards.tsv", "reference/reference_cards.tsv")


def collect(lectures: Path) -> list[dict]:
    """Every card in built lecture folders, oldest lecture first; the same question twice is one card."""
    out, seen = [], set()
    for f in sorted(p for p in Path(lectures).glob("*") if (p / "manifest.json").exists()):
        try:
            man = json.loads((f / "manifest.json").read_text())
        except ValueError:
            man = {}
        for name in CARD_FILES:
            p = f / name
            if not p.exists():
                continue
            for line in p.read_text().splitlines():
                parts = line.split("\t")
                if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
                    continue
                cid = card_id(parts[0])
                if cid in seen:
                    continue
                seen.add(cid)
                out.append({"card_id": cid, "q": parts[0].strip(), "a": parts[1].strip(),
                            "ref": parts[2].strip() if len(parts) > 2 else "", "lecture": f.name,
                            "module": man.get("module") or man.get("title") or f.name, "source": name})
    return out


class Reviews:
    def __init__(self, root: Path, lectures: Path):
        self.root, self.lectures = Path(root), Path(lectures)
        self.root.mkdir(parents=True, exist_ok=True)

    # settings, overrides, log
    def settings(self) -> dict:
        p = self.root / "review_settings.json"
        return {**DEFAULTS, **(json.loads(p.read_text()) if p.exists() else {})}

    def set(self, **kw) -> dict:
        s = self.settings()
        s.update({k: v for k, v in kw.items() if v is not None and k in DEFAULTS})
        if not 5 <= int(s["weekly_minutes"]) <= 1200:
            raise ValueError("weekly minutes must be between 5 and 1200")
        (self.root / "review_settings.json").write_text(json.dumps(s, indent=1))
        return s

    def overrides(self) -> dict:
        p = self.root / "card_overrides.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def override(self, cid: str, **kw) -> dict:
        o = self.overrides()
        o[cid] = {**o.get(cid, {}), **{k: v for k, v in kw.items() if v is not None}}
        (self.root / "card_overrides.json").write_text(json.dumps(o, indent=1))
        return o[cid]

    def log(self) -> dict[str, list[dict]]:
        """Every lecture's reviews.jsonl: {card, ts, pass[, grade, elapsed_s, ...]}."""
        by: dict[str, list[dict]] = {}
        for f in sorted(self.lectures.glob("*/reviews.jsonl")):
            for l in f.read_text().splitlines():
                try:
                    e = json.loads(l)
                    g = e.get("grade") or (GOOD if e["pass"] else AGAIN)
                    by.setdefault(e["card"], []).append({"card_id": e["card"], "t": e["ts"], "grade": int(g),
                                                         "elapsed_s": e.get("elapsed_s")})
                except (ValueError, KeyError, TypeError):
                    continue
        return by

    def cards(self, include_hidden: bool = False) -> list[dict]:
        o = self.overrides()
        out = []
        for c in collect(self.lectures):
            ov = o.get(c["card_id"], {})
            if ov.get("deleted") or (ov.get("suspended") and not include_hidden):
                continue
            out.append({**c, "q": ov.get("q") or c["q"], "a": ov.get("a") or c["a"],
                        "suspended": bool(ov.get("suspended"))})
        return out

    def states(self) -> dict[str, dict]:
        return {cid: replay(ev) for cid, ev in self.log().items() if ev}

    def seconds(self) -> dict:
        """Median seconds per new card, review and lapse from the log, else defaults."""
        by = {"new": [], "review": [], "lapse": []}
        for ev in self.log().values():
            for i, e in enumerate(sorted(ev, key=lambda e: e["t"])):
                if e.get("elapsed_s"):
                    by["new" if i == 0 else ("lapse" if e["grade"] == AGAIN else "review")].append(float(e["elapsed_s"]))
        med = lambda xs: sorted(xs)[len(xs) // 2]
        return {k: (round(med(v), 1) if len(v) >= 10 else SECONDS[k]) for k, v in by.items()}

    # planning
    def daily_caps(self, weekly: float | None = None, class_days: list[int] | None = None) -> list[float]:
        """Minutes per weekday (Mon=0): class days get half a normal day's share."""
        s = self.settings()
        weekly = weekly if weekly is not None else float(s["weekly_minutes"])
        cls = set(class_days if class_days is not None else s["class_weekdays"])
        weights = [0.5 if d in cls else 1.0 for d in range(7)]
        return [weekly * w / sum(weights) for w in weights]

    def plan(self, today: dt.date | None = None, weeks: int = 12, seed: int = 7) -> dict:
        """Highest retention whose simulated weekly minutes (uncapped) stay within 10% of the
        budget in every week, at the planned new cards per week; if none fits, halve new cards
        until one does."""
        s = self.settings()
        budget = float(s["weekly_minutes"])
        today = today or dt.date.today()
        states = self.states()
        cards = self.cards()
        new_pool = [c for c in cards if c["card_id"] not in states]
        started = [states[c["card_id"]] for c in cards if c["card_id"] in states]
        secs = self.seconds()
        npw = int(s["new_per_week"])
        tried = []
        while True:
            for r in RETENTIONS:
                sim = simulate(started, len(new_pool), r, npw, weeks, secs, today, seed=seed)
                tried.append({"retention": r, "new_per_week": npw, "mean_weekly_minutes": sim["mean_weekly_minutes"]})
                if max(sim["weekly_minutes"]) <= budget * 1.1:   # every simulated week within 10%
                    plan = {"made": today.isoformat(), "retention": r, "new_per_week": npw, "budget": budget,
                            "projected_weekly_minutes": sim["mean_weekly_minutes"], "weeks": weeks,
                            "weekly": sim["weekly_minutes"], "tried": tried, "seconds": secs,
                            "parameters": "FSRS-6 defaults"}
                    (self.root / "review_plan.json").write_text(json.dumps(plan, indent=1))
                    return plan
            if npw <= 1:
                plan = {"made": today.isoformat(), "retention": RETENTIONS[-1], "new_per_week": 0, "budget": budget,
                        "projected_weekly_minutes": tried[-1]["mean_weekly_minutes"], "weeks": weeks, "tried": tried,
                        "seconds": secs, "parameters": "FSRS-6 defaults",
                        "note": "reviews of cards already started exceed the budget; the backlog is served lowest recall first"}
                (self.root / "review_plan.json").write_text(json.dumps(plan, indent=1))
                return plan
            npw //= 2

    def current_plan(self, today: dt.date | None = None) -> dict:
        p = self.root / "review_plan.json"
        today = today or dt.date.today()
        if p.exists():
            plan = json.loads(p.read_text())
            if plan.get("budget") == float(self.settings()["weekly_minutes"]) and \
                    (today - dt.date.fromisoformat(plan["made"])).days < 7:
                return plan
        return self.plan(today)

    # sessions
    def spent_today(self, now: dt.datetime) -> float:
        day = now.astimezone().date()
        secs = self.seconds()
        total = 0.0
        for ev in self.log().values():
            evs = sorted(ev, key=lambda e: e["t"])
            for i, e in enumerate(evs):
                if dt.datetime.fromisoformat(e["t"]).astimezone().date() == day:
                    total += float(e.get("elapsed_s") or secs["new" if i == 0 else "review"])
        return total / 60

    def session(self, now: dt.datetime | None = None, cap_minutes: float | None = None) -> dict:
        """Today's cards: due ones, lowest predicted recall first, then new cards, within the
        day's minutes. Nothing about streaks."""
        now = now or dt.datetime.now(dt.timezone.utc)
        plan = self.current_plan(now.astimezone().date())
        r_target = plan["retention"]
        states, secs = self.states(), self.seconds()
        cap = cap_minutes if cap_minutes is not None else self.daily_caps()[now.astimezone().weekday()]
        left = max(0.0, cap - self.spent_today(now)) * 60
        due, new = [], []
        for c in self.cards():
            st = states.get(c["card_id"])
            if st is None:
                new.append(c)
                continue
            if due_at(st, r_target) <= now:
                r = retrievability((now - st["last"]).total_seconds() / 86400, st["s"])
                due.append({**c, "kind": "review", "recall": round(r, 3), "lapses": st["lapses"]})
        due.sort(key=lambda c: c["recall"])
        out, used = [], 0.0
        for c in due:
            cost = secs["review"] + (1 - c["recall"]) * (secs["lapse"] - secs["review"])
            if used + cost > left:
                break
            out.append(c)
            used += cost
        new_today = math.ceil(plan["new_per_week"] / 7) - self.new_done_today(now)
        for c in new[:max(0, new_today)]:
            if used + secs["new"] > left:
                break
            out.append({**c, "kind": "new", "recall": None, "lapses": 0})
            used += secs["new"]
        return {"cards": out, "due": len(due), "overdue_not_shown": len(due) - sum(1 for c in out if c["kind"] == "review"),
                "new_available": len(new), "minutes_cap": round(cap, 1), "minutes_left": round(left / 60, 1),
                "planned_minutes": round(used / 60, 1), "retention": r_target, "new_per_week": plan["new_per_week"]}

    def new_done_today(self, now: dt.datetime) -> int:
        day = now.astimezone().date()
        return sum(1 for ev in self.log().values()
                   if dt.datetime.fromisoformat(min(ev, key=lambda e: e["t"])["t"]).astimezone().date() == day)

    def grade(self, cid: str, got_it: bool, elapsed_s: float | None = None, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now(dt.timezone.utc)
        card = next((c for c in self.cards(include_hidden=True) if c["card_id"] == cid), None)
        if not card:
            raise KeyError("no such card")
        g = GOOD if got_it else AGAIN
        st = step(self.states().get(cid), g, now)
        plan = self.current_plan(now.astimezone().date())
        row = {"card": cid, "ts": now.isoformat(timespec="seconds"), "pass": g >= HARD, "grade": g,
               "elapsed_s": round(elapsed_s, 1) if elapsed_s else None, "stability": round(st["s"], 3),
               "difficulty": round(st["d"], 3)}
        with (self.lectures / card["lecture"] / "reviews.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        return {**row, "card_id": cid, "lecture": card["lecture"], "next_in_days": interval(st["s"], plan["retention"])}

    def stats(self, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now(dt.timezone.utc)
        plan = self.current_plan(now.astimezone().date())
        states = self.states()
        cards = self.cards()
        started = [c for c in cards if c["card_id"] in states]
        due = [c for c in started if due_at(states[c["card_id"]], plan["retention"]) <= now]
        week0 = now.astimezone().date() - dt.timedelta(days=now.astimezone().weekday())
        secs = self.seconds()
        week_min = 0.0
        for ev in self.log().values():
            for i, e in enumerate(sorted(ev, key=lambda e: e["t"])):
                if dt.datetime.fromisoformat(e["t"]).astimezone().date() >= week0:
                    week_min += float(e.get("elapsed_s") or secs["new" if i == 0 else "review"]) / 60
        leeches = [{"card_id": c["card_id"], "q": c["q"], "lapses": states[c["card_id"]]["lapses"], "lecture": c["lecture"]}
                   for c in started if states[c["card_id"]]["lapses"] >= LEECH_LAPSES]
        return {"cards": len(cards), "started": len(started), "new_available": len(cards) - len(started), "due": len(due),
                "weekly_budget": plan["budget"], "minutes_this_week": round(week_min, 1), "retention": plan["retention"],
                "new_per_week": plan["new_per_week"], "projected_weekly_minutes": plan["projected_weekly_minutes"],
                "leeches": leeches, "settings": self.settings(), "parameters": "FSRS-6 defaults",
                "history": sum(len(v) for v in self.log().values())}


# ------------------------------------------------------------------ simulation
def simulate(started: list[dict], new_pool: int, retention: float, new_per_week: int, weeks: int,
             secs: dict, start: dt.date, cap_by_day: list[float] | None = None, seed: int = 7) -> dict:
    """Day-by-day workload with recall drawn from R (fixed seed). Without `cap_by_day` every due
    card is reviewed (the workload the retention costs); with it, each day stops at its cap and
    the backlog is served lowest recall first. Returns weekly minutes and the end backlog."""
    rng = random.Random(seed)
    t0 = dt.datetime.combine(start, dt.time(9), dt.timezone.utc)
    cards = [dict(s) for s in started]
    pool = new_pool
    weekly, per_new = [], new_per_week / 7
    carry = 0.0
    for day in range(weeks * 7):
        now = t0 + dt.timedelta(days=day)
        if day % 7 == 0:
            weekly.append(0.0)
        cap = cap_by_day[now.weekday()] * 60 if cap_by_day else float("inf")
        used = 0.0
        due = [c for c in cards if due_at(c, retention) <= now]
        due.sort(key=lambda c: retrievability((now - c["last"]).total_seconds() / 86400, c["s"]))
        for i, c in enumerate(due):
            r = retrievability((now - c["last"]).total_seconds() / 86400, c["s"])
            got = rng.random() < r
            cost = secs["review"] if got else secs["lapse"]
            if used + cost > cap:
                break
            c.update(step(c, GOOD if got else AGAIN, now))
            used += cost
        carry += per_new
        n_new = min(int(carry), pool)
        carry -= int(carry)
        for _ in range(n_new):
            if used + secs["new"] > cap:
                break
            got = rng.random() < 0.7
            cards.append(step(None, GOOD if got else AGAIN, now))
            pool -= 1
            used += secs["new"]
        weekly[-1] += used / 60
    end = t0 + dt.timedelta(days=weeks * 7)
    backlog = sum(1 for c in cards if due_at(c, retention) <= end)
    return {"weekly_minutes": [round(w, 1) for w in weekly],
            "mean_weekly_minutes": round(sum(weekly) / len(weekly), 1), "backlog_at_end": backlog,
            "cards_started": len(cards), "new_left": pool}
