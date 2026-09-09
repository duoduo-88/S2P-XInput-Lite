"""Language-aware console output shared by connector submodules.

The active language is process-local runtime state.  It must never require a
config read from a transport callback merely to format a diagnostic message.
"""

import builtins

from localization import translate_text
from runtime_logging import write_interactive_prompt


_cached_language = "zh"


def _normalize_language(language):
    language = str(language or "").strip().lower()
    return language if language in ("zh", "en") else "zh"


def set_current_language(language):
    """Set the process-local language after a non-realtime config read."""
    global _cached_language
    _cached_language = _normalize_language(language)
    return _cached_language


def set_language_from_config(config):
    """Initialize or refresh the cache from an already-loaded config object."""
    try:
        language = config.get("gui", "language", fallback="zh")
    except (AttributeError, TypeError, ValueError):
        language = "zh"
    return set_current_language(language)


def current_language():
    """Return the cached language without filesystem or config access."""
    return _cached_language


def localized_print(*values, **kwargs):
    language = current_language()
    translated = tuple(
        translate_text(value, language) if isinstance(value, str) else value
        for value in values
    )
    return builtins.print(*translated, **kwargs)


def localized_input(prompt=""):
    translated = translate_text(prompt, current_language())
    # Prompts run only from a non-realtime caller.  Their text takes the
    # listener's priority lane and waits only for a bounded delivery attempt;
    # the ordinary AsyncTextStream.flush() deliberately remains non-blocking.
    write_interactive_prompt(translated)
    return builtins.input("")
