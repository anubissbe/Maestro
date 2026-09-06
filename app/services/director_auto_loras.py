"""Automatic additions are bounded and assessed against the user's whole selection."""
import math
import re

from services.lora_suggestions import suggest_loras


def select_addition(catalog, selected, prompt, model, images, generate, phases=1):
    names = list(selected.get("activated_loras") or [])
    by_name = {row["filename"]: row for row in catalog}
    if any(name not in by_name for name in names):
        raise ValueError("An active LoRA is unavailable; automatic combinations cannot be assessed")
    active = [by_name[name] for name in names]
    active_ids = {row.get("lora_id") for row in active if row.get("lora_id")}
    candidates = [row for row in catalog if row["filename"] not in names
                  and row.get("lora_id") not in active_ids and not row.get("managed")]
    if not candidates:
        return selected, None
    suggestions = suggest_loras(candidates, prompt, model, images, generate, active, automatic=True)
    # Suggestions are alternatives, not a tested stack. At most one addition
    # per model means every new combination was assessed against all members.
    choice = next((row for row in suggestions if not row["conflicts"]
                   and not row["warnings"] and row["assessment_complete"]), None)
    if choice is None:
        return selected, None
    row = by_name[choice["filename"]]
    rec = row.get("recommended_weights") or {}
    phase_recs = {p.get("phase"): p for p in rec.get("phases", []) if isinstance(p, dict)}
    weights = []
    for i in range(max(1, phases)):
        value = phase_recs.get(i + 1, {}).get("default", rec.get("default", 0.8))
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            value = 0.8
        weights.append(max(0.0, min(2.0, float(value))))
    multipliers = (selected.get("loras_multipliers") or "").split()
    # Preserve each existing schedule, including multi-phase schedules.
    multipliers = [multipliers[i] if i < len(multipliers) else "1" for i in range(len(names))]
    multipliers.append(";".join(str(w) for w in weights))
    updated = {**selected, "activated_loras": [*names, choice["filename"]],
               "loras_multipliers": " ".join(multipliers),
               "loraWeights": {**(selected.get("loraWeights") or {}), choice["filename"]: weights}}
    return updated, choice


def append_keywords(prompt, words):
    for word in words:
        word = word.strip()
        if word and not re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", prompt, re.IGNORECASE):
            prompt = prompt.rstrip() + (", " if prompt.strip() else "") + word
    return prompt


def apply_keywords(plans, selections):
    """Apply only automatic choices, to their own model's actual render prompts."""
    for mode, choice in selections.items():
        if not choice:
            continue
        words = choice.get("trained_words") or []
        for plan in plans:
            field = mode + "_prompt"
            if plan.get(field):
                plan[field] = append_keywords(plan[field], words)
            extra = "keyframe_prompts" if mode == "image" else "window_prompts"
            if isinstance(plan.get(extra), list):
                plan[extra] = [append_keywords(p, words) if isinstance(p, str) else p for p in plan[extra]]
    return plans
