"""
Static Prometheus Constant Array & String Decoder Engine Module.

Directly extracts and decodes Prometheus obfuscated constant tables (Base64, Base85, Mixed)
from Lua chunks without needing dynamic VM execution.
"""

from __future__ import annotations

from typing import List, Dict, Optional, Tuple, Any, Union
import re
import ast

_RE_DECIMAL_ESCAPE = re.compile(r"\\([0-9]{1,3})")
_RE_HEX_ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")
_BASE64_POWERS = (262144, 4096, 64, 1)


class StaticConstantDecoder:
    """
    Deobfuscates and decodes Prometheus string and constant arrays.
    """

    def __init__(self):
        pass

    @staticmethod
    def unescape_lua_string(escaped: str) -> str:
        """
        Convert Lua byte escapes (\\ddd, \\xHH, \\n, etc.) to raw string/bytes.
        """
        if "\\" not in escaped:
            return escaped

        def replace_decimal(m):
            return chr(int(m.group(1)))

        def replace_hex(m):
            return chr(int(m.group(1), 16))

        s = _RE_DECIMAL_ESCAPE.sub(replace_decimal, escaped)
        s = _RE_HEX_ESCAPE.sub(replace_hex, s)
        s = s.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
        s = s.replace(r"\\", "\\").replace(r'\"', '"').replace(r"\'", "'")
        return s

    @staticmethod
    def escape_for_lua(data: Union[bytes, str]) -> str:
        """
        Format a decoded string/byte sequence safely into a Lua string literal.
        """
        if isinstance(data, str):
            raw_bytes = data.encode("utf-8", errors="surrogateescape")
        else:
            raw_bytes = bytes(data)

        parts = ['"']
        for b in raw_bytes:
            if b == 92:  # \
                parts.append(r"\\")
            elif b == 34:  # "
                parts.append(r'\"')
            elif b == 10:  # \n
                parts.append(r"\n")
            elif b == 13:  # \r
                parts.append(r"\r")
            elif b == 9:  # \t
                parts.append(r"\t")
            elif 32 <= b <= 126:
                parts.append(chr(b))
            else:
                parts.append(f"\\{b:03d}")
        parts.append('"')
        return "".join(parts)

    def extract_array_literal(self, source: str) -> Tuple[Optional[str], List[str]]:
        """
        Extract the primary constant table: local <var> = { ... }
        Returns (var_name, [raw_string_or_value, ...])
        """
        # Find first table definition that has multiple entries
        m = re.search(r"\blocal\s+([a-zA-Z0-9_]+)\s*=\s*\{", source)
        if not m:
            return None, []

        var_name = m.group(1)
        open_idx = m.end() - 1

        # Locate matching closing brace
        depth = 0
        quote = None
        close_idx = -1
        idx = open_idx
        src_len = len(source)

        while idx < src_len:
            ch = source[idx]
            if quote:
                if ch == "\\":
                    idx += 2
                    continue
                if ch == quote:
                    quote = None
                idx += 1
                continue

            if ch in ('"', "'"):
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_idx = idx
                    break
            idx += 1

        if close_idx == -1:
            return var_name, []

        table_body = source[open_idx + 1 : close_idx]

        # Extract items: strings and numbers
        # Matches "..." or '...' or numbers
        items = []
        item_pattern = re.compile(
            r"""\"([^\"\\]*(?:\\.[^\"\\]*)*)\"|'([^'\\]*(?:\\.[^'\\]*)*)'|(-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?))"""
        )
        for im in item_pattern.finditer(table_body):
            if im.group(1) is not None:
                items.append(self.unescape_lua_string(im.group(1)))
            elif im.group(2) is not None:
                items.append(self.unescape_lua_string(im.group(2)))
            elif im.group(3) is not None:
                items.append(im.group(3))

        return var_name, items

    def extract_lookup_tables(self, source: str) -> Dict[str, Dict[str, int]]:
        """
        Find and extract Base64 (64 keys) and Base85 (85 keys) lookup tables.
        Returns dict: {'base64': {...}, 'base85': {...}}
        """
        results: Dict[str, Dict[str, int]] = {}

        # Scan for tables with character mappings
        # Pattern: local <var> = { ... }
        table_matches = re.finditer(r"\blocal\s+([a-zA-Z0-9_]+)\s*=\s*\{([^}]+)\}", source)
        for tm in table_matches:
            body = tm.group(2)
            # Find [ "char" ] = expr or ident = expr
            entry_matches = re.findall(
                r"""(?:\[\s*\"([^\"]+)\"\s*\]|\[\s*'([^']+)'\s*\]|([a-zA-Z0-9_]+))\s*=\s*([0-9+\-*() -]+)""",
                body,
            )
            if len(entry_matches) not in (64, 85):
                continue

            lookup: Dict[str, int] = {}
            valid = True
            for k1, k2, k3, val_expr in entry_matches:
                raw_k = k1 or k2 or k3
                if raw_k.startswith("\\"):
                    try:
                        k_char = chr(int(raw_k[1:]))
                    except ValueError:
                        k_char = raw_k
                else:
                    k_char = raw_k

                # Evaluate math expression
                try:
                    val = eval(val_expr, {"__builtins__": None}, {})
                    lookup[k_char] = int(val)
                except Exception:
                    valid = False
                    break

            if valid and len(lookup) == 64:
                results["base64"] = lookup
            elif valid and len(lookup) == 85:
                results["base85"] = lookup

        return results

    def extract_mixed_prefixes(self, source: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract prefix_0 and prefix_1 for Mixed encoding:
        Pattern: if first == "..." then ... elseif first == "..." then ...
        """
        m = re.findall(r"""\bfirst\s*==\s*\"([^\"]+)\"""", source)
        if len(m) >= 2:
            return self.unescape_lua_string(m[0]), self.unescape_lua_string(m[1])
        return None, None

    def detect_rotate(self, source: str) -> Optional[Tuple[int, int]]:
        """
        Detect rotate step: {{1, LEN}, {1, SHIFT}, {SHIFT + 1, LEN}}
        Returns (shift, length) or None. Handles hex and arithmetic expressions.
        """
        m = re.search(
            r"\{\s*1\s*,\s*([^\}]+?)\s*\}\s*,\s*\{\s*1\s*,\s*([^\}]+?)\s*\}\s*,\s*\{\s*([^\}]+?)\s*,\s*([^\}]+?)\s*\}",
            source,
        )
        if m:
            raw_len1, raw_shift = m.group(1).strip(), m.group(2).strip()
            # Evaluate numbers or hex/arithmetic expressions
            try:
                from engine.ast_optimizer import safe_eval_math_expr
                len1 = int(raw_len1, 0) if (raw_len1.isdigit() or raw_len1.startswith(("0x", "0X"))) else safe_eval_math_expr(raw_len1)
                shift = int(raw_shift, 0) if (raw_shift.isdigit() or raw_shift.startswith(("0x", "0X"))) else safe_eval_math_expr(raw_shift)
                if len1 is not None and shift is not None:
                    return int(shift), int(len1)
            except Exception:
                pass
        return None

    @staticmethod
    def decrypt_prometheus_stream(
        encrypted: bytes,
        seed: int,
        param_mul_45: int,
        param_add_45: int,
        param_mul_8: int,
        secret_key_8: int,
    ) -> bytes:
        """
        Exact inverse of Prometheus v0.2.11.1 EncryptStrings.lua PRNG keystream cipher.
        """
        state_45 = seed % 35184372088832
        state_8 = seed % 255 + 2
        prev_values: List[int] = []

        def get_next_pseudo_random_byte() -> int:
            nonlocal state_45, state_8, prev_values
            if not prev_values:
                state_45 = (state_45 * param_mul_45 + param_add_45) % 35184372088832
                while True:
                    state_8 = (state_8 * param_mul_8) % 257
                    if state_8 != 1:
                        break
                r = state_8 % 32
                shift = 13 - (state_8 - r) // 32
                if shift >= 0:
                    n_base = (state_45 // (1 << shift)) % 4294967296
                else:
                    n_base = (state_45 * (1 << (-shift))) % 4294967296
                n = n_base / (1 << r)
                rnd = int((n % 1.0) * 4294967296) + int(n)
                low_16 = rnd % 65536
                high_16 = (rnd - low_16) // 65536
                prev_values = [
                    low_16 % 256,
                    (low_16 - low_16 % 256) // 256,
                    high_16 % 256,
                    (high_16 - high_16 % 256) // 256,
                ]
            return prev_values.pop()

        out = bytearray()
        prev_val = secret_key_8
        for b in encrypted:
            prev_val = (b + get_next_pseudo_random_byte() + prev_val) % 256
            out.append(prev_val)
        return bytes(out)

    def extract_encryption_params(self, source: str) -> Optional[Dict[str, int]]:
        """
        Extract stream cipher parameters generated by Prometheus EncryptStrings step.
        """
        m_45 = re.search(
            r"state_45\s*=\s*\(\s*state_45\s*\*\s*(\d+)\s*\+\s*(\d+)\s*\)\s*%\s*35184372088832",
            source,
        )
        m_8 = re.search(r"state_8\s*=\s*state_8\s*\*\s*(\d+)\s*%\s*257", source)
        m_key = re.search(r"local\s+prevVal\s*=\s*(\d+)", source)
        if m_45 and m_8 and m_key:
            return {
                "param_mul_45": int(m_45.group(1)),
                "param_add_45": int(m_45.group(2)),
                "param_mul_8": int(m_8.group(1)),
                "secret_key_8": int(m_key.group(1)),
            }
        return None

    def unrotate_array(self, items: List[Any], shift: int, length: int) -> List[Any]:
        """
        Reverse the Prometheus rotation algorithm:
        Prometheus rotates by moving elements right by d.
        Reversing it shifts elements left by d: arr[d:] + arr[:d].
        """
        n = min(length, len(items))
        if n <= 1:
            return list(items)

        d = shift % n
        arr = list(items[:n])
        unrotated = arr[d:] + arr[:d]
        return unrotated + list(items[n:])

    def decode_base64(self, data: str, lookup: Dict[str, int]) -> bytes:
        """
        Decode Prometheus custom Base64 string.
        """
        length = len(data)
        parts = bytearray()
        index = 0
        value = 0
        count = 0

        while index < length:
            char = data[index]
            if char in lookup:
                code = lookup[char]
                value += code * _BASE64_POWERS[count]
                count += 1
                if count == 4:
                    count = 0
                    c1 = value // 65536
                    c2 = (value % 65536) // 256
                    c3 = value % 256
                    parts.extend([c1, c2, c3])
                    value = 0
            elif char == "=":
                parts.append(value // 65536)
                if index >= length - 1 or data[index + 1] != "=":
                    parts.append((value % 65536) // 256)
                break
            index += 1

        return bytes(parts)

    def decode_base85(self, data: str, lookup: Dict[str, int]) -> bytes:
        """
        Decode Prometheus custom Base85 string.
        """
        length = len(data)
        parts = bytearray()
        index = 0

        while index < length:
            remain = length - index
            count = 5 if remain >= 5 else remain
            value = 0
            valid = count > 1

            for j in range(5):
                if j < count:
                    ch = data[index + j]
                    if ch not in lookup:
                        valid = False
                        break
                    code = lookup[ch]
                else:
                    code = 84
                value = value * 85 + code

            if valid:
                b1 = (value // 16777216) % 256
                b2 = (value // 65536) % 256
                b3 = (value // 256) % 256
                b4 = value % 256
                if count == 5:
                    parts.extend([b1, b2, b3, b4])
                elif count == 4:
                    parts.extend([b1, b2, b3])
                elif count == 3:
                    parts.extend([b1, b2])
                elif count == 2:
                    parts.append(b1)

            index += count

        return bytes(parts)

    def decode_string(
        self,
        encoded: str,
        lookup64: Optional[Dict[str, int]] = None,
        lookup85: Optional[Dict[str, int]] = None,
        prefix_0: Optional[str] = None,
        prefix_1: Optional[str] = None,
    ) -> Union[bytes, str]:
        """
        Decode a single encoded Prometheus string using available lookups and prefixes.
        """
        if not encoded:
            return b""

        # Mixed encoding check
        if prefix_0 and encoded.startswith(prefix_0) and lookup64:
            return self.decode_base64(encoded[len(prefix_0) :], lookup64)
        if prefix_1 and encoded.startswith(prefix_1) and lookup85:
            return self.decode_base85(encoded[len(prefix_1) :], lookup85)

        # Default to Base64 if available
        if lookup64:
            try:
                res = self.decode_base64(encoded, lookup64)
                if res:
                    return res
            except Exception:
                pass

        # Fallback to Base85 if available
        if lookup85:
            try:
                res = self.decode_base85(encoded, lookup85)
                if res:
                    return res
            except Exception:
                pass

        return encoded

    def decode_all(self, source: str) -> List[Union[bytes, str]]:
        """
        Full static extraction & decoding pipeline for a Prometheus Lua script.
        """
        var_name, items = self.extract_array_literal(source)
        if not items:
            return []

        lookups = self.extract_lookup_tables(source)
        lookup64 = lookups.get("base64")
        lookup85 = lookups.get("base85")
        prefix_0, prefix_1 = self.extract_mixed_prefixes(source)

        # Check rotation
        rotate_info = self.detect_rotate(source)
        if rotate_info:
            shift, length = rotate_info
            items = self.unrotate_array(items, shift, min(length, len(items)))

        decoded: List[Union[bytes, str]] = []
        for item in items:
            if isinstance(item, str):
                decoded.append(
                    self.decode_string(
                        item,
                        lookup64=lookup64,
                        lookup85=lookup85,
                        prefix_0=prefix_0,
                        prefix_1=prefix_1,
                    )
                )
            else:
                decoded.append(item)

        return decoded

    def format_constants_table(
        self, constants: List[Union[bytes, str]], var_name: str = "Constants"
    ) -> str:
        """
        Format decoded constants into Lua table source.
        """
        lines = [f"local {var_name} = {{"]
        for idx, val in enumerate(constants, start=1):
            if isinstance(val, (bytes, bytearray)):
                formatted = self.escape_for_lua(val)
            elif isinstance(val, str):
                formatted = self.escape_for_lua(val)
            else:
                formatted = str(val)
            lines.append(f"[{idx}] = {formatted},")
        lines.append("}")
        return "\n".join(lines) + "\n"


def decode_prometheus_constants(lua_source: str) -> str:
    """Convenience helper to extract and decode constants into Lua table syntax."""
    decoder = StaticConstantDecoder()
    decoded = decoder.decode_all(lua_source)
    return decoder.format_constants_table(decoded)
