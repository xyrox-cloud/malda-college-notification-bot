"""
Small text-styling helpers for outgoing bot messages.
"""

from __future__ import annotations

_BOLD_ITALIC_CAP_BASE = 0x1D468   # 𝑨
_BOLD_ITALIC_LOW_BASE = 0x1D482   # 𝒂

_BOLD_ITALIC_MAP = {
    **{chr(ord("A") + i): chr(_BOLD_ITALIC_CAP_BASE + i) for i in range(26)},
    **{chr(ord("a") + i): chr(_BOLD_ITALIC_LOW_BASE + i) for i in range(26)},
}


def bold_italic(text: str) -> str:
    """
    Render ASCII letters as Unicode Mathematical Bold Italic (𝑵𝒐𝒕𝒊𝒇𝒊𝒄𝒂𝒕𝒊𝒐𝒏
    style). Non-letter characters (spaces, punctuation, digits, emoji) pass
    through unchanged.
    """
    return "".join(_BOLD_ITALIC_MAP.get(ch, ch) for ch in text)
