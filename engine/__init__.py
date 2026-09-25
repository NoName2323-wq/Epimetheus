"""
Epimetheus Deobfuscator Engine Package.

Provides modular sub-engines:
- trace_filter: Streaming VM loop & unpacking noise suppression
- ast_optimizer: Constant arithmetic folding, copy propagation & string folding
- static_decoder: Static extraction and decoding of Prometheus Base64/Base85/Mixed string tables & PRNG cipher
- syntax_normalizer: Luau compound assignment & type annotation normalizer
- constant_inliner: Static constant array call inliner & proxy unwrapper
- lua_lexer: Unified zero-copy Lua lexical scanner
"""

from typing import Any

__all__ = [
    "TraceFilter",
    "filter_trace_stream",
    "filter_trace_lines",
    "AstOptimizer",
    "optimize_lua_code",
    "safe_eval_math_expr",
    "StaticConstantDecoder",
    "decode_prometheus_constants",
    "LuauSyntaxNormalizer",
    "normalize_luau_syntax",
    "ConstantInliner",
    "inline_constants_in_code",
    "tokenize_lua_chunks",
    "join_lua_tokens",
]

_LAZY_EXPORTS = {
    "TraceFilter": (".trace_filter", "TraceFilter"),
    "filter_trace_stream": (".trace_filter", "filter_trace_stream"),
    "filter_trace_lines": (".trace_filter", "filter_trace_lines"),
    "AstOptimizer": (".ast_optimizer", "AstOptimizer"),
    "optimize_lua_code": (".ast_optimizer", "optimize_lua_code"),
    "safe_eval_math_expr": (".ast_optimizer", "safe_eval_math_expr"),
    "StaticConstantDecoder": (".static_decoder", "StaticConstantDecoder"),
    "decode_prometheus_constants": (".static_decoder", "decode_prometheus_constants"),
    "LuauSyntaxNormalizer": (".syntax_normalizer", "LuauSyntaxNormalizer"),
    "normalize_luau_syntax": (".syntax_normalizer", "normalize_luau_syntax"),
    "ConstantInliner": (".constant_inliner", "ConstantInliner"),
    "inline_constants_in_code": (".constant_inliner", "inline_constants_in_code"),
    "tokenize_lua_chunks": (".lua_lexer", "tokenize_lua_chunks"),
    "join_lua_tokens": (".lua_lexer", "join_lua_tokens"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_path, attr_name = _LAZY_EXPORTS[name]
        import importlib
        mod = importlib.import_module(module_path, __package__)
        val = getattr(mod, attr_name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + __all__)
