"""Laya (convaiinnovations/laya, 421M ModernBERT, Apache-2.0) as a DeepEval
System-1 model — a local stand-in for the cloud-only TypeSafe/Jev.

DeepEval's hybrid/system_one eval modes hand the *decision* step of a metric
(e.g. "is this claim supported by the retrieval context?") to a model that
returns calibrated probabilities instead of generated text. Laya answers the
same typed questions (noul / choice / score) in one encoder forward pass, on
this Mac's GPU, in tens of milliseconds.

Limits to keep in mind (see ADR-003):
  * 512-token window per state — long contexts go through predict_long (windowed).
  * The public checkpoint's temperatures are partly invalid (it warns), so its
    confidences are uncalibrated until refit on our own labelled data.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

os.environ.setdefault("USE_TF", "0")

from deepeval.models import DeepEvalBaseSystemOneModel  # noqa: E402
from deepeval.models.system_one.schema import (  # noqa: E402
    ChoiceAnswer, ChoiceQuestion, NoulAnswer, NoulQuestion, ScoreAnswer, ScoreQuestion, SystemOneAnswers,
)

LAYA_ID = os.environ.get("LAYA_MODEL", "convaiinnovations/laya")

SUPPORT_QUESTION = {
    "type": "noul",
    "instructions": "Is the claim fully supported by the context?",
    "criteria": {
        "true": "Everything in the claim, including every number, name and relationship, is stated in the context or follows directly from it.",
        "false": "The claim contradicts the context, changes a number, name or order, or adds something the context does not say.",
    },
}


def _as_text(x: Any) -> str:
    return x if isinstance(x, str) else str(x)


class LayaSystemOne(DeepEvalBaseSystemOneModel):
    def __init__(self, model: str = LAYA_ID, device: Optional[str] = None):
        self._device = device
        self.calls = 0
        self.seconds = 0.0
        super().__init__(model)

    def load_model(self):
        import laya  # heavy import: torch + weights (cached after first download)
        return laya.load(self.name or LAYA_ID, device=self._device)

    # -- raw access, used by the benchmark -------------------------------------
    def predict(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        out = self.model.predict_long(state, questions)
        self.calls += 1
        self.seconds += time.perf_counter() - t0
        return out

    def supported(self, context: str, claim: str) -> float:
        """P(claim is supported by context)."""
        out = self.predict({"context": context, "claim": claim}, {"supported": SUPPORT_QUESTION})
        return float(out["answers"]["supported"]["noul"])

    # -- DeepEval interface ----------------------------------------------------
    @staticmethod
    def _to_laya(q) -> Dict[str, Any]:
        if isinstance(q, NoulQuestion):
            d: Dict[str, Any] = {"type": "noul", "instructions": _as_text(q.instructions)}
            crit = {k: _as_text(v) for k, v in (("true", q.true), ("false", q.false)) if v is not None}
            if crit:
                d["criteria"] = crit
            return d
        if isinstance(q, ChoiceQuestion):
            return {"type": "choice", "instructions": _as_text(q.instructions),
                    "criteria": {k: _as_text(v) for k, v in q.options.items()}}
        if isinstance(q, ScoreQuestion):
            return {"type": "score", "instructions": _as_text(q.instructions),
                    "criteria": [_as_text(v) for v in q.levels]}
        raise TypeError(f"unsupported System-1 question: {type(q).__name__}")

    def decide(self, state: Any, questions: Dict[str, Any]) -> Tuple[SystemOneAnswers, Optional[float]]:
        out = self.predict(state, {k: self._to_laya(q) for k, q in questions.items()})["answers"]
        ans = SystemOneAnswers()
        for k, q in questions.items():
            a = out[k]
            if isinstance(q, NoulQuestion):
                ans.nouls[k] = NoulAnswer(probability=float(a["noul"]))
            elif isinstance(q, ChoiceQuestion):
                ans.choices[k] = ChoiceAnswer(choice=a["choice"], probabilities=a["probabilities"],
                                              confidence=float(a.get("confidence", 0.0)))
            else:
                probs = {int(i): float(p) for i, p in (a.get("probabilities") or {}).items()}
                ans.scores[k] = ScoreAnswer(score=float(a["score"]), probabilities=probs,
                                            confidence=float(a.get("confidence", 0.0)))
        return ans, 0.0  # local: no cost

    async def a_decide(self, state: Any, questions: Dict[str, Any]):
        return self.decide(state, questions)

    def get_model_name(self, *args, **kwargs) -> str:
        return f"laya:{self.name}"
