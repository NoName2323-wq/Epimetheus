#!/usr/bin/env python3
"""
Safe Lua Pretty-Formatter for monolithic 1-line or heavily packed scripts.
Reformats Lua code into clean multi-line code with indentation WITHOUT:
- modifying any string constants
- modifying escape sequences
- deleting any statements or logic
"""

import sys
import os
import re


def safe_beautify_lua(code, max_indent=8):
    token_pattern = re.compile(
        r'(--\[(=*)\[[\s\S]*?\]\2\]|--[^\n]*)|'  # 1: Comments
        r'(\"(?:[^\"\\\\]|\\\\.)*\"|\'(?:[^\'\\\\]|\\\\.)*\'|\[(=*)\[[\s\S]*?\]\4\])|'  # 3: Strings
        r'(\b(?:and|break|do|else|elseif|end|false|for|function|if|in|local|nil|not|or|repeat|return|then|true|until|while)\b)|'  # 5: Keywords
        r'([a-zA-Z_][a-zA-Z0-9_]*)|'  # 6: Identifiers
        r'(0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|'  # 7: Numbers
        r'(==|~=|<=|>=|\.\.\.|\.\.|\+=|-=|\*=|/=|%=|\^=|\.\.=|->)|'  # 8: Operators
        r'([;,{}()[\]+\-*/%^#<>=])|'  # 9: Symbols
        r'(\s+)|'  # 10: Whitespace
        r'(.)'  # 11: Other
    )

    out_lines = []
    curr = []
    indent = 0

    def flush(new_indent=None):
        nonlocal curr, indent
        if curr:
            s = ''.join(curr).strip()
            if s:
                lvl = min(indent, max_indent)
                out_lines.append(('    ' * lvl) + s)
            curr = []
        if new_indent is not None:
            indent = max(0, new_indent)

    for m in token_pattern.finditer(code):
        comment, _, string, _, keyword, ident, number, op, sym, ws, other = m.groups()
        tok = m.group(0)

        if ws:
            continue

        if keyword in ('end', 'until', 'else', 'elseif') or sym == '}':
            flush(indent - 1)

        curr.append(tok)

        if keyword in ('then', 'do', 'repeat', 'else', 'elseif'):
            flush(indent + 1)
        elif keyword == 'end':
            flush()
        elif sym == ';':
            flush()
        elif sym == '{':
            curr.append(' ')
            if len(''.join(curr)) > 60:
                flush(indent + 1)
        elif sym == ',':
            curr.append(' ')
            if len(''.join(curr)) > 80:
                flush()
        elif keyword in ('local', 'return', 'function', 'if', 'while', 'for', 'in', 'and', 'or', 'not'):
            curr.append(' ')
        elif op in ('=', '==', '~=', '<=', '>=', '+=', '-=', '*=', '/='):
            if len(curr) >= 2 and curr[-2] != ' ':
                curr.insert(-1, ' ')
            curr.append(' ')

    flush()
    return '\n'.join(out_lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 format_lua.py <input.lua> [output.lua]")
        print("Example: python3 format_lua.py Premium.lua Premium.formatted.lua")
        sys.exit(1)

    input_file = sys.argv[1]
    if not os.path.isfile(input_file):
        print(f"Error: file '{input_file}' not found.")
        sys.exit(1)

    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file.replace(".lua", ".formatted.lua")
    if output_file == input_file:
        output_file = input_file + ".formatted.lua"

    print(f"Formatting {input_file} -> {output_file}...")
    with open(input_file, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    formatted = safe_beautify_lua(content)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(formatted)

    orig_lines = content.count("\n") + 1
    new_lines = formatted.count("\n") + 1
    print(f"Done! Lines before: {orig_lines:,}, after: {new_lines:,}.")
    print(f"Saved: {output_file}")


if __name__ == "__main__":
    main()
