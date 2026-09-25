#!/usr/bin/env python3
"""
Epimetheus Performance Benchmark Suite.

Benchmarks key pipeline stages:
1. Syntax Normalizer (compound assignments, type stripping)
2. Tokenizer / Lexer
3. AST Optimizer (math folding, alias propagation)
4. Trace Filter (VM loop compression, prefix filtering)
5. Static Constant Decoder (Base64/Base85 decoding)
6. Trace to Lua generation
"""

import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.syntax_normalizer import normalize_luau_syntax, LuauSyntaxNormalizer
from engine.ast_optimizer import optimize_lua_code, safe_eval_math_expr
from engine.trace_filter import TraceFilter, filter_trace_lines
from engine.static_decoder import StaticConstantDecoder
from engine.constant_inliner import inline_constants_in_code
import trace_to_lua


def generate_benchmark_luau(num_lines: int = 5000) -> str:
    """Generate a realistic Luau script with types, compound ops, and string literals."""
    lines = [
        '-- Synthetic Benchmark File for Epimetheus',
        'type Point = { x: number, y: number }',
        'type Handler<T> = (event: T) -> boolean',
        'local my_table = { [1] = "constant_value_1", [2] = "constant_value_2" }',
    ]
    for i in range(num_lines):
        rem = i % 5
        if rem == 0:
            lines.append(f'local var_{i}: number = {i} + 10')
            lines.append(f'var_{i} += {i % 100}')
        elif rem == 1:
            lines.append(f'local str_{i} = "literal_string_{i} with special chars: \\n\\t"')
            lines.append(f'-- Comment describing step {i} with [[ brackets ]]')
        elif rem == 2:
            lines.append(f'function compute_{i}<T>(arg1: T, arg2: number): (boolean, string)')
            lines.append(f'    local res = arg2 * 2')
            lines.append(f'    res -= 5')
            lines.append(f'    return true, "ok"')
            lines.append(f'end')
        elif rem == 3:
            lines.append(f'local val_{i} = raw_val_{i} :: (number) -> string')
        else:
            lines.append(f'tbl_{i}["field_{i}"] ..= "_suffix"')
    return "\n".join(lines)


def generate_benchmark_ast_code(num_lines: int = 3000) -> str:
    """Generate Lua code with constant math expressions and alias chains."""
    lines = ['local Constants = { [1] = "hello", [2] = "world" }']
    for i in range(num_lines):
        rem = i % 4
        if rem == 0:
            lines.append(f'local a_{i} = (-80732 + 80796)')
            lines.append(f'local b_{i} = (0x100 + {i % 50})')
            lines.append(f'local c_{i} = (2 ^ 8)')
        elif rem == 1:
            lines.append(f'local src_{i} = game:GetService("Workspace")')
            lines.append(f'local alias_{i} = src_{i}')
            lines.append(f'local target_{i} = alias_{i}')
            lines.append(f'target_{i}:DoSomething()')
        elif rem == 2:
            lines.append(f'local unused_{i} = unused_{i}')
            lines.append(f'local s_{i} = "literal string containing (-80732 + 80796) unchanged"')
        else:
            lines.append(f'table.concat({{"part1_", "part2_", "part3_{i}"}})')
    return "\n".join(lines)


def generate_benchmark_trace_lines(num_lines: int = 20000) -> list:
    """Generate realistic runtime trace lines with duplicate loops and unpack spam."""
    lines = ["--- TRACE ---"]
    for i in range(num_lines):
        rem = i % 6
        if rem == 0:
            lines.append(f'CALL_RESULT --> local v_{i} = game:GetService("Players")')
        elif rem == 1:
            lines.append('UNPACK CALLED WITH TABLE (size 50)')
        elif rem == 2:
            lines.append(f'ACCESSED --> Players')
        elif rem == 3:
            lines.append(f'CALL_RESULT --> local part = Instance.new("Part")')
        elif rem == 4:
            lines.append(f'SET GLOBAL --> GlobalVar_{i % 10} = "val_{i}"')
        else:
            lines.append(f'URL DETECTED --> https://api.example.com/endpoint/{i % 20}')
    return lines


