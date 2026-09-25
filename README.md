# ⚡ Epimetheus — Advanced Prometheus & Luau Deobfuscator Engine

<div align="center">

![Epimetheus](https://img.shields.io/badge/Project-Epimetheus%20v2.6.0-blueviolet?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge&logo=python&logoColor=white)
![Lua](https://img.shields.io/badge/Lua-5.1-000080?style=for-the-badge&logo=lua&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20Only-success?style=for-the-badge&logo=linux&logoColor=white)
![License](https://img.shields.io/badge/License-GNU%20GPLv3-yellow?style=for-the-badge)
![Tests](https://img.shields.io/badge/Tests-30%20Passed-brightgreen?style=for-the-badge)

**Linux-exclusive next-generation trace emulation, static constant decoding, and AST-optimized deobfuscation engine for Prometheus-protected Roblox Luau scripts.**

*In Greek mythology, Prometheus represents "forethought" (creating the locks and traps), while Epimetheus represents "hindsight" (looking back, deciphering, and unlocking).*

[Русская версия (Russian Version)](README.ru.md) • [Changelog](CHANGELOG.md)

<br/>

<img src="assets/cli_preview.png" alt="Epimetheus CLI Preview" width="800"/>

</div>

---

## ⚠️ Important Note: Linux Only

> [!IMPORTANT]
> **Epimetheus is built exclusively for Linux.** Windows support has been completely removed from the codebase, eliminating all legacy `.exe` wrappers, Windows DLLs, and registry dependencies in favor of pure POSIX performance, native ELF process isolation, and reliable signal handling. Running on Windows will raise a clean termination error.

---

## 📌 Project Lineage & Heritage

* **Name:** **Epimetheus** (formerly *Prometheus-WeAre-Devs-Dumper*).
* **Original Project Base:** Originates from [hutaoshusband/Prometheus-WeAre-Devs-Dumper](https://github.com/hutaoshusband/Prometheus-WeAre-Devs-Dumper). The initial project was developed as a basic Windows-only utility.
* **Obfuscation Target:** Analyzed and reverse-engineered against the official [Prometheus Obfuscator by levno-710 (v0.2.11.1)](https://github.com/prometheus-lua/Prometheus).
* **Evolution to Epimetheus:** The codebase was rebuilt from the ground up as a high-performance, Linux-only engine, introducing modular AST optimization, streaming VM noise filtration, pure Python static table decoding, and Luau syntax normalization.

---

## 🚀 Key Improvements & Architecture

### 1. Pure Linux Native Engine & Clean Binaries
* Stripped all legacy Windows PE binaries (`.exe`), Windows DLLs, and `.manifest` files.
* Uses native 64-bit ELF `lua_bin/lua5.1` binary precompiled for Linux x86_64.
* Automatic execution permission enforcement (`chmod +x`) and robust multi-tier binary resolution (`LUA51_EXECUTABLE` $\rightarrow$ `lua_bin/` $\rightarrow$ system `PATH`).
* Non-blocking POSIX process spawning with strict execution timeouts to prevent infinite VM interpreter hangs.

### 2. High-Performance Modular Sub-Engines (`engine/`)
The processing pipeline was split into dedicated, high-speed modules:

* **[`engine/syntax_normalizer.py`](engine/syntax_normalizer.py) (Luau Syntax Normalizer):**
  - **Compound Assignment Desugaring:** Automatically rewrites Luau compound assignment operators (`+=`, `-=`, `*=`, `/=`, `%=`, `^=`, `..=`) into standard Lua 5.1 syntax (e.g. `x += 5` $\rightarrow$ `x = x + 5`) while strictly preserving string literals, nested parentheses, and comments.
  - **Luau Type Annotation Stripping:** Removes type annotations (e.g. `local x: number = 1` $\rightarrow$ `local x = 1`, `type Foo = ...`) to prevent standard Lua 5.1 compiler parse errors.

* **[`engine/constant_inliner.py`](engine/constant_inliner.py) (Constant Inliner & Wrapper Unwrapper):**
  - **Prometheus ConstantArray Wrapper Inlining:** Reverse-engineers `ConstantArray.lua` wrapper functions (`local function wrapper(a) return ARR[a + offset] end`) and replaces wrapper calls (`wrapper(15)`) with inlined decoded constants directly into the deobfuscated Lua output.
  - **Proxified Locals Unwrapping:** Detects metatable-proxified local variables (`ProxifyLocals.lua`) and restores them to clean, direct local assignments.

* **[`engine/trace_filter.py`](engine/trace_filter.py) (Stream & File Trace Filter):**
  - Prometheus VM generates hundreds of thousands of redundant chunk allocations and unpack spam (`UNPACK CALLED WITH TABLE...` and `CAPTURED CHUNK STRING...`).
  - `TraceFilter` strips out internal interpreter loop noise while preserving 100% of semantic calls, Roblox services, global sets, URLs, and closure entries.
  - **Slashes trace file sizes by over 93%** (e.g. heavy ~2–2.5 MB scripts with 250,000+ VM lines compress from **~10–11 MB down to ~600–775 KB**).
  - Deduplicates tight consecutive VM loops with configurable compression limits.

* **[`engine/ast_optimizer.py`](engine/ast_optimizer.py) (AST & Token Lua Optimizer):**
  - **Constant Arithmetic Folding:** Automatically computes and simplifies Prometheus arithmetic obfuscation like `(-80732 + 80796)` $\rightarrow$ `64`, `(0x497e0 + -301009)` $\rightarrow$ `15`, scientific notation (`1e5`), power expressions (`2 ^ 8`), hexadecimal literals, and basic operators safely without risk of code execution or division-by-zero.
  - **String Concatenation Folding:** Reverses Prometheus `SplitStrings.lua` by automatically merging split literal fragments (`"game:Get" .. "Service"` $\rightarrow$ `"game:GetService"`) and static `table.concat` calls into clean, continuous string literals.
  - **Watermark Dead-Code Elimination:** Identifies and strips Prometheus `WatermarkCheck.lua` dead conditional guards.
  - **String & Comment Protection:** Multi-type lexical tokenizer guarantees that string literals (`"do not fold (-80732 + 80796)"`) and comments are never mutated.
  - **Copy Propagation & Dead Code Elimination:** Inlines redundant intermediate single-use local aliases (`local alias = service; alias:Method() -> service:Method()`) and deletes dead self-assignments (`var = var`).
  - **Whitespace Normalization:** Cleans excessive blank lines and structures output Lua code.

* **[`engine/static_decoder.py`](engine/static_decoder.py) (Static Prometheus Constant Decoder):**
  - **Pure PRNG Keystream Decryptor:** Implements the exact inverse stream cipher for Prometheus `EncryptStrings.lua`, recovering encrypted constants directly in Python without requiring VM execution.
  - Recovers string constants directly in Python without requiring VM execution.
  - Parses custom shuffled 64-character (Base64) and 85-character (Base85) substitution tables.
  - Supports Mixed encoding detection via `prefix_0` / `prefix_1` headers.
  - Detects Prometheus array rotation (`Rotate` step: `{{1, LEN}, {1, SHIFT}, ...}`) with support for mutated math/hex shift expressions, and calculates the reverse circular shift to restore true index order.
  - Serves as an instant static extractor and fallback when dynamic sandboxes encounter syntax or compilation limits.

### 3. Core Engine Revamp & Speedup
* **130x Faster Variable Resolution (`trace_to_lua.py`):** Replaced an $O(N \cdot M)$ sequential loop of `re.sub` over thousands of keys with a single compiled regular expression alternation `\b(var1|var2|...)\b`, reducing replacement time from **1.61s down to 0.012s**.
* **Global Environment Robustness:** Enhanced `MockEnv` with fallback metatable indexing for `_G` and `getfenv()`, preventing `nil` index errors when Prometheus scripts dynamically access global Roblox services.
* **Sandbox Anti-Tamper Hardening:** Added canary protection, line hook neutralization, dummy method chains, and sandbox memory limits (`LUA_MAX_SBX`) to bypass Prometheus environment checks.
* **Streamlined Workflow:** The bloated web server viewer (`viewer.py`) has been removed. Because reports and deobfuscated scripts are now cleanly compressed, they open instantaneously in any standard editor (VS Code, Sublime Text, Notepad++, vim) without lag.

---

## 🧠 Epimetheus Pipeline Workflow

```mermaid
flowchart TD
    A["Obfuscated Luau / Lua Script"] --> B["engine/syntax_normalizer.py"]
    B --> C["Linux Mock Sandbox (lua_bin/lua5.1)"]
    C -->|Dynamic Emulation| D["Raw Stdout Stream"]
    B -->|Fallback / Analysis| E["engine/static_decoder.py"]
    
    D --> F["engine/trace_filter.py"]
    F -->|Drop >92% VM Noise| G[".report.txt (Compact Clean Trace)"]
    
    G --> H["trace_to_lua.py"]
    H -->|Fast Regex Variable Resolution| I["Lua Code Reconstructor"]
    I --> J["engine/ast_optimizer.py"]
    J --> K["engine/constant_inliner.py"]
    
    K -->|Inline Constants & Unproxify Locals| L[".deobf.lua (Clean Lua Source)"]
    C -->|Dump Memory Array| M[".constants.lua (Decoded Constants)"]
    E -.->|Static Verify| M
```

---

## ⚡ Performance Benchmarks

Real-world test on production Prometheus-obfuscated scripts:

| Benchmark Metric | Original Dumper | Epimetheus Engine (v2.6.0) | Improvement |
| :--- | :--- | :--- | :--- |
| **Heavy Script (~2.1 MB) Report** | 9.93 MB (231,175 lines) | **610 KB** (10,573 lines) | **-93.8% file size** |
| **Complex Script (~2.5 MB) Report** | 10.59 MB (257,942 lines) | **775 KB** (13,158 lines) | **-92.8% file size** |
| **Variable Resolution Speed** | ~1.61 s per chunk | **0.012 s** per chunk | **134x faster** |
| **Workspace Footprint** | >26 MB | **5.9 MB** | **77% disk savings** |
| **Unit Test Suite** | 9 tests (basic) | **30 tests** (100% passing) | **Full coverage** |

---

## 📦 Requirements

* **Operating System:** Linux exclusively (Ubuntu, Debian, Fedora, Arch, Kali, Alpine, etc., x86_64).
* **Python:** Version 3.9 or higher.
* **Lua 5.1:** Precompiled native 64-bit ELF binary bundled in `lua_bin/lua5.1`. No external runtime compilation needed.

---

## 🛠️ Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/NoName2323-wq/Epimetheus.git
   cd Epimetheus
   ```

2. **Ensure executable permissions (POSIX):**
   ```bash
   chmod +x deobfuscator.py format_lua.py lua_bin/lua5.1
   ```

3. **Verify installation:**
   ```bash
   ./deobfuscator.py --version
   # or run the automated test suite:
   python3 -m unittest discover tests
   ```

---

## 💻 Usage

### Single File Deobfuscation

```bash
python3 deobfuscator.py target_script.lua
```

### Batch Directory Deobfuscation
Process all `.lua` files in a target directory:

```bash
python3 deobfuscator.py path/to/scripts_directory
```
*(If no argument is given, it defaults to checking `obfuscated_scripts/`)*

### Running Test Suite
Execute the entire regression and engine test suite:

```bash
python3 -m unittest discover tests
```

---

## 📂 Output Files Generated

For every script processed (e.g. `MyScript.lua`), Epimetheus generates:

1. **`MyScript.lua.deobf.lua`**: The reconstructed, clean Lua source code with recovered Roblox calls (`game:GetService`, `:WaitForChild`, `:Connect`), reconstructed functions, loops, global declarations, and inlined AST optimizations.
2. **`MyScript.lua.constants.lua`**: Complete table of all extracted and decoded string/number constants.
3. **`MyScript.lua.report.txt`**: The filtered, compact execution trace logging all accessed properties, method invocations, and detected URLs.

---

## 📄 License & Disclaimer

* **License:** This project is licensed under the **GNU General Public License v3.0** (GPLv3). See the [LICENSE](LICENSE) file for complete terms and conditions.
* **Disclaimer:** This software is developed strictly for reverse engineering research, cybersecurity analysis, and educational purposes. All trademarks, logos, and brand names are the property of their respective owners.
