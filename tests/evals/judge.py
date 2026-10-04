"""Local oMLX judge for DeepEval.

Why a custom model instead of `deepeval set-local-model`: the judge
(Swift-Qwen3.8-27B) has thinking enabled server-side with no reasoning parser,
so its <think> text would land inside the JSON DeepEval parses. This class
turns thinking off per request, strips any leftover reasoning, and parses the
first JSON object robustly. Nothing leaves 127.0.0.1.
"""
import json
import os
import re
import time
import urllib.request

from deepeval.models import DeepEvalBaseLLM

BASE = os.environ.get("OMLX_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")
JUDGE_MODEL = os.environ.get("OMLX_JUDGE_MODEL", "Swift-Qwen3.8-27b-oQ4e-mtp")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost only


def _first_json(text):
    """Return the first complete JSON object/array in text."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON in judge output: {text[:200]!r}")


class OmlxJudge(DeepEvalBaseLLM):
    def __init__(self, model=JUDGE_MODEL, temperature=0.0, max_tokens=2048, timeout=600):
        self.model_name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.calls = 0
        self.seconds = 0.0

    def load_model(self):
        return self.model_name

    def _chat(self, prompt, want_json):
        body = {
            "model": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt + (
                "\n\nRespond with valid JSON only, no prose." if want_json else "")}],
        }
        req = urllib.request.Request(f"{BASE}/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        t0 = time.time()
        with _OPENER.open(req, timeout=self.timeout) as r:
            out = json.loads(r.read())["choices"][0]["message"]["content"] or ""
        self.calls += 1
        self.seconds += time.time() - t0
        return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()

    def generate(self, prompt, schema=None):
        text = self._chat(prompt, want_json=schema is not None)
        if schema is None:
            return text
        last = None
        for attempt in range(2):  # one repair attempt on malformed JSON
            try:
                return schema.model_validate(_first_json(text))
            except Exception as e:  # noqa: BLE001 — surface after retry
                last = e
                text = self._chat(prompt + f"\n\nYour previous reply was not valid for the required "
                                  f"JSON schema ({e}). Reply again with JSON only.", want_json=True)
        raise last

    async def a_generate(self, prompt, schema=None):
        # oMLX serves one request at a time well on this machine; stay sequential.
        return self.generate(prompt, schema)

    def get_model_name(self):
        return f"oMLX:{self.model_name}"


JUDGE = OmlxJudge()
