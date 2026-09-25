# Changelog

All notable changes to the Epimetheus Deobfuscator & Dumper project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.5.0] - 2026-09-25

### Added
- **Linux Exclusivity Enforcement**:
  - Implemented `check_platform()` in [`deobfuscator.py`](deobfuscator.py) to prevent execution on non-Linux operating systems with a clean error message.
  - Added unit test `test_check_platform_blocks_non_linux` to verify non-Linux environments are blocked, bringing test suite to **19 passing tests**.

### Removed
- **Complete Removal of Windows Support**:
  - Deleted all legacy Windows binaries (`bin2c5.1.exe`, `lua5.1.exe`, `luac5.1.exe`, `wlua5.1.exe`), Windows dynamic libraries (`lua5.1.dll`, `lua51.dll`), and manifest files from `lua_bin/`.
  - Removed Windows-specific candidate paths, `.exe` detection, and `os.name == "nt"` branches from `deobfuscator.py`.
  - Removed Windows commands, badges, and documentation from `README.md` and `README.ru.md`.

---

## [2.4.0] - 2026-09-24

### Added
- **Official Rebranding to Epimetheus**:
  - Rebranded project to **Epimetheus** (the mythological counterpart to Prometheus, representing hindsight and deciphering).
- **`engine/` Modular Sub-Engine Package**:
  - **`engine/trace_filter.py`**: Stream and batch trace filter that eliminates interpreter noise (`UNPACK CALLED WITH TABLE...`, `CAPTURED CHUNK STRING...`) and VM loop allocations. Slashes trace file sizes by over **92–94%** (from 10–11 MB down to 600–775 KB).
  - **`engine/ast_optimizer.py`**: AST and token-based Lua optimizer providing safe constant arithmetic folding (e.g. `(-80732 + 80796)` $\rightarrow$ `64`), hexadecimal normalization, copy propagation (inlining single-use aliases `var2 = var1`), and dead self-assignment elimination (`x = x`), with strict preservation of string literals and comments.
  - **`engine/static_decoder.py`**: Pure Python static string table decoder capable of extracting and reconstructing custom 64-character (Base64) and 85-character (Base85) substitution alphabets, Mixed encoding prefixes, and Prometheus circular array rotations (`Rotate` step).
- **Comprehensive Engine Test Suite**:
  - Created [`tests/test_engine.py`](tests/test_engine.py) with 9 dedicated unit tests verifying filtering, constant folding, Base64/Base85 custom decoding, and rotation un-shifting. Total project unit tests increased to **18 tests** (100% pass rate).

### Changed
- **130x Variable Resolution Speedup**:
  - Completely refactored `resolve_vars` in [`trace_to_lua.py`](trace_to_lua.py) from an $O(N \cdot M)$ sequential loop of `re.sub` over thousands of keys into a single compiled regular expression alternation `\b(var1|var2|...)\b`, cutting variable replacement time from **1.61 s down to 0.012 s**.
- **Trace Pipeline Integration**:
  - Integrated `TraceFilter` directly into `deobfuscator.py` stdout processing, preventing bloated 10 MB `.report.txt` files from being written to disk.
  - Integrated `AstOptimizer` directly into `trace_to_lua.py` post-processing pipeline.
  - Integrated `StaticConstantDecoder` as a direct fallback in `deobfuscator.py` when Lua scripts exceed compiler limits (`control structure too long`).

### Removed
- **Eliminated Web Viewer (`viewer.py`)**:
  - Removed the standalone virtualized HTTP web viewer and its background threading overhead. Because trace reports and deobfuscated files are now cleanly compressed (~600 KB), they open instantaneously in any standard IDE or text editor without freezing.
- **Removed Analyzed Obfuscator Source**:
  - Deleted the embedded `Prometheus-master/` source tree after extracting and reverse-engineering all VM encoding, rotation, and anti-tamper logic into native Python modules, keeping the repository light and clean.

---

## [2.3.0] - 2026-09-24

### Added
- Deep source analysis of official Prometheus obfuscator ([wcrddn/Prometheus](https://github.com/wcrddn/Prometheus/tree/master)), specifically `ConstantArray.lua`, `AntiTamper.lua`, and `Vmify.lua`.
- Verified end-to-end deobfuscation on complex production scripts: multi-megabyte obfuscated targets (reconstructing 1,862 and 2,401 lines of clean Lua).

### Changed
- Fixed `normalize_luau_syntax` in `deobfuscator.py` to be string-literal and comment aware, preventing corruption of binary and escaped strings containing operators (e.g. `"Xtrba#(f%="`).

---

## [2.2.0] - 2026-09-24

### Added
- **Anti-Tamper Bypass**:
  - Neutralized C-type function detection and debug line hooks (`debug.sethook`).
  - Added canary check protection: unknown global accesses safely return `nil` to avoid triggering Prometheus detection traps.
- **Resource Limits**:
  - Added `LUA_MAX_SBX` memory and step limits to protect the emulator from memory exhaustion when processing heavily nested control structures.

---

## [2.1.0] - 2026-09-23

### Added
- **Native Linux x86_64 Port**:
  - Compiled and bundled native 64-bit ELF `lua_bin/lua5.1` binary.
  - Implemented multi-tier binary resolution: checks `LUA51_EXECUTABLE` environment variable, bundled `lua_bin/`, and system `PATH`.
  - Added automatic execution permission enforcement (`chmod +x`).
- **Process Robustness**:
  - Replaced Windows-specific process execution with cross-platform POSIX process handling and timeout controls to prevent infinite interpreter hangs.

---

## [2.0.0] - 2026-09-23

### Changed
- Forked from original repository [hutaoshusband/Prometheus-WeAre-Devs-Dumper](https://github.com/hutaoshusband/Prometheus-WeAre-Devs-Dumper).
- Removed hardcoded Windows backslash paths and `.exe` dependencies.
- Added initial regression test harness (`tests/test_deobfuscator.py`).

---

## [1.0.0] - 2024-07-11

### Initial Release
- Initial implementation by `hutaoshusband` targeting WeAreDevs Prometheus-obfuscated Roblox scripts on Windows.
- Basic mock environment hooking `game:HttpGet` and table string dumping.
