import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import deobfuscator
import trace_to_lua


ROOT = Path(__file__).resolve().parents[1]
COMPLEX_FIXTURES = ROOT / "deobfuscated_scripts_complex"
OBFUSCATED_FIXTURES = ROOT / "obfuscated_scripts"


class DeobfuscatorRegressionTests(unittest.TestCase):
    def test_normalize_luau_syntax_rewrites_compound_assignments(self):
        source = "foo+=1 bar.baz-=delta tbl[idx]*=scale"

        rewritten = deobfuscator.normalize_luau_syntax(source)

        self.assertIn("foo = foo + 1", rewritten)
        self.assertIn("bar.baz = bar.baz - delta", rewritten)
        self.assertIn("tbl[idx] = tbl[idx] * scale", rewritten)

    @unittest.skipUnless(COMPLEX_FIXTURES.is_dir(), "local complex fixtures are not checked in")
    def test_complex_fixtures_emit_traceful_code(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            working_copy = Path(tmp_dir) / "complex"
            shutil.copytree(COMPLEX_FIXTURES, working_copy)

            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(working_copy)],
                cwd=ROOT,
                capture_output=True,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(
                result.returncode,
                0,
                msg=f"{stdout}\n{stderr}",
            )

            generated = sorted(working_copy.glob("*.deobf.lua"))
            self.assertEqual(len(generated), 4)

            interesting_tokens = (
                "game:GetService",
                "Instance.new",
                "task.",
                ":Connect(",
                ":WaitForChild(",
                "Color3.",
                "UDim2.",
            )

            for output_file in generated:
                text = output_file.read_text(encoding="utf-8", errors="replace")
                non_comment_lines = [
                    line
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("--")
                ]
                code_lines = [
                    line for line in non_comment_lines if not line.startswith("local Constants =")
                ]

                self.assertTrue(
                    code_lines,
                    msg=f"{output_file.name} only produced constants/header:\n{text[:1000]}",
                )
                self.assertTrue(
                    any(token in text for token in interesting_tokens),
                    msg=f"{output_file.name} did not recover meaningful Lua code:\n{text[:1000]}",
                )

    @unittest.skipUnless(COMPLEX_FIXTURES.is_dir(), "local complex fixtures are not checked in")
    def test_complex_fixture_constants_are_ascii_safe(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            working_copy = Path(tmp_dir) / "complex"
            shutil.copytree(COMPLEX_FIXTURES, working_copy)

            subprocess.run(
                [sys.executable, "deobfuscator.py", str(working_copy)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            for report_file in sorted(working_copy.glob("*.report.txt")):
                text = report_file.read_text(encoding="utf-8", errors="replace")
                constants_section = text.split("--- CONSTANTS ---", 1)[1]
                self.assertFalse(
                    any(ord(char) > 127 for char in constants_section),
                    msg=f"{report_file.name} still contains non-ASCII constant data:\n{constants_section[:1500]}",
                )

    def test_large_lua51_control_structure_falls_back_to_static_constants(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample = Path(tmp_dir) / "large_control.luau"
            oversized_block = " ".join("a=1" for _ in range(70000))
            sample.write_text(
                '--[[ v1.0.0 https://wearedevs.net/obfuscator ]] '
                'return(function(...)local z={"\\065","\\066"} '
                f"if false then {oversized_block} end end)"
                "(getfenv and getfenv()or _ENV)",
                encoding="utf-8",
            )

            test_env = dict(shutil.os.environ)
            test_env["LUA_MAX_SBX"] = "50000"
            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(sample)],
                cwd=ROOT,
                capture_output=True,
                env=test_env,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(result.returncode, 0, msg=f"{stdout}\n{stderr}")
            self.assertIn("using static string-table fallback", stdout)
            self.assertNotIn("STDERR:", stdout)

            report = sample.with_name(sample.name + ".report.txt")
            deobfuscated = sample.with_name(sample.name + ".deobf.lua")
            self.assertIn('[1] = "A"', report.read_text(encoding="utf-8"))
            self.assertIn('[2] = "B"', deobfuscated.read_text(encoding="utf-8"))

    def test_deobfuscator_safely_handles_http_substring_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample = Path(tmp_dir) / "http_substring.luau"
            sample.write_text(
                '--[[ v1.0.0 https://wearedevs.net/obfuscator ]] '
                'return(function(...)local env = ...; local z={"http", "www", "https://google.com/search?q=test"} '
                'for _, url in ipairs(z) do env.table.concat({url}, "") end end)'
                "(getfenv and getfenv()or _ENV)",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(sample)],
                cwd=ROOT,
                capture_output=True,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(result.returncode, 0, msg=f"{stdout}\n{stderr}")
            self.assertNotIn("STDERR:", stdout)
            self.assertIn("URL DETECTED IN CONCAT --> https://google.com/search?q=test", stdout)

    def test_mocked_string_char_tolerates_dummy_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample = Path(tmp_dir) / "dummy_string_char.luau"
            sample.write_text(
                '--[[ v1.0.0 https://wearedevs.net/obfuscator ]] '
                'return(function(...)local env = ...; local z={"A"} '
                'local B = env.string.char; B(env.game) end)'
                "(getfenv and getfenv()or _ENV)",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(sample)],
                cwd=ROOT,
                capture_output=True,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(result.returncode, 0, msg=f"{stdout}\n{stderr}")
            self.assertNotIn("STDERR:", stdout)
            self.assertTrue(sample.with_name(sample.name + ".deobf.lua").exists())

    @unittest.skipUnless(OBFUSCATED_FIXTURES.is_dir(), "local obfuscated fixtures are not checked in")
    def test_known_wearedevs_sample_keeps_decoded_constants_and_code(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sample = Path(tmp_dir) / "known_decoded.lua"
            shutil.copy2(
                OBFUSCATED_FIXTURES / "obfuscated_script-1771597947527.lua",
                sample,
            )

            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(sample)],
                cwd=ROOT,
                capture_output=True,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(result.returncode, 0, msg=f"{stdout}\n{stderr}")

            deobfuscated = sample.with_name(sample.name + ".deobf.lua")
            text = deobfuscated.read_text(encoding="utf-8", errors="replace")
            for token in (
                "Tamper Detected!",
                "FindFirstChild",
                "GetService",
                "FireServer",
                "I LOVE Gravity!",
            ):
                self.assertIn(token, text)

    @unittest.skipUnless(OBFUSCATED_FIXTURES.is_dir(), "local obfuscated fixtures are not checked in")
    def test_local_obfuscated_fixture_batch_still_produces_outputs(self):
        source_files = [
            path
            for path in sorted(OBFUSCATED_FIXTURES.glob("*.lua"))
            if ".deobf." not in path.name and ".report." not in path.name
        ]
        self.assertGreaterEqual(len(source_files), 10)

        with tempfile.TemporaryDirectory() as tmp_dir:
            working_copy = Path(tmp_dir) / "obfuscated"
            working_copy.mkdir()
            for source_file in source_files:
                shutil.copy2(source_file, working_copy / source_file.name)

            result = subprocess.run(
                [sys.executable, "deobfuscator.py", str(working_copy)],
                cwd=ROOT,
                capture_output=True,
            )
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            self.assertEqual(result.returncode, 0, msg=f"{stdout}\n{stderr}")

            generated = sorted(working_copy.glob("*.deobf.lua"))
            self.assertEqual(len(generated), len(source_files))
            for output_file in generated:
                text = output_file.read_text(encoding="utf-8", errors="replace")
                self.assertIn("-- Deobfuscated via Trace Emulation", text)
                self.assertGreater(
                    len(text),
                    30,
                    msg=f"{output_file.name} produced empty-looking output",
                )

    def test_trace_to_lua_parses_colon_calls_and_split_args(self):
        raw_call = 'local abc_123 = game:GetService("Players")'
        call_expr, var_name, is_method = trace_to_lua.simplify_call_result(raw_call)

        self.assertTrue(is_method)
        self.assertEqual(call_expr, raw_call)
        self.assertEqual(var_name, "abc_123")

        args = trace_to_lua.smart_split_args('"a,b", wrapper("x,y"), 42')
        self.assertEqual(args[0], '"a,b"')
        self.assertEqual(args[1], ' wrapper("x,y")')
        self.assertEqual(args[2], ' 42')

        escaped_args = trace_to_lua.smart_split_args(r'"hello \"world, test\"", 100, {a = 1, b = 2}')
        self.assertEqual(len(escaped_args), 3)
        self.assertEqual(escaped_args[0], r'"hello \"world, test\""')
        self.assertEqual(escaped_args[1].strip(), '100')
        self.assertEqual(escaped_args[2].strip(), '{a = 1, b = 2}')

        lines = [
            "ACCESSED --> game",
            "CALL_RESULT --> local a = game:GetService()",
            "ACCESSED --> task",
            "CALL_RESULT --> local b = task.wait(1)",
            "ACCESSED --> game",
            "CALL_RESULT --> local a = game:GetService()",
            "ACCESSED --> task",
            "CALL_RESULT --> local b = task.wait(1)",
            "ACCESSED --> game",
            "CALL_RESULT --> local a = game:GetService()",
            "ACCESSED --> task",
            "CALL_RESULT --> local b = task.wait(1)",
        ]
        loop_info = trace_to_lua.detect_loops(lines)
        self.assertIsNotNone(loop_info)
        self.assertEqual(loop_info[0], 4)
        self.assertGreaterEqual(loop_info[1], 3)

    def test_check_platform_blocks_non_linux(self):
        import unittest.mock as mock
        for non_linux in ("win32", "darwin", "freebsd", "cygwin", "sunos5"):
            with mock.patch("sys.platform", non_linux):
                with self.assertRaises(SystemExit) as ctx:
                    deobfuscator.check_platform()
                self.assertIn("Linux-native", str(ctx.exception))

        with mock.patch("sys.platform", "linux"):
            deobfuscator.check_platform()  # Must not raise
        with mock.patch("sys.platform", "linux2"):
            deobfuscator.check_platform()  # Must not raise

    def test_lua_sandbox_blocks_dangerous_os_and_io_apis(self):
        lua_exe = deobfuscator.get_lua_executable()
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_script = Path(tmp_dir) / "exploit_test.lua"
            test_script.write_text(
                'local c = { [1] = "secret" }\n'
                'return (function(arr)\n'
                '    if os.execute then os.execute("echo DANGEROUS") end\n'
                '    if io and io.open then io.open("/tmp/fail", "w") end\n'
                '    local p = game:GetService("Players")\n'
                '    return arr[1]\n'
                'end)(getfenv and getfenv() or _ENV)\n'
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "deobfuscator.py"), str(test_script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            self.assertNotIn("DANGEROUS", result.stdout)
            self.assertFalse(Path("/tmp/fail").exists())

    def test_lua_sandbox_safe_debug_without_recursion(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_script = Path(tmp_dir) / "debug_test.lua"
            test_script.write_text(
                'local c = { [1] = "secret" }\n'
                'return (function(arr)\n'
                '    local info = debug.getinfo(1)\n'
                '    local tb = debug.traceback("probe")\n'
                '    local up = debug.getupvalue(function() end, 1)\n'
                '    local p = game:GetService("Workspace")\n'
                '    return arr[1]\n'
                'end)(getfenv and getfenv() or _ENV)\n'
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "deobfuscator.py"), str(test_script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            self.assertIn("game.GetService", result.stdout)
            self.assertIn('"Workspace"', result.stdout)

    def test_extract_static_constants_sandboxed(self):
        from deobfuscator import extract_static_constants

        malicious_content = (
            "local constants_table = {\n"
            "    [1] = (os and os.execute and os.execute('echo EXPLOIT_TRIGGERED')) or 'safe_data',\n"
            "    [2] = 'hello_world',\n"
            "}\n"
        )
        extracted = extract_static_constants(malicious_content, "constants_table")
        self.assertIn("safe_data", extracted)
        self.assertNotIn("EXPLOIT_TRIGGERED", extracted)

    def test_trace_to_lua_loop_preserves_tail_operations(self):
        import trace_to_lua

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = Path(tmp_dir) / "loop_tail_test.report.txt"
            lines = [
                "--- TRACE ---",
                "CALL_RESULT --> local v_1001 = game.GetService(Workspace)",
                "CALL_RESULT --> local v_1002 = v_1001.FindPartOnRay(Ray)",
                "CALL_RESULT --> local v_1003 = game.GetService(Workspace)",
                "CALL_RESULT --> local v_1004 = v_1003.FindPartOnRay(Ray)",
                "CALL_RESULT --> local v_1005 = game.GetService(Workspace)",
                "CALL_RESULT --> local v_1006 = v_1005.FindPartOnRay(Ray)",
                "CALL_RESULT --> local v_1007 = game.GetService(Players)",
                "--- TRACE END ---",
            ]
            report.write_text("\n".join(lines), encoding="utf-8")
            trace_to_lua.parse_trace(str(report))
            deobf = report.with_name("loop_tail_test.deobf.lua").read_text(encoding="utf-8")
            self.assertIn("while true do", deobf)
            self.assertIn("Players", deobf)


if __name__ == "__main__":
    unittest.main()
