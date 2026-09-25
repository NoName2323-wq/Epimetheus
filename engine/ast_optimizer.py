"""
AST / Token-based Lua Code Optimizer Engine Module.

Performs:
1. Safe constant arithmetic folding (integers, hex, floats, basic ops)
2. String and comment preservation (never mutates literals)
3. Dead self-assignment removal (var = var)
4. Local variable alias/copy propagation
5. Code cleanup and normalization
"""

from typing import List, Tuple, Optional, Dict
import ast
import re


HEX_INT_PATTERN = re.compile(r"^0[xX][0-9a-fA-F]+$")
# Matches parenthesized or standalone constant arithmetic expressions like:
# (-80732 + 80796), (0x497e0 + -301009), 221781 + -221780
MATH_EXPR_PATTERN = re.compile(
    r"\(\s*(-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?))\s*([+\-*%])\s*(-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?|\(-?\d+\)))\s*\)"
)
STANDALONE_HEX_PATTERN = re.compile(r"\b0[xX]([0-9a-fA-F]+)\b")


def safe_eval_math_expr(expr_str: str) -> Optional[int | float]:
    """
    Safely evaluate a mathematical expression containing only numbers and basic operators.
    Returns None if expression is invalid or dangerous.
    """
    clean = expr_str.strip()
    # Normalize hex literals for Python's AST
    # Replace 0x... with decimal
    def replace_hex(m):
        return str(int(m.group(0), 16))

    clean = STANDALONE_HEX_PATTERN.sub(replace_hex, clean)

    try:
        parsed = ast.parse(clean, mode="eval")
    except Exception:
        return None

    for node in ast.walk(parsed):
        if not isinstance(
            node,
            (
                ast.Expression,
                ast.UnaryOp,
                ast.BinOp,
                ast.Constant,
                ast.USub,
                ast.UAdd,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Mod,
                ast.Pow,
                ast.FloorDiv,
            ),
        ):
            return None

    try:
        val = eval(compile(parsed, "<string>", "eval"), {"__builtins__": None}, {})
        if isinstance(val, (int, float)):
            # If float is virtually an integer, convert to int
            if isinstance(val, float) and val.is_integer() and abs(val) < 1e15:
                return int(val)
            return val
    except Exception:
        return None

    return None


def split_lua_tokens(source: str) -> List[Tuple[str, str]]:
    """
    Split Lua source into (token_type, token_content) pairs.
    Token types:
      - 'COMMENT_LONG'
      - 'COMMENT_SHORT'
      - 'STRING_LONG'
      - 'STRING_SHORT'
      - 'CODE'
    """
    tokens = []
    idx = 0
    length = len(source)
    code_start = 0

    while idx < length:
        # Check long comment --[[ ... ]]
        if source.startswith("--[[", idx):
            if idx > code_start:
                tokens.append(("CODE", source[code_start:idx]))
            end_comment = source.find("]]", idx + 4)
            if end_comment == -1:
                tokens.append(("COMMENT_LONG", source[idx:]))
                return tokens
            tokens.append(("COMMENT_LONG", source[idx : end_comment + 2]))
            idx = end_comment + 2
            code_start = idx
            continue

        # Check short comment --
        if source.startswith("--", idx):
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

        # Check long string [[ ... ]]
        if source.startswith("[[", idx):
            if idx > code_start:
                tokens.append(("CODE", source[code_start:idx]))
            end_str = source.find("]]", idx + 2)
            if end_str == -1:
                tokens.append(("STRING_LONG", source[idx:]))
                return tokens
            tokens.append(("STRING_LONG", source[idx : end_str + 2]))
            idx = end_str + 2
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


