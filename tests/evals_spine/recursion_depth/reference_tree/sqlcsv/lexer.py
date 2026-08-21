# module-kind: tests
"""Turn a statement into tokens."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlcsv.errors import InputError

_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "select",
        "distinct",
        "from",
        "where",
        "group",
        "by",
        "having",
        "order",
        "asc",
        "desc",
        "limit",
        "offset",
        "and",
        "or",
        "not",
        "is",
        "null",
        "as",
        "inner",
        "left",
        "join",
        "on",
    }
)

_SYMBOLS: Final[tuple[str, ...]] = (
    "<=",
    ">=",
    "!=",
    "<>",
    "=",
    "<",
    ">",
    ",",
    "(",
    ")",
    "*",
    ".",
)


class TokenKind(StrEnum):
    """What a token is."""

    KEYWORD = "keyword"
    IDENT = "ident"
    NUMBER = "number"
    STRING = "string"
    SYMBOL = "symbol"
    END = "end"


@dataclass(frozen=True)
class Token:
    """One lexed token.

    Attributes:
        kind: The token's category.
        text: Its source text, lowercased for a keyword.
        value: The decoded value for a literal.
    """

    kind: TokenKind
    text: str
    value: object = None


def tokenize(source: str) -> list[Token]:
    """Lex *source* into tokens.

    Args:
        source: The statement text.

    Returns:
        The tokens, terminated by an END token.

    Raises:
        InputError: A string literal is unterminated, or a character is not
            part of the language.
    """
    tokens: list[Token] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if char == "'":
            literal, index = _read_string(source, index)
            tokens.append(Token(TokenKind.STRING, literal, literal))
            continue
        if char.isdigit() or (
            char == "-" and index + 1 < length and source[index + 1].isdigit()
        ):
            token, index = _read_number(source, index)
            tokens.append(token)
            continue
        if char.isalpha() or char == "_":
            word, index = _read_word(source, index)
            lowered = word.lower()
            if lowered in _KEYWORDS:
                tokens.append(Token(TokenKind.KEYWORD, lowered))
            else:
                tokens.append(Token(TokenKind.IDENT, word))
            continue
        symbol = _read_symbol(source, index)
        if symbol is None:
            msg = f"unexpected character {char!r} at position {index}"
            raise InputError(msg)
        tokens.append(Token(TokenKind.SYMBOL, symbol))
        index += len(symbol)
    tokens.append(Token(TokenKind.END, ""))
    return tokens


def _read_string(source: str, start: int) -> tuple[str, int]:
    """Read a single-quoted literal, doubled quotes standing for one.

    Returns:
        The decoded value and the index just past the closing quote.

    Raises:
        InputError: The literal is unterminated.
    """
    index = start + 1
    parts: list[str] = []
    while index < len(source):
        char = source[index]
        if char == "'":
            if index + 1 < len(source) and source[index + 1] == "'":
                parts.append("'")
                index += 2
                continue
            return "".join(parts), index + 1
        parts.append(char)
        index += 1
    msg = "unterminated string literal"
    raise InputError(msg)


def _read_number(source: str, start: int) -> tuple[Token, int]:
    """Read an integer or decimal literal, with an optional leading minus.

    Returns:
        The token and the index just past it.
    """
    index = start + 1 if source[start] == "-" else start
    seen_dot = False
    while index < len(source):
        char = source[index]
        if char.isdigit():
            index += 1
            continue
        if (
            char == "."
            and not seen_dot
            and index + 1 < len(source)
            and source[index + 1].isdigit()
        ):
            seen_dot = True
            index += 1
            continue
        break
    text = source[start:index]
    value: object = float(text) if seen_dot else int(text)
    return Token(TokenKind.NUMBER, text, value), index


def _read_word(source: str, start: int) -> tuple[str, int]:
    """Read an identifier or keyword.

    Returns:
        The word and the index just past it.
    """
    index = start
    while index < len(source) and (source[index].isalnum() or source[index] == "_"):
        index += 1
    return source[start:index], index


def _read_symbol(source: str, index: int) -> str | None:
    """Match the longest symbol at *index*.

    Returns:
        The symbol, or ``None`` when nothing matches.
    """
    for symbol in _SYMBOLS:
        if source.startswith(symbol, index):
            return symbol
    return None
