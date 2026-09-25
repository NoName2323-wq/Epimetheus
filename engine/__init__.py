"""
Prometheus Deobfuscator Engine Package.

Provides modular sub-engines:
- trace_filter: Streaming VM loop & unpacking noise suppression
- ast_optimizer: Constant arithmetic folding, copy propagation & AST cleanup
- static_decoder: Static extraction and decoding of Prometheus Base64/Base85/Mixed string tables
"""

from .trace_filter import TraceFilter, filter_trace_stream, filter_trace_lines
from .ast_optimizer import AstOptimizer, optimize_lua_code
from .static_decoder import StaticConstantDecoder, decode_prometheus_constants

__all__ = [
    "TraceFilter",
    "filter_trace_stream",
    "filter_trace_lines",
    "AstOptimizer",
    "optimize_lua_code",
    "StaticConstantDecoder",
    "decode_prometheus_constants",
]
