"""Swap which model oMLX has loaded (the generator and the judge don't fit together).

    python omlx_swap.py judge      # unload generator, load judge
    python omlx_swap.py generator  # unload judge, load generator (restores daily setup)
    python omlx_swap.py status
    python omlx_swap.py idle [seconds]   # exit 3 if another client is using oMLX

Uses oMLX's local admin API (127.0.0.1 only). Idempotent: loading an already
loaded model is a no-op.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("OMLX_BASE_URL", "http://127.0.0.1:8001/v1").rsplit("/v1", 1)[0]
GEN = os.environ.get("OMLX_MODEL", "Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx")
JUDGE = os.environ.get("OMLX_JUDGE_MODEL", "Swift-Qwen3.8-27b-oQ4e-mtp")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(method, path, timeout=300):
    req = urllib.request.Request(ROOT + path, method=method, data=b"" if method == "POST" else None)
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def loaded():
    return _call("GET", "/api/status").get("loaded_models", [])


def swap(to_load, to_unload, attempts=3):
    """Unload one model, wait until its memory is released, load the other.

    Retries the load: right after an unload oMLX can still be reclaiming
    memory (or another client may have re-requested the old model), which
    shows up as HTTP 500/507 on the first try."""
    t0 = time.time()
    for attempt in range(1, attempts + 1):
        if to_unload in loaded():
            try:
                _call("POST", f"/admin/api/models/{to_unload}/unload")
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 404 = already unloaded
                    raise
            for _ in range(30):  # wait for the unload to land
                if to_unload not in loaded():
                    break
                time.sleep(1)
        if to_load in loaded():
            break
        try:
            _call("POST", f"/admin/api/models/{to_load}/load", timeout=600)
            break
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            print(f"load {to_load} attempt {attempt}: HTTP {e.code} {body}", file=sys.stderr)
            if attempt == attempts:
                sys.exit(f"swap failed after {attempts} attempts (loaded: {loaded()})")
            time.sleep(10 * attempt)
    now = loaded()
    if to_load not in now:
        sys.exit(f"swap failed: {to_load} not loaded (loaded: {now})")
    print(json.dumps({"loaded": now, "seconds": round(time.time() - t0, 1)}))


def idle(seconds=60):
    """True when no other client is using oMLX: no active or waiting requests and no new
    completions over `seconds`. An eval swaps models for an hour or more; once, a
    swap during someone else's work failed their requests (HTTP 507) and errored half of
    the eval's own metrics (409/507), so it is checked first."""
    a = _call("GET", "/api/status")
    time.sleep(seconds)
    b = _call("GET", "/api/status")
    busy = b.get("active_requests", 0) or b.get("waiting_requests", 0) or b.get("total_requests", 0) > a.get("total_requests", 0)
    if busy:
        print(f"oMLX is in use by another client ({b.get('total_requests', 0) - a.get('total_requests', 0)} requests in {seconds}s, "
              f"{b.get('active_requests', 0)} active, loaded {b.get('loaded_models')}); not swapping. "
              "Run again when it is idle, or set EVAL_FORCE=1.", file=sys.stderr)
    return not busy


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "judge":
        swap(JUDGE, GEN)
    elif cmd == "generator":
        swap(GEN, JUDGE)
    elif cmd == "idle":
        sys.exit(0 if idle(int(sys.argv[2]) if len(sys.argv) > 2 else 60) else 3)
    else:
        print(json.dumps({"loaded": loaded(), "generator": GEN, "judge": JUDGE}))
