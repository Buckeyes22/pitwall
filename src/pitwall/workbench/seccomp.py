"""Classic-BPF seccomp program that denies the network syscall family for restricted tools.

Port of ``seccompFilter`` in ``packages/pi-workbench/src/restricted.ts``. The program is a
sequence of 8-byte little-endian ``struct sock_filter`` records (code, jt, jf, k) that
``setpriv --seccomp-filter`` loads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Classic BPF opcodes used by the program.
BPF_LD_W_ABS = 0x20
BPF_ALU_AND_K = 0x54
BPF_JEQ_K = 0x15
BPF_RET_K = 0x06

# seccomp_data offsets.
OFFSET_NR = 0
OFFSET_ARCH = 4

# Return actions.
RET_KILL_PROCESS = 0x80000000
RET_ERRNO_EPERM = 0x00050001
RET_ALLOW = 0x7FFF0000

X32_SYSCALL_BIT = 0x40000000


@dataclass(frozen=True)
class _Arch:
    audit: int
    syscalls: tuple[int, ...]
    x32_guard: bool


# io_uring can issue socket operations without the classic socket syscalls, so
# setup/enter/register are denied alongside the socket family.
_ARCHITECTURES: dict[str, _Arch] = {
    "x64": _Arch(
        0xC000003E,
        (*range(41, 56), 288, 299, 307, 425, 426, 427),
        x32_guard=True,
    ),
    "arm64": _Arch(
        0xC00000B7,
        (*range(198, 213), 242, 243, 269, 417, 425, 426, 427),
        x32_guard=False,
    ),
}
_ALIASES = {"x64": "x64", "x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def supported_architecture(arch: str) -> str | None:
    """Return the canonical architecture name (``x64`` or ``arm64``), or None if unsupported."""
    return _ALIASES.get(arch.lower())


def seccomp_program(arch: str) -> bytes:
    """Build the deny-network filter for ``arch`` (x86_64/x64/amd64 or aarch64/arm64)."""
    name = supported_architecture(arch)
    if name is None:
        raise ValueError(f"restricted tool network boundary is unsupported on {arch}")
    spec = _ARCHITECTURES[name]
    instructions: list[tuple[int, int, int, int]] = [
        (BPF_LD_W_ABS, 0, 0, OFFSET_ARCH),
        (BPF_JEQ_K, 1, 0, spec.audit),  # an unexpected syscall ABI falls into the kill
        (BPF_RET_K, 0, 0, RET_KILL_PROCESS),
        (BPF_LD_W_ABS, 0, 0, OFFSET_NR),
    ]
    if spec.x32_guard:
        # x32 shares the x86_64 audit ABI with a high syscall-number tag; reject it
        # before the exact x86_64 numbers are considered.
        instructions += [
            (BPF_ALU_AND_K, 0, 0, X32_SYSCALL_BIT),
            (BPF_JEQ_K, 1, 0, 0),  # an untagged syscall skips the kill
            (BPF_RET_K, 0, 0, RET_KILL_PROCESS),
            (BPF_LD_W_ABS, 0, 0, OFFSET_NR),  # reload nr for the deny list
        ]
    for syscall in spec.syscalls:
        instructions.append((BPF_JEQ_K, 0, 1, syscall))  # a match falls through to EPERM
        instructions.append((BPF_RET_K, 0, 0, RET_ERRNO_EPERM))
    instructions.append((BPF_RET_K, 0, 0, RET_ALLOW))
    return b"".join(struct.pack("<HBBI", *instruction) for instruction in instructions)
