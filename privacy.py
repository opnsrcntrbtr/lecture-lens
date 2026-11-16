"""EP-5 local-only and public-repo assertions (`lecture doctor --privacy`).

Checks configuration, not network traffic, and changes nothing. Values are never printed:
details name a setting or a host class (loopback or not), never a key.

  endpoints_local      every *_URL / *_BASE_URL setting (environment and .env) points at a loopback host
  eval_local           DeepEval is set to a local model; no hosted-judge or reporting key is configured
  eval_telemetry       DeepEval telemetry is opted out (warn)
  engine_cloud_off     the capture engine runs (or would start) with telemetry off and no cloud sync,
                       cloud transcription, hosted PII backend or LAN discovery
  listeners_loopback   the engine, model server and companion listen on loopback only
  guard_roster_sync    the public repo's guard has a current hashed copy of the roster (warn)

Contract: lectures/doctor_privacy.json  [{check, ok, level, detail}]
The roster hashes are written by `sync_guard()` into the public repo's ignored .guard/ folder:
SHA-256 of each roster name and of each uncommon name token. The guard compares hashes, so it
never reads this overlay and the names are never spelled out.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}
HOSTED_KEYS = re.compile(r"CONFIDENT|API_KEY|LOGIN_KEY|TOKEN", re.I)
ENGINE_BAD = [
    (re.compile(r"--enable-sync\b"), "cloud sync"),
    (re.compile(r"--sync-token\b"), "cloud sync token"),
    (re.compile(r"--deepgram-api-key\b|--audio-transcription-engine[ =]deepgram\b|-a deepgram\b"), "cloud transcription"),
    (re.compile(r"--pii-backend[ =]tinfoil\b"), "hosted PII backend"),
    (re.compile(r"--enable-mdns\b"), "LAN discovery"),
]
ENGINE_ENV_BAD = ("SCREENPIPE_SYNC_TOKEN", "SCREENPIPE_SYNC_PASSWORD", "DEEPGRAM_API_KEY")
OWN_PORTS = {"3030": "capture engine", "8001": "model server", "47110": "companion"}


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def is_loopback(url: str) -> bool:
    host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_endpoints(env: dict) -> dict:
    urls = {k: v for k, v in env.items() if re.search(r"(_BASE_URL|_URL)$", k) and v}
    bad = sorted(k for k, v in urls.items() if not is_loopback(v))
    return {"check": "endpoints_local", "ok": not bad, "level": "fail",
            "detail": f"{len(urls)} endpoint settings, all loopback" if not bad else f"not loopback: {', '.join(bad)}"}


def check_eval(env: dict, deepeval_files: list[Path]) -> list[dict]:
    keys = sorted(k for k in env if k.startswith(("CONFIDENT", "DEEPEVAL")) and HOSTED_KEYS.search(k) and env[k])
    local, file_keys, base_ok = None, [], True
    for f in deepeval_files:
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        file_keys += [k for k, v in d.items() if HOSTED_KEYS.search(k) and v]
        if "USE_LOCAL_MODEL" in d:
            local = str(d["USE_LOCAL_MODEL"]).upper() in ("YES", "TRUE", "1")
        if d.get("LOCAL_MODEL_BASE_URL"):
            base_ok = base_ok and is_loopback(d["LOCAL_MODEL_BASE_URL"])
    problems = []
    if keys or file_keys:
        problems.append(f"hosted key setting present: {', '.join(sorted(set(keys + file_keys)))}")
    if local is False:
        problems.append("DeepEval is not set to a local model")
    if not base_ok:
        problems.append("DeepEval's local model URL is not loopback")
    opt = str(env.get("DEEPEVAL_TELEMETRY_OPT_OUT", "")).upper() in ("YES", "TRUE", "1")
    return [{"check": "eval_local", "ok": not problems, "level": "fail",
             "detail": "; ".join(problems) or ("local judge configured" if local else "no DeepEval config found")},
            {"check": "eval_telemetry", "ok": opt, "level": "warn",
             "detail": "DeepEval telemetry opted out" if opt else
             "set DEEPEVAL_TELEMETRY_OPT_OUT=YES in .env (DeepEval sends anonymous usage events otherwise)"}]


def check_engine(cmdline: str | None, start_script: str, env: dict) -> dict:
    """The running engine's command line, else the start script's arguments."""
    src = cmdline if cmdline else start_script
    found = [what for pat, what in ENGINE_BAD if pat.search(src)]
    found += [f"{k} set" for k in ENGINE_ENV_BAD if env.get(k)]
    if "--disable-telemetry" not in src:
        found.append("telemetry not disabled")
    return {"check": "engine_cloud_off", "ok": not found, "level": "fail",
            "detail": ("running engine" if cmdline else "start script") + (": " + ", ".join(found) if found else
                                                                         ": telemetry off, no cloud features")}


def check_listeners(lsof_out: str) -> dict:
    bad, seen = [], []
    for m in re.finditer(r"TCP (\S+):(\d+) \(LISTEN\)", lsof_out):
        host, port = m.group(1), m.group(2)
        if port in OWN_PORTS:
            seen.append(OWN_PORTS[port])
            if host not in ("127.0.0.1", "[::1]", "localhost"):
                bad.append(f"{OWN_PORTS[port]} on {'all interfaces' if host in ('*', '0.0.0.0', '[::]') else 'a non-loopback address'}")
    return {"check": "listeners_loopback", "ok": not bad, "level": "fail",
            "detail": ", ".join(bad) if bad else (f"loopback only: {', '.join(sorted(set(seen)))}" if seen else "none running")}


# ------------------------------------------------------------------ guard sync
def roster_hashes(names: list[str], common: set[str]) -> list[str]:
    """Hashes the public guard compares against: each full name, and each token of 4+ letters
    that is not a common English word (so ordinary words never block a push)."""
    words = re.compile(r"[a-z0-9]+")
    hs = set()
    for n in names:
        toks = words.findall(n.lower())
        if not toks:
            continue
        if len(toks) > 1:
            hs.add(" ".join(toks))
        hs.update(t for t in toks if len(t) >= 4 and t not in common)
    return sorted(hashlib.sha256(h.encode()).hexdigest() for h in hs)


def sync_guard(public_repo: Path, names: list[str], common: set[str]) -> dict:
    d = Path(public_repo) / ".guard"
    d.mkdir(exist_ok=True)
    hs = roster_hashes(names, common)
    (d / "roster.local.sha256").write_text("# roster hashes, written by the private doctor; never committed\n" +
                                           "\n".join(hs) + "\n")
    return {"hashes": len(hs), "path": ".guard/roster.local.sha256"}


def check_guard_sync(public_repo: Path | None, roster_files: list[Path]) -> dict:
    if not public_repo or not Path(public_repo).exists():
        return {"check": "guard_roster_sync", "ok": True, "level": "warn", "detail": "no public repo configured"}
    f = Path(public_repo) / ".guard" / "roster.local.sha256"
    newest = max((p.stat().st_mtime for p in roster_files if p.exists()), default=0)
    ok = f.exists() and f.stat().st_mtime >= newest
    ignored = subprocess.run(["git", "-C", str(public_repo), "check-ignore", "-q", ".guard/roster.local.sha256"]).returncode == 0
    return {"check": "guard_roster_sync", "ok": ok and ignored, "level": "warn",
            "detail": ("current" if ok else "missing or older than the roster: run lecture doctor --privacy --sync-guard")
            + ("" if ignored else "; .guard/ is not git-ignored in the public repo")}


def run(env: dict, deepeval_files: list[Path], engine_cmdline: str | None, start_script: str, lsof_out: str,
        public_repo: Path | None, roster_files: list[Path]) -> list[dict]:
    return [check_endpoints(env), *check_eval(env, deepeval_files), check_engine(engine_cmdline, start_script, env),
            check_listeners(lsof_out), check_guard_sync(public_repo, roster_files)]
