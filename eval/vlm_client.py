#!/usr/bin/env python3
"""Minimal client for a vision model behind an OpenAI-compatible server (oMLX).

Shared by the eval harness and lecture_kit so production uses the exact code
path that is evaluated. The one non-obvious job: prove the image actually
reached the model. oMLX silently strips image parts when a model is loaded in
text-only mode, and the model then answers anyway, confidently and wrongly.
"""
import base64, json, mimetypes, os, re, time, urllib.request
from pathlib import Path

BASE = os.environ.get("OMLX_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")
KEY = os.environ.get("OMLX_API_KEY", "")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost only

# One image with this processor (patch 16, merge 2) costs >= ~256 tokens even at
# low resolution; 64 is a safe floor that text-length jitter never reaches.
MIN_IMAGE_TOKENS = 64


def _data_url(path):
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _post(body, timeout):
    req = urllib.request.Request(f"{BASE}/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json",
                                  **({"Authorization": f"Bearer {KEY}"} if KEY else {})})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read())


def ask(model, prompt, images=(), video=None, think=False, max_tokens=900, timeout=900, retries=1):
    """Send text + images (+ optional video part). Returns a dict with the answer,
    token usage and wall latency. Never raises on server errors; sets `error`."""
    parts = [{"type": "image_url", "image_url": {"url": _data_url(p)}} for p in images]
    if video:
        parts.append({"type": "video_url", "video_url": {"url": _data_url(video)}})
    parts.append({"type": "text", "text": prompt})
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": bool(think)},
            "messages": [{"role": "user", "content": parts}]}
    last_err = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = _post(body, timeout)
            msg = r["choices"][0]["message"]
            text = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            u = r.get("usage", {})
            return {"text": text, "reasoning_chars": len(reasoning),
                    "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                    "latency_s": round(time.time() - t0, 2), "error": None}
        except Exception as e:  # timeouts, 5xx, malformed JSON
            last_err = f"{type(e).__name__}: {e}"
    return {"text": "", "prompt_tokens": None, "completion_tokens": None,
            "latency_s": None, "error": last_err}


def text_baseline(model, prompt):
    """prompt_tokens for the same prompt with no media — the reference point."""
    return ask(model, prompt, max_tokens=1)["prompt_tokens"]


def image_received(prompt_tokens, baseline, n_images=1):
    """True only if the server counted image tokens for every image sent."""
    if prompt_tokens is None or baseline is None:
        return False
    return prompt_tokens - baseline >= MIN_IMAGE_TOKENS * max(1, n_images)


# The production slide prompt. lecture_kit and the eval both use this string,
# so a prompt change is automatically an eval change.
def slide_prompt(context=""):
    """Production slide prompt, shared with the eval.

    History: an earlier wording ("…course about: <title>. Describe… If there is
    no image or it is a player UI, reply SKIP") made the model answer SKIP for
    content-rich slides whenever a lecture title was supplied — which is always,
    in production. Content-first wording with SKIP as the explicit exception fixed
    it; the eval now passes a title for every case so this cannot regress unseen.
    """
    ctx = f' The lecture is titled "{context}".' if context else ""
    return ("This is one frame from a lecture video in an AI product management course." + ctx + "\n"
            "If the frame shows slide content — any heading, text, numbers, chart, table or diagram — describe it "
            "so a learner who cannot see it loses nothing: the heading, every bullet or label verbatim, every number "
            "exactly as shown, and for any diagram, chart or table the structure (what the boxes/axes/rows are and "
            "how they relate). Reproduce any table row by row as a markdown table. Do not describe browser tabs, "
            "the address bar, course navigation or video player controls.\n"
            "Only if the frame has no slide content at all — just a speaker, a blank or noisy frame, or bare player "
            "controls — reply exactly: SKIP. If no image is attached, reply exactly: SKIP.")
