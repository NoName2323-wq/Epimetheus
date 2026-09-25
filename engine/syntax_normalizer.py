"""
Luau to Lua 5.1 Syntax Normalizer Engine Module.

Translates modern Luau-specific syntax constructs into standard Lua 5.1
compatible syntax while strictly protecting string literals and comments:
1. Compound assignment operators: +=, -=, *=, /=, %=, ^=, ..=
2. Luau type annotations:
   - Standalone single-line and multiline type aliases: type Foo = { ... }
   - Function parameter type annotations (including complex nested types and generics)
   - Function return type annotations (simple, multiple, table)
   - Local variable type annotations (with and without initial value)
"""

from typing import List, Tuple, Optional, Dict
import re
import uuid


COMPOUND_ASSIGNMENT_OPERATORS = ("+=", "-=", "*=", "/=", "%=", "^=", "..=")


def tokenize_lua_chunks(source: str) -> List[Tuple[str, str]]:
    """
    Split Lua source into (token_type, token_content) pairs.
    Token types:
      - 'COMMENT_LONG' (e.g. --[[...]], --[=[...]=], etc.)
      - 'COMMENT_SHORT' (e.g. --...)
      - 'STRING_LONG' (e.g. [[...]], [=[...]=], etc.)
      - 'STRING_SHORT' (e.g. "...", '...')
      - 'CODE'
    Guarantees strings and comments are never mutated by AST/lexical transformations.
    """
    tokens = []
    idx = 0
    length = len(source)
    code_start = 0

    while idx < length:
        # Check comments (-- or --[(=*)\[)
        if source.startswith("--", idx):
            m_comm = re.match(r"^--\[(=*)\[", source[idx:])
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
            m_str = re.match(r"^\[(=*)\[", source[idx:])
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
        # Check string literal '...' or "..."
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


def _find_compound_lhs_start(content: str, operator_index: int) -> int:
    """Locate the starting character index of the left-hand side identifier/expression."""
    idx = operator_index - 1
    while idx >= 0 and content[idx].isspace():
        idx -= 1

    while idx >= 0 and content[idx] == "]":
        bracket_depth = 1
        idx -= 1
        while idx >= 0 and bracket_depth > 0:
            if content[idx] == "]":
                bracket_depth += 1
            elif content[idx] == "[":
                bracket_depth -= 1
            idx -= 1

    while idx >= 0 and (content[idx].isalnum() or content[idx] == "_"):
        idx -= 1

    while idx >= 0 and content[idx] == ".":
        idx -= 1
        while idx >= 0 and content[idx] == "]":
            bracket_depth = 1
            idx -= 1
            while idx >= 0 and bracket_depth > 0:
                if content[idx] == "]":
                    bracket_depth += 1
                elif content[idx] == "[":
                    bracket_depth -= 1
                idx -= 1
        while idx >= 0 and (content[idx].isalnum() or content[idx] == "_"):
            idx -= 1

    return idx + 1


