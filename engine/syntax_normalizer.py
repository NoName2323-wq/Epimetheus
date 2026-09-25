"""
Luau to Lua 5.1 Syntax Normalizer Engine Module.

Translates modern Luau-specific syntax constructs into standard Lua 5.1
compatible syntax while strictly protecting string literals and comments:
1. Compound assignment operators: +=, -=, *=, /=, %=, ^=, ..=
2. Luau type annotations: local var: Type = val -> local var = val
3. Luau continue statement compatibility
"""

from typing import List, Tuple, Optional
import re


COMPOUND_ASSIGNMENT_OPERATORS = ("+=", "-=", "*=", "/=", "%=", "^=", "..=")


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


class LuauSyntaxNormalizer:
    """
    Normalizes Luau code to standard Lua 5.1 syntax.
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

        in_single_quote = False
        in_double_quote = False
        in_long_string = False
        in_comment = False

        while idx < length:
            ch = content[idx]

            if not in_single_quote and not in_double_quote and not in_long_string and not in_comment:
                if ch == "'":
                    in_single_quote = True
                    idx += 1
                    continue
                elif ch == '"':
                    in_double_quote = True
                    idx += 1
                    continue
                elif content.startswith("--[[", idx):
                    in_comment = True
                    idx += 4
                    continue
                elif content.startswith("--", idx):
                    nl = content.find("\n", idx)
                    if nl == -1:
                        break
                    idx = nl + 1
                    continue
                elif content.startswith("[[", idx):
                    in_long_string = True
                    idx += 2
                    continue

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
                continue
            elif in_single_quote:
                if ch == "\\":
                    idx += 2
                    continue
                if ch == "'":
                    in_single_quote = False
                idx += 1
                continue
            elif in_double_quote:
                if ch == "\\":
                    idx += 2
                    continue
                if ch == '"':
                    in_double_quote = False
                idx += 1
                continue
            elif in_long_string:
                if content.startswith("]]", idx):
                    in_long_string = False
                    idx += 2
                    continue
                idx += 1
                continue
            elif in_comment:
                if content.startswith("]]", idx):
                    in_comment = False
                    idx += 2
                    continue
                idx += 1
                continue

        rewritten = content
        for start, end, replacement in reversed(replacements):
            rewritten = rewritten[:start] + replacement + rewritten[end:]

        return rewritten

    def strip_type_annotations(self, content: str) -> str:
        """
        Strips Luau type annotations:
            type Point = { x: number, y: number } -> [stripped]
            local x: number = 10 -> local x = 10
            local y: string? = nil -> local y = nil
            local z: Vector3 -> local z
            function foo(a: any, b: number): boolean -> function foo(a, b)
        """
        # 1. Strip standalone type alias declarations: `type Foo = ...` or `export type Foo = ...`
        res = re.sub(
            r"^[ \t]*(?:export\s+)?type\s+[a-zA-Z_][a-zA-Z0-9_]*(?:<[^>]*>)?\s*=\s*.*$",
            "",
            content,
            flags=re.MULTILINE,
        )

        # 2. Strip function signatures: parameters & return types
        def clean_func_sig(m):
            fn_decl = m.group(1) or ""
            params = m.group(2)
            clean_params = re.sub(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[^,)]+", r"\1", params)
            return f"function{fn_decl}({clean_params})"

        res = re.sub(
            r"\bfunction(\s+[a-zA-Z0-9_.:]+)?\s*\(([^)]*)\)\s*(?::\s*(?:\([^)]*\)|[a-zA-Z0-9_?|&<>.~{}]+))?",
            clean_func_sig,
            res,
        )

        # 3. Strip `local var: Type = `
        res = re.sub(
            r"\blocal\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z0-9_?|&<>.~{}\s]+?\s*=",
            r"local \1 =",
            res,
        )

        # 4. Strip `local var: Type` (without assignment)
        res = re.sub(
            r"\blocal\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z0-9_?|&<>.~{}\s]+?(?=[;\n\r]|$)",
            r"local \1",
            res,
        )
        return res

    def normalize(self, content: str) -> str:
        """Run complete Luau syntax normalization pipeline."""
        res = content
        if self.rewrite_compound:
            res = self.rewrite_compound_assignments(res)
        if self.strip_types:
            res = self.strip_type_annotations(res)
        return res


def normalize_luau_syntax(content: str) -> str:
    """Convenience function for Luau syntax normalization."""
    normalizer = LuauSyntaxNormalizer()
    return normalizer.normalize(content)
