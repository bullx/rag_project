"""
Text quality helpers — reject garbage chunks from broken PDF parsing.
"""

import re
import string


def is_readable_text(text: str, min_len: int = 50) -> bool:
    """
    Return True if text looks like real readable content.
    Rejects binary garbage from broken PDF extraction.
    """
    text = text.strip()
    if len(text) < min_len:
        return False

    printable = sum(1 for c in text if c in string.printable)
    if printable / len(text) < 0.85:
        return False

    letters = sum(1 for c in text if c.isalpha())
    if letters < max(20, min_len * 0.25):
        return False

    # Too many isolated symbols / PDF artifact patterns
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
        return False

    words = re.findall(r"[A-Za-z]{3,}", text)
    if len(words) < 5:
        return False

    return True
