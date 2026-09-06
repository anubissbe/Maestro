"""Keep model identifiers with their provider when switching the settings UI."""


def provider_model_update(services, updates, local_default):
    updates = dict(updates)
    previous = services.get("llm_provider", "local")
    provider = updates.get("llm_provider", previous)
    remembered = dict(services.get("llm_models_by_provider") or {})
    old_model = services.get("llm_model_id", "")
    if old_model and not (previous == "local" and old_model.startswith("MiniMax-M")):
        remembered[previous] = old_model
    if provider != previous and "llm_model_id" not in updates:
        default = local_default if provider == "local" else (
            "MiniMax-M3" if provider in {"minimax", "minimax_subscription"} else ""
        )
        updates["llm_model_id"] = remembered.get(provider, default)
    model = updates.get("llm_model_id", old_model)
    if provider == "local" and model.startswith("MiniMax-M"):
        # Repair pre-fix settings too, including switching Local -> Local.
        model = remembered.get("local", local_default)
        if model.startswith("MiniMax-M"):
            model = local_default
        updates["llm_model_id"] = model
    if model:
        remembered[provider] = model
    services["llm_models_by_provider"] = remembered
    return updates
