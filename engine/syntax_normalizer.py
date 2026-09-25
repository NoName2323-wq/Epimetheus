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


from .lua_lexer import tokenize_lua_chunks, join_lua_tokens


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
    expecting_operand = True

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
                expecting_operand = False
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
            if bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
                expecting_operand = False
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                break
            paren_depth = max(0, paren_depth - 1)
            if bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
                expecting_operand = False
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if brace_depth == 0 and bracket_depth == 0 and paren_depth == 0:
                break
            brace_depth = max(0, brace_depth - 1)
            if bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
                expecting_operand = False
        elif bracket_depth == 0 and paren_depth == 0 and brace_depth == 0:
            if char in ";,\n\r":
                break
            if char.isspace():
                if expecting_operand:
                    idx += 1
                    continue
                # After operand, check if next non-space is a continuing binary operator
                look = idx
                while look < length and content[look].isspace():
                    if content[look] in "\n\r":
                        break
                    look += 1
                if look >= length or content[look] in ";,\n\r":
                    break
                rest = content[look:]
                is_binop = False
                op_len = 0
                for op in ("..", "==", "~=", "<=", ">=", "+", "-", "*", "/", "%", "^", "<", ">"):
                    if rest.startswith(op) and not rest.startswith("--"):
                        is_binop = True
                        op_len = len(op)
                        break
                if not is_binop:
                    m_kw = re.match(r"^(?:and|or)\b", rest)
                    if m_kw:
                        is_binop = True
                        op_len = len(m_kw.group(0))

                if not is_binop:
                    break
                else:
                    expecting_operand = True
                    idx = look + op_len
                    continue
            else:
                if char.isalnum() or char in ("_", "."):
                    expecting_operand = False

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


def _consume_luau_type(code: str, start_idx: int) -> int:
    """
    Consumes a Luau type expression starting at start_idx.
    Properly handles:
        - Function types: (number) -> string, (string, number) -> boolean, () -> ()
        - Generic functions and types: <T>(T) -> T, Array<Map<string, number>>
        - Table types: { [string]: number }, { x: number }
        - Union & intersection: number | string, Foo & Bar
        - Optional types: string?, ((number) -> string)?
        - Parenthesized casts and method calls: (val :: any):Method()
    """
    length = len(code)
    idx = start_idx
    while idx < length and code[idx] in " \t":
        idx += 1

    if idx >= length or code[idx] in "\r\n;":
        return idx

    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    angle_depth = 0

    while idx < length:
        ch = code[idx]

        # Check closing delimiters that may match an outer expression
        if ch == ')':
            if paren_depth == 0 and brace_depth == 0 and bracket_depth == 0 and angle_depth == 0:
                break
            paren_depth = max(0, paren_depth - 1)
            idx += 1
            continue
        elif ch == '}':
            if brace_depth == 0 and paren_depth == 0 and bracket_depth == 0 and angle_depth == 0:
                break
            brace_depth = max(0, brace_depth - 1)
            idx += 1
            continue
        elif ch == ']':
            if bracket_depth == 0 and paren_depth == 0 and brace_depth == 0 and angle_depth == 0:
                break
            bracket_depth = max(0, bracket_depth - 1)
            idx += 1
            continue
        elif ch == '>':
            if angle_depth == 0 and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
                break
            angle_depth = max(0, angle_depth - 1)
            idx += 1
            continue

        # Opening delimiters
        if ch == '(':
            paren_depth += 1
            idx += 1
            continue
        elif ch == '{':
            brace_depth += 1
            idx += 1
            continue
        elif ch == '[':
            bracket_depth += 1
            idx += 1
            continue
        elif ch == '<':
            angle_depth += 1
            idx += 1
            continue

        # If inside any nested structure, consume until closed
        if paren_depth > 0 or brace_depth > 0 or bracket_depth > 0 or angle_depth > 0:
            idx += 1
            continue

        # Top-level (depth 0): check for statement separators
        if ch in (',', ';', '\n', '\r'):
            break

        # Check for Luau type continuation operators: '->', '|', '&', '?'
        if code.startswith('->', idx):
            idx += 2
            while idx < length and code[idx] in " \t":
                idx += 1
            continue

        if ch in ('|', '&'):
            idx += 1
            while idx < length and code[idx] in " \t":
                idx += 1
            continue

        if ch == '?':
            idx += 1
            continue

        # Identifier chars or dot (e.g. module.Type)
        if ch.isalnum() or ch in ('_', '.'):
            idx += 1
            continue

        # Whitespace: check if followed by a type continuation operator
        if ch in " \t":
            look = idx
            while look < length and code[look] in " \t":
                look += 1
            if look < length:
                if code.startswith('->', look):
                    idx = look + 2
                    while idx < length and code[idx] in " \t":
                        idx += 1
                    continue
                elif code[look] in ('|', '&'):
                    idx = look + 1
                    while idx < length and code[idx] in " \t":
                        idx += 1
                    continue
                elif code[look] == '?':
                    idx = look + 1
                    continue
            # Whitespace not followed by a type continuation operator -> type ends here
            break

        break

    return idx


