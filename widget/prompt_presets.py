"""Translation prompt preset normalization and persistence helpers."""
from __future__ import annotations

from widget.api_client import DEFAULT_TRANSLATE_PROMPT
from widget.settings import WidgetSettings


DEFAULT_PRESET_NAME = "默认翻译"


def normalize_prompt_presets(data: dict) -> list[dict[str, str]]:
    """Return unique, non-empty presets while migrating the legacy prompt."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    raw = data.get("prompt_presets")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if name and prompt and name not in seen:
                result.append({"name": name, "prompt": prompt})
                seen.add(name)
    if not result:
        legacy_prompt = str(data.get("prompt") or "").strip()
        result.append({
            "name": DEFAULT_PRESET_NAME,
            "prompt": legacy_prompt or DEFAULT_TRANSLATE_PROMPT,
        })
    return result


def active_prompt_name(data: dict, presets: list[dict[str, str]]) -> str:
    requested = str(data.get("active_prompt_preset") or "").strip()
    if any(item["name"] == requested for item in presets):
        return requested
    return presets[0]["name"]


def prompt_for_name(presets: list[dict[str, str]], name: str) -> str:
    for item in presets:
        if item["name"] == name:
            return item["prompt"]
    return presets[0]["prompt"] if presets else DEFAULT_TRANSLATE_PROMPT


def save_prompt_presets(settings: WidgetSettings,
                        presets: list[dict[str, str]],
                        active_name: str) -> None:
    normalized = normalize_prompt_presets({"prompt_presets": presets})
    if not any(item["name"] == active_name for item in normalized):
        active_name = normalized[0]["name"]
    data = settings.load()
    data.update({
        "prompt_presets": normalized,
        "active_prompt_preset": active_name,
        # Keep the legacy field for backward compatibility.
        "prompt": prompt_for_name(normalized, active_name),
    })
    settings.save(data)
