"""Lightweight i18n module for Refix.

Usage:
    from i18n import t, set_language, get_language

    set_language("en")  # or "ja"
    text = t("some.key", var="value")
"""

from __future__ import annotations

SUPPORTED_LANGUAGES = ("en", "ja")

_current_language: str = "en"
_registry: dict[str, dict[str, str]] = {}


def set_language(lang: str) -> None:
    """Set the current language. Raises ValueError for unsupported languages."""
    global _current_language
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language: {lang!r}. Must be one of: {SUPPORTED_LANGUAGES}"
        )
    _current_language = lang


def get_language() -> str:
    """Return the current language code."""
    return _current_language


def register(strings: dict[str, dict[str, str]]) -> None:
    """Register translation strings into the global registry.

    Args:
        strings: Dict mapping dot-notation keys to {"en": "...", "ja": "..."} dicts.
    """
    _registry.update(strings)


def t(key: str, **kwargs: object) -> str:
    """Look up a translation string by key and optionally format it.

    Args:
        key: Dot-notation key (e.g. "review_fix.instruction_body").
        **kwargs: Variables to substitute via str.format().

    Returns:
        Translated and formatted string.

    Raises:
        KeyError: If the key is not found in the registry.
    """
    translations = _registry[key]
    lang = _current_language
    text = translations[lang] if lang in translations else translations["en"]
    if kwargs:
        text = text.format(**kwargs)
    return text


# Import submodules to trigger their register() calls at import time
from i18n import prompts as _prompts  # noqa: E402, F401
from i18n import ui_strings as _ui_strings  # noqa: E402, F401
