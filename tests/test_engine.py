import unittest
from engine.trace_filter import TraceFilter, filter_trace_lines
from engine.ast_optimizer import AstOptimizer, optimize_lua_code, safe_eval_math_expr
from engine.static_decoder import StaticConstantDecoder, decode_prometheus_constants


class EngineTraceFilterTests(unittest.TestCase):
    def test_filter_removes_unpack_and_chunk_spam(self):
        lines = [
            "--- TRACE ---",
            "UNPACK CALLED WITH TABLE (size 10)",
            "CAPTURED CHUNK STRING dummy_chunk_data",
            "ACCESSED --> game",
            "CALL_RESULT --> local p = game:GetService(\"Players\")",
            "UNPACK CALLED WITH TABLE (size 5)",
            "SET GLOBAL --> MyGlobal = 123",
            "URL DETECTED --> https://example.com/api",
        ]

        filtered = filter_trace_lines(lines)

        self.assertIn("--- TRACE ---", filtered)
        self.assertIn("ACCESSED --> game", filtered)
        self.assertIn("CALL_RESULT --> local p = game:GetService(\"Players\")", filtered)
        self.assertIn("SET GLOBAL --> MyGlobal = 123", filtered)
        self.assertIn("URL DETECTED --> https://example.com/api", filtered)

        for line in filtered:
            self.assertNotIn("UNPACK CALLED", line)
            self.assertNotIn("CAPTURED CHUNK", line)

    def test_filter_compresses_consecutive_duplicates(self):
        lines = ["ACCESSED --> game"] * 20 + ["CALL_RESULT --> local x = 1"]
        flt = TraceFilter(max_consecutive_duplicates=3)
        filtered = flt.filter_lines(lines)

        # 3 duplicates + 1 call result = 4 lines
        self.assertEqual(len(filtered), 4)
        self.assertEqual(filtered.count("ACCESSED --> game"), 3)
        self.assertEqual(filtered[-1], "CALL_RESULT --> local x = 1")
        self.assertEqual(flt.duplicates_compressed, 17)


class EngineAstOptimizerTests(unittest.TestCase):
    def test_safe_eval_math_expr(self):
        self.assertEqual(safe_eval_math_expr("-80732 + 80796"), 64)
        self.assertEqual(safe_eval_math_expr("0x10 + 5"), 21)
        self.assertEqual(safe_eval_math_expr("100 - 40 * 2"), 20)
        self.assertIsNone(safe_eval_math_expr("os.execute('ls')"))
        self.assertIsNone(safe_eval_math_expr("1 / 0"))

    def test_constant_folding_in_code_preserves_literals(self):
        code = (
            'local a = (-80732 + 80796)\n'
            'local s = "do not fold (-80732 + 80796) here"\n'
            '-- comment (-80732 + 80796) here\n'
            'local b = (0x10 + 4)\n'
        )
        optimized = optimize_lua_code(code)

        self.assertIn("local a = 64", optimized)
        self.assertIn('local s = "do not fold (-80732 + 80796) here"', optimized)
        self.assertIn("-- comment (-80732 + 80796) here", optimized)
        self.assertIn("local b = 20", optimized)

    def test_alias_propagation_and_dead_self_assignment(self):
        code = (
            'local player_service = game:GetService("Players")\n'
            'local alias_service = player_service\n'
            'alias_service:Connect()\n'
            'alias_service = alias_service\n'
        )
        optimized = optimize_lua_code(code)

        self.assertNotIn("local alias_service = player_service", optimized)
        self.assertNotIn("alias_service = alias_service", optimized)
        self.assertIn('player_service:Connect()', optimized)


class EngineStaticDecoderTests(unittest.TestCase):
    def test_unescape_and_escape_roundtrip(self):
        raw = r"\072\101\108\108\111"  # :ello -> 72='H', 101='e', 108='l', 108='l', 111='o'
        unescaped = StaticConstantDecoder.unescape_lua_string(raw)
        self.assertEqual(unescaped, "Hello")

        escaped = StaticConstantDecoder.escape_for_lua("Hello\nWorld")
        self.assertEqual(escaped, r'"Hello\nWorld"')

    def test_base64_decode_custom_lookup(self):
        decoder = StaticConstantDecoder()
        # Build standard 64 charset
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        lookup = {ch: i for i, ch in enumerate(chars)}

        # Base64 "SGVsbG8=" -> "Hello"
        decoded = decoder.decode_base64("SGVsbG8=", lookup)
        self.assertEqual(decoded, b"Hello")

    def test_base85_decode_custom_lookup(self):
        decoder = StaticConstantDecoder()
        # In Prometheus: 85 printable characters
        chars = (
            "!\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "[\\]^_`abcdefghijklmnopqrstu"
        )
        lookup = {ch: i for i, ch in enumerate(chars)}

        # Prometheus Base85 encodes 4 bytes into 5 chars:
        # For 'test': bytes: 116, 101, 115, 116 -> value = 1952805748
        # 1952805748 in base 85: codes [37, 34, 69, 45, 23] -> chars:
        encoded = "".join(chars[c] for c in [37, 34, 69, 45, 23])
        decoded = decoder.decode_base85(encoded, lookup)
        self.assertEqual(decoded, b"test")

    def test_unrotate_array(self):
        decoder = StaticConstantDecoder()
        # In Prometheus:
        # reverse(1, n); reverse(1, d); reverse(d+1, n)
        # unrotate_array reverses it back
        orig = [1, 2, 3, 4, 5]
        # Simulate rotate with shift 2:
        # reverse(0, 4) -> [5, 4, 3, 2, 1]
        # reverse(0, 1) -> [4, 5, 3, 2, 1]
        # reverse(2, 4) -> [4, 5, 1, 2, 3]
        rotated = [4, 5, 1, 2, 3]
        unrotated = decoder.unrotate_array(rotated, shift=2, length=5)
        self.assertEqual(unrotated, orig)


if __name__ == "__main__":
    unittest.main()
