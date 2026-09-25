"""
Lua & Luau Zero-Copy Lexer and Tokenizer Engine Module.

Centralized high-performance lexer for Epimetheus.
Splits Lua/Luau source into typed tokens without unnecessary substring copying:
  - 'COMMENT_LONG'  (--[[...]], --[=[...]=], etc.)
  - 'COMMENT_SHORT' (--...)
  - 'STRING_LONG'   ([[...]], [=[...]=], etc.)
  - 'STRING_SHORT'  ("...", '...')
  - 'CODE'          (Executable Lua code)

Guarantees strings and comments are preserved and never mutated by AST passes.
"""

from typing import List, Tuple
import re

_RE_LONG_COMMENT_START = re.compile(r"--\[(=*)\[")
_RE_LONG_STRING_START = re.compile(r"\[(=*)\[")


def tokenize_lua_chunks(source: str) -> List[Tuple[str, str]]:
    """
    Split Lua source into (token_type, token_content) pairs.
    Uses index-based regex matching (pos parameter) to avoid string slice allocations.
    """
    tokens: List[Tuple[str, str]] = []
    idx = 0
    length = len(source)
    code_start = 0

    while idx < length:
        # Check comments (-- or --[(=*)\[)
        if source.startswith("--", idx):
            m_comm = _RE_LONG_COMMENT_START.match(source, idx)
            if m_comm:
                if idx > code_start:
                    tokens.append(("CODE", source[code_start:idx]))
                eq_count = len(m_comm.group(1))
                close_delim = "]" + ("=" * eq_count) + "]"
                content_start = idx + len(m_comm.group(0))
                close_pos = source.find(close_delim, content_start)
                if close_pos == -1:
                    tokens.append(("COMMENT_LONG", source[idx:]))
                    return tokens
                end_idx = close_pos + len(close_delim)
                tokens.append(("COMMENT_LONG", source[idx:end_idx]))
                idx = end_idx
                code_start = idx
                continue
            else:
                if idx > code_start:
                    tokens.append(("CODE", source[code_start:idx]))
                end_nl = source.find("\n", idx + 2)
                if end_nl == -1:
                    tokens.append(("COMMENT_SHORT", source[idx:]))
                    return tokens
                tokens.append(("COMMENT_SHORT", source[idx:end_nl]))
                idx = end_nl
                code_start = idx
                continue

        # Check long strings [(=*)\[
        if source.startswith("[", idx):
            m_str = _RE_LONG_STRING_START.match(source, idx)
            if m_str:
                if idx > code_start:
                    tokens.append(("CODE", source[code_start:idx]))
                eq_count = len(m_str.group(1))
                close_delim = "]" + ("=" * eq_count) + "]"
                content_start = idx + len(m_str.group(0))
                close_pos = source.find(close_delim, content_start)
                if close_pos == -1:
                    tokens.append(("STRING_LONG", source[idx:]))
                    return tokens
                end_idx = close_pos + len(close_delim)
                tokens.append(("STRING_LONG", source[idx:end_idx]))
                idx = end_idx
                code_start = idx
                continue

        char = source[idx]
        # Check short string literal '...' or "..."
        if char in ('"', "'"):
            if idx > code_start:
                tokens.append(("CODE", source[code_start:idx]))
            quote = char
            str_start = idx
            idx += 1
            while idx < length:
                ch = source[idx]
                if ch == "\\":
                    idx += 2
                    continue
                if ch == quote:
                    idx += 1
                    break
                idx += 1
            tokens.append(("STRING_SHORT", source[str_start:idx]))
            code_start = idx
            continue

        idx += 1

    if code_start < length:
        tokens.append(("CODE", source[code_start:]))

    return tokens


def join_lua_tokens(tokens: List[Tuple[str, str]]) -> str:
    """Reconstruct Lua source from token pairs."""
    return "".join(content for _, content in tokens)
