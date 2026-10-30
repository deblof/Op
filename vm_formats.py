"""Route recognized static patterns to the matching analysis; never run Lua."""

from dataclasses import dataclass
import re
import threading

from gist_lifter import lift as lift_gist

from aryq_inspector import AryqReport, inspect_aryq
from patterns import decode_addition_table
from prometheus_runner import analyze_dispatcher
from vm_inspector import VMReport, inspect_vm

_GIST_LIFT = threading.BoundedSemaphore(1)

# A header is only a hint, not proof that the script is a particular VM format.
_LURAPH_HEADER = re.compile(
    r'\A\s*--[ \t]*This file was protected using Luraph Obfuscator v(\d+)(?:\.(\d+))?',
    re.ASCII | re.IGNORECASE,
)


@dataclass(frozen=True)
class VMAnalysis:
    kind: str
    label: str
    report: VMReport | AryqReport | None = None
    source: str | None = None
    count: int = 0
    structured: int = 0
    runtime_assumption: bool = False


def analyze_vm(source: str) -> VMAnalysis | None:
    """Try only independently verified handlers, then report recognized headers."""
    # The binary reader itself checks an exact interpreter fingerprint.
    report = inspect_vm(source)
    if report is not None:
        if not _GIST_LIFT.acquire(blocking=False):
            return VMAnalysis('busy', 'VM translation')
        try:
            try:
                recovered = lift_gist(source)
            except (ValueError, TypeError, IndexError, OverflowError, SyntaxError, RecursionError):
                recovered = None
            if recovered is not None:
                output, structured, _ = recovered
                return VMAnalysis('translated', 'known Gist VM', report=report, source=output,
                                  count=report.functions, structured=structured)
        finally:
            _GIST_LIFT.release()
        return VMAnalysis('report', 'known VM interpreter', report=report)
    aryq = inspect_aryq(source)
    if aryq == 'busy':
        return VMAnalysis('busy', 'VM analysis')
    if aryq is not None:
        if aryq.readable is not None:
            return VMAnalysis('translated', 'exact Aryq sample', report=aryq, source=aryq.readable,
                              count=aryq.constant_blocks, structured=aryq.structured, runtime_assumption=True)
        output, count = decode_addition_table(source)
        return VMAnalysis('aryq', 'exact Aryq sample', report=aryq, source=output, count=count)
    # This matcher verifies the complete helper structure and literal table.
    output, count = decode_addition_table(source)
    if count:
        return VMAnalysis('partial', 'byte-addition table', source=output, count=count)
    marker = _LURAPH_HEADER.match(source[:500])
    if marker:
        version = marker.group(1) + ('.' + marker.group(2) if marker.group(2) else '')
        return VMAnalysis('unsupported', f'Luraph v{version}')
    converted = analyze_dispatcher(source)
    if converted == 'busy':
        return VMAnalysis('busy', 'VM analysis')
    if converted:
        output, blocks = converted
        return VMAnalysis('dispatcher', 'Prometheus-style VM dispatcher', source=output, count=blocks)
    return None