def _find_compound_rhs_end(content: str, rhs_start: int) -> int:
    """Locate the ending character index of the right-hand side expression."""
    idx = rhs_start
    length = len(content)
    bracket_depth = 0
    paren_depth = 0
    brace_depth = 0
    quote = None

    while idx < length and content[idx].isspace():
        idx += 1

    while idx < length:
        char = content[idx]

        if quote:
            if char == "\\":
                idx += 2
                continue
            if char == quote:
                quote = None
            idx += 1
            continue

        if char in ("'", '"'):
            quote = char
            idx += 1
            continue

        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                break
            paren_depth = max(0, paren_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if brace_depth == 0 and bracket_depth == 0 and paren_depth == 0:
                break
            brace_depth = max(0, brace_depth - 1)
        elif bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
            if char in ";,\n\r" or char.isspace():
                break

        idx += 1

    return idx


def _strip_type_aliases(code: str) -> str:
    """
    Remove standalone type alias statements (single-line or multiline table types):
        type Foo = { ... }
        export type Bar = ...
        type UnionFoo = { x: number } & { y: string }
    """
    pattern = re.compile(r"\b(?:export\s+)?type\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*<[^>]*>)?\s*=")
    result = []
    last_idx = 0
    length = len(code)

    while True:
        m = pattern.search(code, last_idx)
        if not m:
            result.append(code[last_idx:])
            break

        start_pos = m.start()
        result.append(code[last_idx:start_pos])

        idx = m.end()
        while idx < length and code[idx].isspace():
            idx += 1

        brace_depth = 0
        paren_depth = 0
        bracket_depth = 0

        while idx < length:
            ch = code[idx]
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth = max(0, brace_depth - 1)
                if brace_depth == 0 and paren_depth == 0 and bracket_depth == 0:
                    idx += 1
                    look = idx
                    while look < length and code[look].isspace():
                        look += 1
                    if look < length and code[look] in ('&', '|'):
                        idx = look + 1
                        continue
                    while idx < length and code[idx] in " \t":
                        idx += 1
                    if idx < length and code[idx] == ';':
                        idx += 1
                    break
            elif ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth = max(0, paren_depth - 1)
            elif ch == '[':
                bracket_depth += 1
            elif ch == ']':
                bracket_depth = max(0, bracket_depth - 1)
            elif brace_depth == 0 and paren_depth == 0 and bracket_depth == 0:
                if ch in (';', '\n'):
                    look = idx + 1
                    while look < length and code[look].isspace():
                        look += 1
                    if look < length and code[look] in ('&', '|'):
                        idx = look + 1
                        continue
                    if ch == ';':
                        idx += 1
                    break
            idx += 1

        last_idx = idx

    return "".join(result)


def _clean_function_parameters(param_str: str) -> str:
    """
    Given a parameter signature, strips ': Type' annotations from each parameter while
    correctly preserving nested complex types (tables, generics, tuples) until the true
    parameter delimiter.
    """
    clean_params = []
    idx = 0
    length = len(param_str)

    while idx < length:
        while idx < length and param_str[idx].isspace():
            idx += 1
        if idx >= length:
            break

        # Check for vararg `...`
        if param_str.startswith("...", idx):
            clean_params.append("...")
            idx += 3
            while idx < length and param_str[idx] != ',':
                idx += 1
            if idx < length and param_str[idx] == ',':
                idx += 1
            continue

        m = re.match(r"[a-zA-Z_][a-zA-Z0-9_]*", param_str[idx:])
        if not m:
            idx += 1
            continue

        name = m.group(0)
        clean_params.append(name)
        idx += len(name)

        while idx < length and param_str[idx].isspace():
            idx += 1

        if idx < length and param_str[idx] == ':':
            idx += 1
            brace_depth = 0
            paren_depth = 0
            bracket_depth = 0
            angle_depth = 0
            while idx < length:
                ch = param_str[idx]
                if ch == '{':
                    brace_depth += 1
                elif ch == '}':
                    brace_depth = max(0, brace_depth - 1)
                elif ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth = max(0, paren_depth - 1)
                elif ch == '[':
                    bracket_depth += 1
                elif ch == ']':
                    bracket_depth = max(0, bracket_depth - 1)
                elif ch == '<':
                    angle_depth += 1
                elif ch == '>':
                    angle_depth = max(0, angle_depth - 1)
                elif ch == ',':
                    if brace_depth == 0 and paren_depth == 0 and bracket_depth == 0 and angle_depth == 0:
                        break
                idx += 1

        if idx < length and param_str[idx] == ',':
            idx += 1

    return ", ".join(clean_params)


def _strip_function_type_annotations(code: str) -> str:
    """
    Strips Luau parameter, generic, and return type annotations from function definitions:
        function foo<T>(a: any, b: {x: {y: string}}): {x: {y: string}} -> function foo(a, b)
    Supports multiline parameters, generic type arguments, and arbitrarily nested return types.
    """
    pattern = re.compile(r"\bfunction(\s+[a-zA-Z0-9_.:]+)?(?:\s*<[^>]*>)?\s*\(")
    result = []
    last_idx = 0
    length = len(code)

    while True:
        m = pattern.search(code, last_idx)
        if not m:
            result.append(code[last_idx:])
            break

        func_start = m.start()
        fn_name = m.group(1) or ""
        result.append(code[last_idx:func_start])

        # Scan parameters inside balanced (...)
        param_start = m.end()  # right after '('
        idx = param_start
        paren_depth = 1
        brace_depth = 0
        bracket_depth = 0
        angle_depth = 0

        while idx < length and paren_depth > 0:
            ch = code[idx]
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    break
            elif ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth = max(0, brace_depth - 1)
            elif ch == '[':
                bracket_depth += 1
            elif ch == ']':
                bracket_depth = max(0, bracket_depth - 1)
            elif ch == '<':
                angle_depth += 1
            elif ch == '>':
                angle_depth = max(0, angle_depth - 1)
            idx += 1

        raw_params = code[param_start:idx]
        cleaned_params = _clean_function_parameters(raw_params)
        result.append(f"function{fn_name}({cleaned_params})")

        # Now idx is pointing at the closing ')' of the parameter list
        idx += 1  # move past ')'

        # Check if there is a return type annotation: `:`
        look = idx
        while look < length and code[look].isspace():
            look += 1

        if look < length and code[look] == ':':
            idx = look + 1
            while idx < length and code[idx].isspace():
                idx += 1

            if idx < length and code[idx] == '{':
                brace_depth = 1
                idx += 1
                while idx < length and brace_depth > 0:
                    if code[idx] == '{':
                        brace_depth += 1
                    elif code[idx] == '}':
                        brace_depth -= 1
                    idx += 1
            elif idx < length and code[idx] == '(':
                paren_depth = 1
                idx += 1
                while idx < length and paren_depth > 0:
                    if code[idx] == '(':
                        paren_depth += 1
                    elif code[idx] == ')':
                        paren_depth -= 1
                    idx += 1
            else:
                angle_depth = 0
                while idx < length:
                    ch = code[idx]
                    if ch == '<':
                        angle_depth += 1
                    elif ch == '>':
                        angle_depth = max(0, angle_depth - 1)
                    elif angle_depth == 0:
                        if ch.isspace() or ch in (';', '\n', '\r'):
                            break
                        if not (ch.isalnum() or ch in ('_', '.', '?', '|', '&', '~')):
                            break
                    idx += 1

        last_idx = idx

    return "".join(result)


def _strip_local_type_annotations(code: str) -> str:
    """
    Strips type annotations from local variables:
        local x: number = 10 -> local x = 10
        local z: Vector3 -> local z
    """
    # 1. local var: Type =
    res = re.sub(
        r"\blocal\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z0-9_?|&<>.~{}\s]+?\s*=",
        r"local \1 =",
        code,
    )
    # 2. local var: Type (without =)
    res = re.sub(
        r"\blocal\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z0-9_?|&<>.~{}\s]+?(?=[;\n\r]|$)",
        r"local \1",
        res,
    )
    return res


def mask_strings_and_comments(source: str) -> Tuple[str, Dict[str, str]]:
    """
    Replaces string literals and comments with safe identifier placeholders.
    Returns (masked_source, placeholder_map).
    Guarantees zero collisions by choosing a unique prefix absent from source.
    """
    while True:
        prefix = f"__EPI_{uuid.uuid4().hex[:8]}_"
        if prefix not in source:
            break

    tokens = tokenize_lua_chunks(source)
    masked_parts = []
    mapping: Dict[str, str] = {}
    str_counter = 0
    cmt_counter = 0

    for token_type, content in tokens:
        if token_type in ("STRING_SHORT", "STRING_LONG"):
            placeholder = f"{prefix}STR_{str_counter}__"
            str_counter += 1
            mapping[placeholder] = content
            masked_parts.append(placeholder)
        elif token_type == "COMMENT_SHORT":
            placeholder = f"--{prefix}CMT_{cmt_counter}__"
            cmt_counter += 1
            mapping[placeholder] = content
            masked_parts.append(placeholder)
        elif token_type == "COMMENT_LONG":
            placeholder = f"--[[{prefix}CMTL_{cmt_counter}__]]"
            cmt_counter += 1
            mapping[placeholder] = content
            masked_parts.append(placeholder)
        else:
            masked_parts.append(content)

    return "".join(masked_parts), mapping


def unmask_strings_and_comments(masked_source: str, mapping: Dict[str, str]) -> str:
    """Restores masked strings and comments."""
    if not mapping:
        return masked_source
    # Sort placeholders by length descending to prevent partial match collisions
    for placeholder, original in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        masked_source = masked_source.replace(placeholder, original)
    return masked_source


class LuauSyntaxNormalizer:
    """
    Normalizes Luau code to standard Lua 5.1 syntax while preserving string literals and comments.
    """

    def __init__(self, rewrite_compound: bool = True, strip_types: bool = True):
        self.rewrite_compound = rewrite_compound
        self.strip_types = strip_types

    def rewrite_compound_assignments(self, content: str) -> str:
        """
        Rewrites Luau compound assignments (a += b) into standard Lua 5.1 (a = a + b).
        """
        replacements: List[Tuple[int, int, str]] = []
        idx = 0
        length = len(content)

        while idx < length:
            matched_operator = None
            for operator in COMPOUND_ASSIGNMENT_OPERATORS:
                if content.startswith(operator, idx):
                    matched_operator = operator
                    break

            if not matched_operator:
                idx += 1
                continue

            lhs_start = _find_compound_lhs_start(content, idx)
            rhs_start = idx + len(matched_operator)
            rhs_end = _find_compound_rhs_end(content, rhs_start)

            lhs = content[lhs_start:idx].strip()
            rhs = content[rhs_start:rhs_end].strip()
            if lhs and rhs:
                op_symbol = matched_operator[:-1]
                replacements.append(
                    (lhs_start, rhs_end, f"{lhs} = {lhs} {op_symbol} {rhs}")
                )
            idx = rhs_end

        rewritten = content
        for start, end, replacement in reversed(replacements):
            rewritten = rewritten[:start] + replacement + rewritten[end:]

        return rewritten

    def strip_type_annotations(self, code_chunk: str) -> str:
        """
        Strips Luau type annotations from code:
            - type Foo = ... (single or multiline)
            - function foo(a: any, b: number): boolean
            - local x: number = 10
            - local z: Vector3
        """
        chunk = _strip_type_aliases(code_chunk)
        chunk = _strip_function_type_annotations(chunk)
        chunk = _strip_local_type_annotations(chunk)
        return chunk

    def normalize(self, content: str) -> str:
        """
        Run complete Luau syntax normalization pipeline.
        Masks comments and string literals with safe identifier placeholders,
        ensuring literals and comments are never altered while allowing full-document
        compound assignment rewrites and type stripping.
        """
        masked_code, mapping = mask_strings_and_comments(content)
        if self.rewrite_compound:
            masked_code = self.rewrite_compound_assignments(masked_code)
        if self.strip_types:
            masked_code = self.strip_type_annotations(masked_code)
        return unmask_strings_and_comments(masked_code, mapping)


def normalize_luau_syntax(content: str) -> str:
    """Convenience function for Luau syntax normalization."""
    normalizer = LuauSyntaxNormalizer()
    return normalizer.normalize(content)
