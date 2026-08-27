from __future__ import annotations

import string
import unicodedata
from typing import Iterable, List, Optional, Set, Tuple, Union


class Charsets:
    """Pre-defined character sets for common validation scenarios."""

    LETTERS = string.ascii_letters
    LETTERS_SPACE = string.ascii_letters + " "
    ALPHANUMERIC = string.ascii_letters + string.digits
    ALPHANUMERIC_SPACE = string.ascii_letters + string.digits + " "
    BASIC_PUNCTUATION = ".,!?;:'\"-()[]"

    @staticmethod
    def combine(*charsets: str) -> str:
        """
        Combines multiple charsets into a single string.
        Example: Charsets.combine(Charsets.LETTERS, Charsets.BASIC_PUNCTUATION, "@#")
        """
        return "".join(charsets)


class TextTools:
    """
    Powerful utility class for string validation, error formatting, and cleaning.
    """

    @staticmethod
    def _to_char_set(chars: Union[str, Iterable[str]]) -> Set[str]:
        """Helper to convert a string or an iterable of strings into a set of characters."""
        if isinstance(chars, str):
            return set(chars)

        flat_set = set()
        for item in chars:
            flat_set.update(item)
        return flat_set

    @classmethod
    def get_invalid_chars(
        cls,
        text: str,
        allowed_chars: Optional[Union[str, Iterable[str]]] = None,
        forbidden_chars: Optional[Union[str, Iterable[str]]] = None,
    ) -> List[str]:
        """
        Returns a list of characters present in the string that violate the given rules.
        """
        invalid = []
        if allowed_chars is not None:
            allowed_set = cls._to_char_set(allowed_chars)
            invalid.extend([c for c in text if c not in allowed_set and c not in invalid])

        if forbidden_chars is not None:
            forbidden_set = cls._to_char_set(forbidden_chars)
            invalid.extend([c for c in text if c in forbidden_set and c not in invalid])

        result = []
        for c in invalid:
            if c not in result:
                result.append(c)
        return result

    @classmethod
    def check(
        cls,
        text: str,
        allowed_chars: Optional[Union[str, Iterable[str]]] = None,
        forbidden_chars: Optional[Union[str, Iterable[str]]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Checks if text is valid and returns a (is_valid, invalid_chars_list) tuple.
        """
        invalid_chars = cls.get_invalid_chars(text, allowed_chars, forbidden_chars)
        return not bool(invalid_chars), invalid_chars

    @staticmethod
    def clean_text(text: str) -> str:
        """
        Cleans the string by replacing accented characters with their unaccented equivalents
        (e.g., 'é', 'è', 'ê' become 'e', 'à' becomes 'a', etc.).
        """
        if not text:
            return text

        normalized = unicodedata.normalize("NFD", text)
        return "".join(char for char in normalized if unicodedata.category(char) != "Mn")

    @classmethod
    def force_clean(cls, text: str, allowed_chars: Union[str, Iterable[str]]) -> str:
        """
        Aggressively cleans text by:
        1. Removing accents
        2. Dropping any character that is not in the allowed_chars set.
        """
        if not text:
            return text

        no_accents = cls.clean_text(text)
        allowed_set = cls._to_char_set(allowed_chars)

        return "".join(char for char in no_accents if char in allowed_set)

