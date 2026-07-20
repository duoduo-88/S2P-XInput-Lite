"""Language-aware console output shared by connector submodules."""

import builtins

from config_utils import CONFIG_PATH, load_config
from localization import translate_text


def current_language():
    try:
        language = load_config(CONFIG_PATH).get(
            "gui", "language", fallback="zh"
        ).lower()
    except Exception:
        language = "zh"
    return language if language in ("zh", "en") else "zh"


def localized_print(*values, **kwargs):
    language = current_language()
    translated = tuple(
        translate_text(value, language) if isinstance(value, str) else value
        for value in values
    )
    return builtins.print(*translated, **kwargs)


def localized_input(prompt=""):
    return builtins.input(translate_text(prompt, current_language()))