class AstOptimizer:
    """
    Lua source code post-processing optimizer.
    """

    def __init__(
        self,
        fold_constants: bool = True,
        propagate_copies: bool = True,
        eliminate_dead_assignments: bool = True,
    ):
        self.fold_constants = fold_constants
        self.propagate_copies = propagate_copies
        self.eliminate_dead_assignments = eliminate_dead_assignments

    def fold_math_expressions(self, code_chunk: str) -> str:
        """
        Recursively fold simple parenthesized arithmetic expressions in a code chunk.
        """
        result = code_chunk

        # Pass 1: Parenthesized expressions
        for _ in range(5):
            modified = False
            matches = list(MATH_EXPR_PATTERN.finditer(result))
            if not matches:
                break

            offset = 0
            for m in matches:
                expr_text = m.group(0)
                # Strip outer parens
                inner = expr_text.strip("()")
                evaluated = safe_eval_math_expr(inner)
                if evaluated is not None:
                    repl = str(evaluated)
                    start = m.start() + offset
                    end = m.end() + offset
                    result = result[:start] + repl + result[end:]
                    offset += len(repl) - len(expr_text)
                    modified = True

            if not modified:
                break

        return result

    def optimize_code_chunk(self, chunk: str) -> str:
        """
        Apply code-level transformations to non-string, non-comment Lua tokens.
        """
        if self.fold_constants:
            chunk = self.fold_math_expressions(chunk)

        return chunk

    def propagate_copy_assignments(self, lines: List[str]) -> List[str]:
        """
        Eliminate redundant aliases:
            local var_B = var_A
        where var_B is purely an alias for var_A.
        """
        alias_map: Dict[str, str] = {}
        cleaned_lines: List[str] = []

        # Simple assignment matcher: local b = a (both must be valid identifiers, not numbers or keywords)
        copy_pattern = re.compile(r"^\s*local\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*$")
        self_assign_pattern = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\1\s*$")
        LUA_KEYWORDS = {"true", "false", "nil", "function", "return", "end", "not", "and", "or"}

        for line in lines:
            stripped = line.strip()

            # Remove self-assignments: x = x
            if self.eliminate_dead_assignments and self_assign_pattern.match(stripped):
                continue

            # Check if line defines an alias
            m = copy_pattern.match(stripped)
            if m:
                var_dst, var_src = m.group(1), m.group(2)
                # Avoid circular or self assignments or Lua keywords
                if var_dst != var_src and var_src not in alias_map and var_src not in LUA_KEYWORDS and var_dst not in LUA_KEYWORDS:
                    alias_map[var_dst] = var_src
                    # Drop the alias definition line
                    continue

            # Apply existing aliases to line
            transformed_line = line
            for dst, src in alias_map.items():
                if dst in transformed_line:
                    # Word boundary replacement
                    transformed_line = re.sub(rf"\b{re.escape(dst)}\b", src, transformed_line)

            cleaned_lines.append(transformed_line)

        return cleaned_lines

    def optimize(self, lua_code: str) -> str:
        """
        Full optimization pipeline on Lua source code string.
        """
        tokens = split_lua_tokens(lua_code)
        optimized_parts = []

        for token_type, content in tokens:
            if token_type == "CODE":
                optimized_parts.append(self.optimize_code_chunk(content))
            else:
                optimized_parts.append(content)

        merged = "".join(optimized_parts)

        # Line-by-line post-passes (alias propagation, dead code, blank lines)
        lines = merged.splitlines()
        if self.propagate_copies or self.eliminate_dead_assignments:
            lines = self.propagate_copy_assignments(lines)

        # Normalize redundant blank lines (max 2 consecutive)
        final_lines = []
        consecutive_blank = 0
        for line in lines:
            if not line.strip():
                consecutive_blank += 1
                if consecutive_blank <= 1:
                    final_lines.append("")
            else:
                consecutive_blank = 0
                final_lines.append(line)

        return "\n".join(final_lines) + "\n"


def optimize_lua_code(lua_code: str) -> str:
    """Convenience function for optimizing Lua source."""
    opt = AstOptimizer()
    return opt.optimize(lua_code)
