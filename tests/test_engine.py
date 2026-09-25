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


    def test_string_concatenation_folding(self):
        code = 'local s = "Hello, " .. "world" .. "!"\nlocal s2 = "foo" .. "bar"\n'
        optimized = optimize_lua_code(code)
        self.assertIn('local s = "Hello, world!"', optimized)
        self.assertIn('local s2 = "foobar"', optimized)

    def test_table_concat_folding(self):
        code = 'local res = table.concat({"partA", "partB", "partC"}, "_")\n'
        optimized = optimize_lua_code(code)
        self.assertIn('local res = "partA_partB_partC"', optimized)

    def test_scientific_and_power_math_folding(self):
        code = "local x = (1e2 + 50)\nlocal y = (2 ^ 4)\nlocal z = (10 % 3)\n"
        optimized = optimize_lua_code(code)
        self.assertIn("local x = 150", optimized)
        self.assertIn("local y = 16", optimized)
        self.assertIn("local z = 1", optimized)

    def test_watermark_removal(self):
        code = 'if _var ~= "This Script is Part of the Prometheus Obfuscator by levno-710" then return end\nlocal a = 1\n'
        optimized = optimize_lua_code(code)
        self.assertNotIn('then return end', optimized)
        self.assertIn('local a = 1', optimized)


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
        orig = [1, 2, 3, 4, 5]
        rotated = [4, 5, 1, 2, 3]
        unrotated = decoder.unrotate_array(rotated, shift=2, length=5)
        self.assertEqual(unrotated, orig)

    def test_detect_rotate_with_math_and_hex(self):
        decoder = StaticConstantDecoder()
        snippet = "for i, v in ipairs({{1, 0x100}, {1, (0x10 + 4)}, {20 + 1, 0x100}}) do"
        rotate_info = decoder.detect_rotate(snippet)
        self.assertIsNotNone(rotate_info)
        shift, length = rotate_info
        self.assertEqual(shift, 20)
        self.assertEqual(length, 256)

    def test_prometheus_stream_decryption(self):
        decoder = StaticConstantDecoder()
        # Test PRNG stream cipher round-trip with arbitrary keys
        param_mul_45 = 125
        param_add_45 = 654321
        param_mul_8 = 45
        secret_key_8 = 123
        seed = 9876543210
        expected = b"Epimetheus Prometheus Decryption Test"

        # Encrypt with Prometheus cipher formula
        state_45 = seed % 35184372088832
        state_8 = seed % 255 + 2
        prev_values = []

        def get_rnd():
            nonlocal state_45, state_8, prev_values
            if not prev_values:
                state_45 = (state_45 * param_mul_45 + param_add_45) % 35184372088832
                while True:
                    state_8 = (state_8 * param_mul_8) % 257
                    if state_8 != 1:
                        break
                r = state_8 % 32
                shift = 13 - (state_8 - r) // 32
                n_base = (state_45 // (1 << shift)) % 4294967296 if shift >= 0 else (state_45 * (1 << (-shift))) % 4294967296
                n = n_base / (1 << r)
                rnd = int((n % 1.0) * 4294967296) + int(n)
                low_16 = rnd % 65536
                high_16 = (rnd - low_16) // 65536
                prev_values = [low_16 % 256, (low_16 - low_16 % 256) // 256, high_16 % 256, (high_16 - high_16 % 256) // 256]
            return prev_values.pop()

        encrypted = bytearray()
        prev = secret_key_8
        for b in expected:
            rnd = get_rnd()
            encrypted.append((b - (rnd + prev)) % 256)
            prev = b

        decrypted = decoder.decrypt_prometheus_stream(
            bytes(encrypted),
            seed=seed,
            param_mul_45=param_mul_45,
            param_add_45=param_add_45,
            param_mul_8=param_mul_8,
            secret_key_8=secret_key_8,
        )
        self.assertEqual(decrypted, expected)


if __name__ == "__main__":
    unittest.main()
