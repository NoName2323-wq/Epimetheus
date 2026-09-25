"""
Prometheus Static Constant Inliner & Proxy Unwrapper Engine Module.

Directly analyzes Lua code to:
1. Detect Prometheus ConstantArray.lua wrapper functions:
     local function wrapper(a) return ARR[a + offset] end
2. Statically resolve and inline constant calls:
     wrapper(123) -> "DecodedConstant"
3. Unwrap Prometheus ProxifyLocals.lua metatable wrappers:
     setmetatable({ [valName] = init }, mt) -> init
"""

from typing import List, Dict, Optional, Tuple, Any
import re
from .ast_optimizer import safe_eval_math_expr
from .lua_lexer import tokenize_lua_chunks as split_lua_tokens
from .static_decoder import StaticConstantDecoder


WRAPPER_FUNC_PATTERN = re.compile(
    r"(?:local\s+)?function\s+([a-zA-Z0-9_]+)\s*\(\s*([a-zA-Z0-9_]+)\s*\)\s*"
    r"return\s+([a-zA-Z0-9_]+)\s*\[\s*\2\s*([+\-])\s*([0-9a-fA-FxX]+|\d+)\s*\]\s*end"
)

WRAPPER_ASSIGN_PATTERN = re.compile(
    r"(?:local\s+)?([a-zA-Z0-9_]+)\s*=\s*function\s*\(\s*([a-zA-Z0-9_]+)\s*\)\s*"
    r"return\s+([a-zA-Z0-9_]+)\s*\[\s*\2\s*([+\-])\s*([0-9a-fA-FxX]+|\d+)\s*\]\s*end"
)

_PROXIFIED_LOCAL_PATTERN = re.compile(
    r"setmetatable\s*\(\s*\{\s*\[\s*[\"'][^\"']+[\"']\s*\]\s*=\s*([^,}]+?)\s*\}\s*,\s*\{[^}]+\}\s*\)"
)


class ConstantInliner:
    """
    Inlines decoded constants into AST/code call sites and unwraps proxy locals.
    """

    def __init__(self, constants: Optional[List[Any]] = None):
        self.constants = constants or []

    def detect_wrapper(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Detects wrapper function definition:
            local function wrapper(arg) return ARR[arg + offset] end
        """
        for pattern in (WRAPPER_FUNC_PATTERN, WRAPPER_ASSIGN_PATTERN):
            m = pattern.search(code)
            if m:
                func_name, arg_name, arr_name, sign, offset_str = m.groups()
                try:
                    offset = int(offset_str, 0)
                except ValueError:
                    val = safe_eval_math_expr(offset_str)
                    if val is None:
                        continue
                    offset = int(val)

                return {
                    "func_name": func_name,
                    "arg_name": arg_name,
                    "arr_name": arr_name,
                    "sign": sign,
                    "offset": offset,
                    "match_span": m.span(),
                }
        return None

    def inline_constants(
        self, code: str, constants: Optional[List[Any]] = None
    ) -> str:
        """
        Replace all wrapper(index_expr) calls with their concrete decoded constant literals.
        """
        wrapper_info = self.detect_wrapper(code)
        if not wrapper_info:
            return code

        const_list = constants if constants is not None else self.constants
        if not const_list:
            # Try to statically decode constants from code if not provided
            decoder = StaticConstantDecoder()
            const_list = decoder.decode_all(code)

        if not const_list:
            return code

        func_name = wrapper_info["func_name"]
        sign = wrapper_info["sign"]
        offset = wrapper_info["offset"]

        tokens = split_lua_tokens(code)
        optimized_parts = []

        call_pattern = re.compile(
            rf"\b{re.escape(func_name)}\s*\(\s*(-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?|\([^)]+\)))\s*\)"
        )

        escaped_cache: Dict[int, str] = {}

        def get_escaped(arr_idx: int) -> str:
            if arr_idx in escaped_cache:
                return escaped_cache[arr_idx]
            c_val = const_list[arr_idx - 1]
            if isinstance(c_val, (bytes, bytearray, str)):
                res = StaticConstantDecoder.escape_for_lua(c_val)
            elif isinstance(c_val, bool):
                res = "true" if c_val else "false"
            elif c_val is None:
                res = "nil"
            else:
                res = str(c_val)
            escaped_cache[arr_idx] = res
            return res

        for token_type, content in tokens:
            if token_type == "CODE" and func_name in content:

                def replace_call(cm):
                    raw_arg = cm.group(1).strip()
                    try:
                        val = int(raw_arg, 0)
                    except ValueError:
                        eval_val = safe_eval_math_expr(raw_arg)
                        if eval_val is None:
                            return cm.group(0)
                        val = int(eval_val)

                    arr_idx = (val + offset) if sign == "+" else (val - offset)
                    # Lua tables are 1-indexed
                    if 1 <= arr_idx <= len(const_list):
                        return get_escaped(arr_idx)

                    return cm.group(0)

                optimized_parts.append(call_pattern.sub(replace_call, content))
            else:
                optimized_parts.append(content)

        return "".join(optimized_parts)

    def unwrap_proxified_locals(self, code: str) -> str:
        """
        Unwrap Prometheus ProxifyLocals.lua local metatable assignments:
            local x = setmetatable({ [secret] = init }, mt) -> local x = init
        """
        if "setmetatable" not in code:
            return code
        return _PROXIFIED_LOCAL_PATTERN.sub(lambda m: m.group(1).strip(), code)


def inline_constants_in_code(
    code: str, constants: Optional[List[Any]] = None
) -> str:
    """Convenience helper to unwrap proxy locals and inline constant array calls in Lua source."""
    inliner = ConstantInliner(constants)
    code = inliner.unwrap_proxified_locals(code)
    return inliner.inline_constants(code)
