"""NG-4 acceptance: compare each lecture's agenda_map.json with the hand-made key in
eval/golden/agenda_key_<day>.json (status per part; start of the longest run within the
key's tolerance). Keys marked 'proposed' are reported but flagged as unconfirmed.

  python3 tools/agenda_bench.py [--rebuild]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def _m(t: str) -> int:
    h, m = t[:5].split(":")
    return int(h) * 60 + int(m)


def compare(m: dict, key: dict) -> dict:
    by = {r["item_id"]: r for r in m["parts"]}
    rows = []
    for k in key["parts"]:
        r = by.get(k["item_id"], {})
        ok = r.get("status") == k["status"]
        t_err = None
        if k.get("start") and r.get("runs"):
            longest = max(r["runs"], key=lambda ab: _m(ab[1]) - _m(ab[0]))
            t_err = abs(_m(longest[0]) - _m(k["start"]))
            ok = ok and t_err <= k.get("tolerance_min", 10)
        rows.append({"item_id": k["item_id"], "want": k["status"], "got": r.get("status"), "start_error_min": t_err, "ok": ok})
    return {"parts": rows, "all_ok": all(r["ok"] for r in rows), "key_status": key.get("status")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args(argv)
    import lecture_kit as lk
    out = []
    for kf in sorted((HERE / "eval" / "golden").glob("agenda_key_*.json")):
        key = json.loads(kf.read_text())
        for folder in sorted(lk.LECTURES.glob(f"{key['day']}-*")):
            if not (folder / "manifest.json").exists() or not (folder / "notes.md").exists():
                continue
            mf = folder / "agenda_map.json"
            if a.rebuild or not mf.exists():
                lk.agenda_map(folder, notes=False)
            if mf.exists():
                out.append({"day": key["day"], **compare(json.loads(mf.read_text()), key)})
            break
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