def run_benchmarks():
    print("=" * 60)
    print("⚡ Epimetheus Benchmark Suite")
    print("=" * 60)

    # 1. Luau Syntax Normalizer
    print("\n[1/5] Benchmarking Luau Syntax Normalizer...")
    luau_code = generate_benchmark_luau(3000)
    size_kb = len(luau_code) / 1024
    print(f"      Input size: {size_kb:.1f} KB ({luau_code.count(chr(10))} lines)")

    t0 = time.perf_counter()
    normalized = normalize_luau_syntax(luau_code)
    t1 = time.perf_counter()
    dur_norm = t1 - t0
    print(f"      Duration: {dur_norm:.4f} s ({size_kb / dur_norm:.1f} KB/s)")

    # 2. AST Optimizer
    print("\n[2/5] Benchmarking AST Optimizer (math folding & alias propagation)...")
    ast_code = generate_benchmark_ast_code(2000)
    size_ast_kb = len(ast_code) / 1024
    print(f"      Input size: {size_ast_kb:.1f} KB ({ast_code.count(chr(10))} lines)")

    t0 = time.perf_counter()
    optimized = optimize_lua_code(ast_code)
    t1 = time.perf_counter()
    dur_ast = t1 - t0
    print(f"      Duration: {dur_ast:.4f} s ({size_ast_kb / dur_ast:.1f} KB/s)")

    # 3. Trace Filter
    print("\n[3/5] Benchmarking TraceFilter (streaming & loop compression)...")
    trace_lines = generate_benchmark_trace_lines(25000)
    print(f"      Input: {len(trace_lines)} trace lines")

    t0 = time.perf_counter()
    flt = TraceFilter(max_consecutive_duplicates=5)
    filtered = flt.filter_lines(trace_lines)
    t1 = time.perf_counter()
    dur_filter = t1 - t0
    print(f"      Duration: {dur_filter:.4f} s ({len(trace_lines) / dur_filter:.0f} lines/s)")
    print(f"      Filtered: {len(trace_lines)} -> {len(filtered)} lines")

    # 4. Math Expr Evaluator
    print("\n[4/5] Benchmarking safe_eval_math_expr (10,000 evaluations)...")
    sample_exprs = [
        "-80732 + 80796",
        "0x100 + 45",
        "100 - 40 * 2",
        "2 ^ 8",
        "(0x497e0 + -301009)",
    ] * 2000

    t0 = time.perf_counter()
    for expr in sample_exprs:
        safe_eval_math_expr(expr)
    t1 = time.perf_counter()
    dur_math = t1 - t0
    print(f"      Duration: {dur_math:.4f} s ({len(sample_exprs) / dur_math:.0f} expr/s)")

    # 5. Static Decoder (Base64)
    print("\n[5/5] Benchmarking StaticConstantDecoder (Base64 decode 1,000 strings)...")
    decoder = StaticConstantDecoder()
    b64_lookup = {chr(65 + i): i for i in range(26)}
    for i in range(26):
        b64_lookup[chr(97 + i)] = 26 + i
    for i in range(10):
        b64_lookup[str(i)] = 52 + i
    b64_lookup["+"] = 62
    b64_lookup["/"] = 63

    sample_b64 = "SGVsbG8gV29ybGQgZnJvbSBFcGltZXRoZXVzIEVuZ2luZSE="
    t0 = time.perf_counter()
    for _ in range(2000):
        decoder.decode_base64(sample_b64, b64_lookup)
    t1 = time.perf_counter()
    dur_b64 = t1 - t0
    print(f"      Duration: {dur_b64:.4f} s ({2000 / dur_b64:.0f} decodes/s)")

    total_dur = dur_norm + dur_ast + dur_filter + dur_math + dur_b64
    print("\n" + "=" * 60)
    print(f"Total Benchmark Pipeline Duration: {total_dur:.4f} s")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmarks()
