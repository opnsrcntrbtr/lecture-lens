#!/usr/bin/env python3
"""Content guard: stop private or third-party material from being pushed.

This repository is public. The things it is used *with* are not: course
material, the names of the institutions and people who teach it, captured
lectures, credentials. The guard refuses a push that contains any of them.

What it checks, for the files and commit messages being pushed:

1. Blocked terms. The list is stored only as SHA-256 hashes
   (tools/guard/blocked_terms.sha256), so the public repo never spells out the
   names it must not contain. Text is split into lowercase words; every run of
   1 to 6 words is hashed and compared, and long words are also scanned for
   blocked words embedded inside them.
2. Your own plaintext list, if .guard/terms.local.txt exists (never committed).
3. Paths that must never be published: captured lectures, notes, feedback,
   slide decks, recordings, .env files.
4. Secrets: API keys, tokens, private keys.
5. Personal data: absolute home-directory paths, e-mail addresses.
6. Files over 1 MB.

Usage:
    tools/content_guard.py --all                 every tracked file
    tools/content_guard.py --staged              what is staged for commit
    tools/content_guard.py --range A..B          commits in a range (added lines + messages)
    tools/content_guard.py --pre-push            read the pre-push hook's stdin

Exit code 0 = clean, 1 = findings, 2 = the guard itself could not run.
It never prints the blocked list; it prints the offending text from your files.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_FILE = ROOT / "tools" / "guard" / "blocked_terms.sha256"
ALLOW_FILE = ROOT / "tools" / "guard" / "allow_tokens.txt"
LOCAL_TERMS = ROOT / ".guard" / "terms.local.txt"
MAX_BYTES = 1_000_000
MAX_NGRAM = 6
MIN_EMBEDDED = 5          # shorter blocked words only match as whole words

FORBIDDEN_PATHS = [
    (r"(^|/)lectures/", "captured lecture output"),
    (r"(^|/)notes/", "study notes"),
    (r"(^|/)feedback/", "ratings contain captured text"),
    (r"(^|/)private_tools/", "private tooling"),
    (r"(^|/)\.guard/", "local guard list"),
    (r"(^|/)\.env(\..+)?$", "environment file"),
    (r"(^|/)eval/results/", "eval results contain model output from real sessions"),
    (r"(^|/)session_.*\.(json|md)$", "cases built from a real session"),
    (r"(^|/)(transcript[^/]*\.(md|txt|json)|cards\.tsv|zoom_transcript[^/]*)$", "session artefact"),
    (r"\.(pptx?|key|pdf|docx?|xlsx?)$", "document or slide deck"),
    (r"\.(mp4|mov|m4a|mp3|wav|webm|mkv)$", "recording"),
    (r"\.(sqlite|db|bin)$", "database or binary store"),
]
ALLOWED_PATHS = [r"(^|/)\.env\.example$"]
IMAGE_OK = r"^(website/static/|docs/assets/)"
IMAGE = r"\.(png|jpe?g|gif|heic|webp)$"

SECRETS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\b(sk|rk)-[A-Za-z0-9_-]{20,}", "API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{30,}", "GitHub token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bxox[abprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"(?i)(api|secret|access)[_-]?(key|token)[a-z0-9_]*\s*[:=]\s*['\"]?(?!\$|<|your|xxx|changeme|example|\.\.\.|none|null|\{)[A-Za-z0-9/+_=-]{20,}", "credential assignment"),
]
PERSONAL = [
    (r"/Users/(?!you/|example/|runner/|\$)[A-Za-z0-9._-]+/", "absolute home path"),
    (r"/home/(?!you/|example/|runner/|\$)[A-Za-z0-9._-]+/", "absolute home path"),
    (r"\b[A-Za-z0-9._%+-]+@(?!example\.(com|org|edu)\b|users\.noreply\.github\.com\b|github\.com\b|noreply\.|anthropic\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "e-mail address"),
]
WORD = re.compile(r"[a-z0-9]+")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normalise(term: str) -> str:
    return " ".join(WORD.findall(term.lower()))


class Terms:
    """Blocked terms as hashes, plus an optional local plaintext list."""

    def __init__(self, hash_file: Path = HASH_FILE, allow_file: Path = ALLOW_FILE, local: Path = LOCAL_TERMS):
        self.hashes: set[str] = set()
        self.embedded_lengths: set[int] = set()
        if hash_file.exists():
            for line in hash_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("# embedded-lengths:"):
                    self.embedded_lengths = {int(x) for x in line.split(":", 1)[1].split(",") if x.strip()}
                elif line and not line.startswith("#"):
                    self.hashes.add(line.split()[0])
        self.allow = set()
        if allow_file.exists():
            self.allow = {l.strip().lower() for l in allow_file.read_text().splitlines() if l.strip() and not l.startswith("#")}
        self.local = []
        if local.exists():
            self.local = [normalise(l) for l in local.read_text().splitlines() if l.strip() and not l.startswith("#")]
            self.local = [t for t in self.local if t]
        self._seen: dict[str, bool] = {}

    def _hit(self, phrase: str) -> bool:
        if phrase not in self._seen:
            self._seen[phrase] = sha(phrase) in self.hashes
        return self._seen[phrase]

    def find(self, text: str) -> list[str]:
        """Blocked words or phrases present in `text`, as they appear in it."""
        words = WORD.findall(text.lower())
        found: list[str] = []
        for i in range(len(words)):
            for n in range(1, MAX_NGRAM + 1):
                if i + n > len(words):
                    break
                phrase = " ".join(words[i:i + n])
                if n == 1 and phrase in self.allow:
                    continue
                if self._hit(phrase):
                    found.append(phrase)
            w = words[i]
            if w in self.allow:
                continue
            for size in self.embedded_lengths:        # a blocked word glued inside a longer one
                if size >= MIN_EMBEDDED and len(w) > size:
                    for j in range(len(w) - size + 1):
                        if self._hit(w[j:j + size]):
                            found.append(w)
                            break
        if self.local:
            flat = " " + " ".join(words) + " "
            for t in self.local:
                if (f" {t} " in flat) or (len(t) >= MIN_EMBEDDED and " " not in t and t in flat
                                          and not any(t in a and a in flat for a in self.allow)):
                    found.append(t)
        return sorted(set(found))


def check_path(path: str) -> list[str]:
    if any(re.search(p, path) for p in ALLOWED_PATHS):
        return []
    out = [f"forbidden path ({why})" for pat, why in FORBIDDEN_PATHS if re.search(pat, path, re.I)]
    if re.search(IMAGE, path, re.I) and not re.search(IMAGE_OK, path):
        out.append("image outside website/static (screenshots may show course material)")
    return out


def check_text(text: str, terms: Terms, where: str) -> list[tuple[str, int, str]]:
    findings = []
    for n, line in enumerate(text.splitlines(), 1):
        for hit in terms.find(line):
            findings.append((where, n, f"blocked term: “{hit}”"))
        for pat, why in SECRETS:
            if re.search(pat, line):
                findings.append((where, n, f"possible secret ({why})"))
        for pat, why in PERSONAL:
            m = re.search(pat, line)
            if m:
                findings.append((where, n, f"personal data ({why}): {m.group(0)}"))
    return findings


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"content guard: git {' '.join(args)} failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return r.stdout


def scan_blob(path: str, data: bytes, terms: Terms) -> list[tuple[str, int, str]]:
    findings = [(path, 0, m) for m in check_path(path)]
    findings += [(path, 0, f"blocked term in file name: “{h}”") for h in terms.find(path)]
    if len(data) > MAX_BYTES:
        findings.append((path, 0, f"file is {len(data) // 1024} KB (limit {MAX_BYTES // 1024} KB)"))
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return findings
    return findings + check_text(text, terms, path)


def scan_all(terms: Terms) -> list:
    out = []
    for path in filter(None, git("ls-files", "-z").split("\0")):
        p = ROOT / path
        if p.is_file():
            out += scan_blob(path, p.read_bytes(), terms)
    return out


def scan_staged(terms: Terms) -> list:
    out = []
    for path in filter(None, git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")):
        data = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True).stdout
        out += scan_blob(path, data, terms)
    return out


def scan_commits(revs: list[str], terms: Terms) -> list:
    """Every commit: its message, author, the paths it adds or changes, and the lines it adds."""
    out = []
    for rev in revs:
        short = rev[:9]
        meta = git("show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", rev)
        out += [(f"commit {short} message", n, m) for _, n, m in check_text(meta, terms, "")]
        for path in filter(None, git("diff-tree", "--root", "--no-commit-id", "--name-only", "--diff-filter=ACMR", "-r", "-z", rev).split("\0")):
            data = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True).stdout
            out += [(f"{short}:{p}", n, m) for p, n, m in scan_blob(path, data, terms)]
    return out


def revs_for_push(stdin_lines: list[str], remote: str | None) -> list[str]:
    revs: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, _, remote_sha = parts
        if set(local_sha) == {"0"}:          # deleting a remote ref pushes nothing
            continue
        if set(remote_sha) == {"0"}:         # new branch: everything the remote does not have
            args = ["rev-list", local_sha, "--not", f"--remotes={remote}" if remote else "--remotes"]
        else:
            args = ["rev-list", f"{remote_sha}..{local_sha}"]
        revs += [r for r in git(*args).split() if r not in revs]
    return revs


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse to publish private or third-party material.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range")
    g.add_argument("--pre-push", action="store_true")
    ap.add_argument("--remote", default=None)
    a = ap.parse_args()

    terms = Terms()
    if not terms.hashes and not terms.local:
        print("content guard: no blocked-term list found (tools/guard/blocked_terms.sha256). Refusing to continue.",
              file=sys.stderr)
        return 2

    if a.all:
        findings = scan_all(terms)
    elif a.staged:
        findings = scan_staged(terms)
    elif a.range:
        findings = scan_commits(git("rev-list", a.range).split(), terms)
    else:
        findings = scan_commits(revs_for_push(sys.stdin.read().splitlines(), a.remote), terms)

    if not findings:
        print("content guard: clean")
        return 0
    print(f"content guard: {len(findings)} finding(s). Nothing was pushed.\n", file=sys.stderr)
    for where, line, msg in findings[:200]:
        print(f"  {where}{':' + str(line) if line else ''}  {msg}", file=sys.stderr)
    print("\nReplace names with generic ones (\"the institute\", \"the course portal\"), move private files out of the\n"
          "repository, then commit again. See docs: Project > Content guard.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
