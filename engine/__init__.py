"""
Epimetheus Deobfuscator Engine Package.

Provides modular sub-engines:
- trace_filter: Streaming VM loop & unpacking noise suppression
- ast_optimizer: Constant arithmetic folding, copy propagation & string folding
- static_decoder: Static extraction and decoding of Prometheus Base64/Base85/Mixed string tables & PRNG cipher
- syntax_normalizer: Luau compound assignment & type annotation normalizer
- constant_inliner: Static constant array call inliner & proxy unwrapper
"""

from .trace_filter import TraceFilter, filter_trace_stream, filter_trace_lines
from .ast_optimizer import AstOptimizer, optimize_lua_code
from .static_decoder import StaticConstantDecoder, decode_prometheus_constants
from .syntax_normalizer import LuauSyntaxNormalizer, normalize_luau_syntax
from .constant_inliner import ConstantInliner, inline_constants_in_code

__all__ = [
    "TraceFilter",
    "filter_trace_stream",
    "filter_trace_lines",
    "AstOptimizer",
    "optimize_lua_code",
    "StaticConstantDecoder",
    "decode_prometheus_constants",
    "LuauSyntaxNormalizer",
    "normalize_luau_syntax",
    "ConstantInliner",
    "inline_constants_in_code",
]
