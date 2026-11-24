"""Onboarding Step 4 - each ERP field's category-specific configuration (ErpField.config).

Plain dicts, checked here so the shape stays predictable for Go Live:

  fixed:    {"value": "N", "per_importer": [{"importer": "TEREX INDIA", "value": "Y"}],
             "hidden": false}
  operator: {"input_type": "text" | "number" | "date" | "dropdown", "choices": ["A", "P"],
             "default": "A"}        (question = display_label, hint = description,
                                     mandatory = the field's own flag)
  other categories: kept as given (configured in later rounds), size-limited.
"""
from __future__ import annotations

import json

INPUT_TYPES = ("text", "number", "date", "dropdown")
MAX_CONFIG_BYTES = 20_000


class ConfigError(ValueError):
    pass


def _text(v, limit=500) -> str:
    s = "" if v is None else str(v).strip()
    if len(s) > limit:
        raise ConfigError(f"A value is too long (max {limit} characters).")
    return s


def clean_config(category: str | None, config) -> dict | None:
    """The config to store for a field of this category - or ConfigError, with a reason."""
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ConfigError("Configuration must be an object.")
    if len(json.dumps(config)) > MAX_CONFIG_BYTES:
        raise ConfigError("Configuration is too large.")
    if category == "fixed":
        rows, seen = [], set()
        for r in config.get("per_importer") or []:
            if not isinstance(r, dict):
                continue
            imp, val = _text(r.get("importer"), 255), _text(r.get("value"))
            if not imp:
                continue
            if imp.lower() in seen:
                raise ConfigError(f"'{imp}' has two different values - keep one.")
            seen.add(imp.lower())
            rows.append({"importer": imp, "value": val})
        return {"value": _text(config.get("value")), "per_importer": rows, "hidden": bool(config.get("hidden"))}
    if category == "operator":
        input_type = config.get("input_type") or "text"
        if input_type not in INPUT_TYPES:
            raise ConfigError(f"Input type must be one of: {', '.join(INPUT_TYPES)}.")
        choices = []
        for c in config.get("choices") or []:
            c = _text(c, 255)
            if c and c not in choices:
                choices.append(c)
        if input_type == "dropdown" and not choices:
            raise ConfigError("A dropdown needs at least one choice.")
        default = _text(config.get("default"))
        if input_type == "dropdown" and default and default not in choices:
            raise ConfigError(f"The default '{default}' is not one of the choices.")
        return {"input_type": input_type, "choices": choices if input_type == "dropdown" else [], "default": default}
    return config


def is_configured(category: str | None, config: dict | None, field) -> bool:
    """Whether a field's configuration is complete enough for Go Live."""
    if category == "fixed":
        return bool(config and (config.get("value") or config.get("per_importer")))
    if category == "operator":
        return bool(config and config.get("input_type"))
    return False
