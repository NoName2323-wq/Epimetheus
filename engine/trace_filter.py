"""
Trace Filter Engine Module for Prometheus Deobfuscator.

High-throughput streaming filter that strips redundant VM interpreter loops,
internal opcode dispatches, and table unpacking spam from sandbox trace reports.
"""

from typing import Iterable, Iterator, Sequence, Optional, Dict, Any
import os
import re


DEFAULT_PRESERVED_PREFIXES = (
    "CALL_RESULT -->",
    "SET GLOBAL -->",
    "TRACE_PRINT -->",
    "URL DETECTED",
    "--- ENTERING CLOSURE",
    "--- EXITING CLOSURE",
    "ACCESSED -->",
    "LOADSTRING DETECTED",
    "LOADSTRING CONTENT",
    "PROP_SET -->",
    "local Constants =",
    "--- CONSTANTS",
    "--- TRACE",
    "--- DEOBFUSCATION REPORT",
    "File:",
)

DEFAULT_DROPPED_PREFIXES = (
    "UNPACK CALLED WITH TABLE",
    "CAPTURED CHUNK STRING",
    "DEBUG:",
)


_RE_CONST_ENTRY = re.compile(r"^\[\d+\]\s*=")


class TraceFilter:
    """
    Intelligent streaming and batch filter for runtime execution traces.
    """

    def __init__(
        self,
        preserved_prefixes: Optional[Sequence[str]] = None,
        dropped_prefixes: Optional[Sequence[str]] = None,
        max_consecutive_duplicates: int = 5,
        compress_duplicates: bool = True,
        track_stats: bool = False,
    ):
        self.preserved_prefixes = tuple(preserved_prefixes or DEFAULT_PRESERVED_PREFIXES)
        self.dropped_prefixes = tuple(dropped_prefixes or DEFAULT_DROPPED_PREFIXES)
        self.max_consecutive_duplicates = max_consecutive_duplicates
        self.compress_duplicates = compress_duplicates
        self.track_stats = track_stats

        # Statistics
        self.lines_processed = 0
        self.lines_preserved = 0
        self.lines_dropped = 0
        self.duplicates_compressed = 0
        self.bytes_in = 0
        self.bytes_out = 0

    def reset_stats(self) -> None:
        self.lines_processed = 0
        self.lines_preserved = 0
        self.lines_dropped = 0
        self.duplicates_compressed = 0
        self.bytes_in = 0
        self.bytes_out = 0

    def is_relevant(self, line: str, clean: Optional[str] = None) -> bool:
        """
        Determine whether a raw trace line contains meaningful semantic operations.
        """
        line_clean = clean if clean is not None else line.strip()
        if not line_clean:
            return False

        for dropped in self.dropped_prefixes:
            if dropped in line_clean:
                return False

        for preserved in self.preserved_prefixes:
            if preserved in line_clean:
                return True

        # In constants table (e.g. '[1] = "foo"')
        if _RE_CONST_ENTRY.match(line_clean) or line_clean.startswith("}"):
            return True

        return False

    def filter_stream(self, lines: Iterable[str]) -> Iterator[str]:
        """
        Yields filtered trace lines lazily while squashing repetitive VM loop spam.
        """
        last_line: Optional[str] = None
        consecutive_count = 0
        in_constants = False
        track = self.track_stats

        for raw_line in lines:
            line = raw_line.rstrip("\r\n")
            self.lines_processed += 1
            if track:
                line_bytes = len(line.encode("utf-8", errors="replace")) + 1
                self.bytes_in += line_bytes

            stripped = line.strip()

            if stripped in ("--- CONSTANTS START ---", "--- CONSTANTS ---"):
                in_constants = True
            elif stripped in ("--- CONSTANTS END ---",):
                in_constants = False

            if in_constants:
                self.lines_preserved += 1
                if track:
                    self.bytes_out += line_bytes
                yield line
                continue

            if not self.is_relevant(line, clean=stripped):
                self.lines_dropped += 1
                continue

            # Check consecutive identical operations (e.g. tight VM loops)
            if self.compress_duplicates and stripped == last_line:
                consecutive_count += 1
                if consecutive_count > self.max_consecutive_duplicates:
                    self.duplicates_compressed += 1
                    self.lines_dropped += 1
                    continue
            else:
                consecutive_count = 1
                last_line = stripped

            self.lines_preserved += 1
            if track:
                self.bytes_out += line_bytes
            yield line

    def filter_lines(self, lines: Sequence[str]) -> list[str]:
        """
        Batch filter a collection of trace lines.
        """
        return list(self.filter_stream(lines))

    def filter_file(self, input_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Stream-filter a trace report file on disk.
        """
        if output_path is None:
            output_path = input_path + ".tmp"
            replace_original = True
        else:
            replace_original = False

        self.reset_stats()
        with open(input_path, "r", encoding="utf-8", errors="replace") as fin, \
             open(output_path, "w", encoding="utf-8") as fout:
            for filtered_line in self.filter_stream(fin):
                fout.write(filtered_line + "\n")

        if replace_original:
            os.replace(output_path, input_path)
            target_path = input_path
        else:
            target_path = output_path

        return self.get_stats(target_path)

    def get_stats(self, target_path: str = "") -> Dict[str, Any]:
        savings_pct = 0.0
        if self.bytes_in > 0:
            savings_pct = round((1.0 - (self.bytes_out / self.bytes_in)) * 100.0, 2)

        return {
            "target": target_path,
            "lines_processed": self.lines_processed,
            "lines_preserved": self.lines_preserved,
            "lines_dropped": self.lines_dropped,
            "duplicates_compressed": self.duplicates_compressed,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "reduction_percent": savings_pct,
        }


def filter_trace_stream(lines: Iterable[str], max_consecutive_duplicates: int = 5) -> Iterator[str]:
    """Convenience generator for streaming trace filtering."""
    flt = TraceFilter(max_consecutive_duplicates=max_consecutive_duplicates)
    yield from flt.filter_stream(lines)


def filter_trace_lines(lines: Sequence[str], max_consecutive_duplicates: int = 5) -> list[str]:
    """Convenience function for in-memory list filtering."""
    flt = TraceFilter(max_consecutive_duplicates=max_consecutive_duplicates)
    return flt.filter_lines(lines)