def _strip_function_type_annotations(code: str) -> str:
    """
    Strips Luau parameter, generic, and return type annotations from function definitions:
        function foo<T>(a: any, b: {x: {y: string}}): {x: {y: string}} -> function foo(a, b)
        function foo(): (number) -> string -> function foo()
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
            idx = _consume_luau_type(code, look + 1)

        last_idx = idx

    return "".join(result)


def _strip_local_type_annotations(code: str) -> str:
    """
    Strips type annotations from local variable declarations:
        local x: number = 10 -> local x = 10
        local x: number, y: string = 1, 2 -> local x, y = 1, 2
        local x: number, y: string -> local x, y
        local cb: (amount: number) -> number = fn -> local cb = fn
        local f: <T>(T) -> T = id -> local f = id
    """
    pattern = re.compile(r"\blocal\s+(?!function\b)")
    result = []
    last_idx = 0
    length = len(code)

    while True:
        m = pattern.search(code, last_idx)
        if not m:
            result.append(code[last_idx:])
            break

        stmt_start = m.start()
        result.append(code[last_idx:stmt_start])

        idx = m.end()
        var_chunks = []
        curr_chunk_start = idx

        paren_depth = 0
        brace_depth = 0
        bracket_depth = 0
        angle_depth = 0
        ended_with_equal = False
        end_idx = length

        while idx < length:
            ch = code[idx]

            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth = max(0, paren_depth - 1)
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
            elif paren_depth == 0 and brace_depth == 0 and bracket_depth == 0 and angle_depth == 0:
                if ch == ',':
                    var_chunks.append(code[curr_chunk_start:idx].strip())
                    curr_chunk_start = idx + 1
                elif ch == '=':
                    ended_with_equal = True
                    var_chunks.append(code[curr_chunk_start:idx].strip())
                    end_idx = idx
                    break
                elif ch in (';', '\n', '\r'):
                    var_chunks.append(code[curr_chunk_start:idx].strip())
                    end_idx = idx
                    break

            idx += 1

        if idx >= length and curr_chunk_start < length:
            var_chunks.append(code[curr_chunk_start:].strip())
            end_idx = length

        cleaned_vars = []
        for chunk in var_chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            m_var = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)", chunk)
            if m_var:
                cleaned_vars.append(m_var.group(1))

        if cleaned_vars:
            result.append(f"local {', '.join(cleaned_vars)}")
            if ended_with_equal:
                result.append(" =")
                last_idx = end_idx + 1
            else:
                last_idx = end_idx
        else:
            result.append(code[stmt_start:end_idx])
            last_idx = end_idx

    return "".join(result)


def _strip_type_casts(code: str) -> str:
    """
    Strips Luau type cast operator expressions:
        expr :: Type -> expr
        (val :: any):Method() -> (val):Method()
        local x = value :: (number) -> string -> local x = value
        local y = value :: (string, number) -> boolean -> local y = value
    """
    pattern = re.compile(r"::")
    result = []
    last_idx = 0

    while True:
        m = pattern.search(code, last_idx)
        if not m:
            result.append(code[last_idx:])
            break

        cast_pos = m.start()
        # Keep code before '::', trimming trailing spaces before '::'
        result.append(code[last_idx:cast_pos].rstrip(" \t"))
        last_idx = _consume_luau_type(code, m.end())

    return "".join(result)


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
    """Restores masked strings and comments in a single pass."""
    if not mapping:
        return masked_source
    sample_key = next(iter(mapping.keys()))
    m_pref = re.search(r"(__EPI_[0-9a-fA-F]+_)", sample_key)
    if m_pref:
        prefix = re.escape(m_pref.group(1))
        pattern = re.compile(rf"--\[\[{prefix}CMTL_\d+__\]\]|--{prefix}CMT_\d+__|{prefix}STR_\d+__")
        return pattern.sub(lambda m: mapping.get(m.group(0), m.group(0)), masked_source)
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
        if not any(operator in content for operator in COMPOUND_ASSIGNMENT_OPERATORS):
            return content

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
                is_simple = bool(re.match(r"^(?:[a-zA-Z0-9_.]+|\d+(?:\.\d+)?|\([^)]+\))$", rhs))
                rhs_formatted = rhs if is_simple else f"({rhs})"
                replacements.append(
                    (lhs_start, rhs_end, f"{lhs} = {lhs} {op_symbol} {rhs_formatted}")
                )
            idx = rhs_end

        if not replacements:
            return content

        parts: List[str] = []
        last_idx = 0
        for start, end, replacement in replacements:
            parts.append(content[last_idx:start])
            parts.append(replacement)
            last_idx = end
        parts.append(content[last_idx:])
        return "".join(parts)

    def strip_type_annotations(self, code_chunk: str) -> str:
        """
        Strips Luau type annotations and type casts from code:
            - type Foo = ... (single or multiline)
            - function foo<T>(a: any, b: number): boolean
            - local x: number = 10
            - local cb: (amount: number) -> number
            - local z: Vector3
            - expr :: Type (type cast operator)
        """
        chunk = code_chunk
        if "type" in chunk:
            chunk = _strip_type_aliases(chunk)
        if "function" in chunk and (":" in chunk or "<" in chunk):
            chunk = _strip_function_type_annotations(chunk)
        if "local" in chunk and ":" in chunk:
            chunk = _strip_local_type_annotations(chunk)
        if "::" in chunk:
            chunk = _strip_type_casts(chunk)
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
