"""Decode the deny-network seccomp program instruction by instruction."""

from __future__ import annotations

import struct

import pytest

from pitwall.workbench.seccomp import seccomp_program

LD_ABS, AND_K, JEQ_K, RET_K = 0x20, 0x54, 0x15, 0x06
KILL, EPERM, ALLOW = 0x80000000, 0x00050001, 0x7FFF0000

X64_DENIED = [*range(41, 56), 288, 299, 307, 425, 426, 427]
ARM64_DENIED = [*range(198, 213), 242, 243, 269, 417, 425, 426, 427]


def decode(program: bytes) -> list[tuple[int, int, int, int]]:
    assert len(program) % 8 == 0
    return [struct.unpack("<HBBI", program[i : i + 8]) for i in range(0, len(program), 8)]


def deny_pairs(denied: list[int]) -> list[tuple[int, int, int, int]]:
    pairs: list[tuple[int, int, int, int]] = []
    for number in denied:
        pairs += [(JEQ_K, 0, 1, number), (RET_K, 0, 0, EPERM)]
    return pairs


@pytest.mark.parity
def test_program_decodes_to_policy() -> None:
    """Source: restricted.ts seccompFilter (x64)."""
    expected = [
        (LD_ABS, 0, 0, 4),
        (JEQ_K, 1, 0, 0xC000003E),
        (RET_K, 0, 0, KILL),
        (LD_ABS, 0, 0, 0),
        (AND_K, 0, 0, 0x40000000),
        (JEQ_K, 1, 0, 0),
        (RET_K, 0, 0, KILL),
        (LD_ABS, 0, 0, 0),
        *deny_pairs(X64_DENIED),
        (RET_K, 0, 0, ALLOW),
    ]
    for arch in ("x86_64", "x64", "amd64"):
        assert decode(seccomp_program(arch)) == expected


def test_arm64_program_decodes_to_policy() -> None:
    expected = [
        (LD_ABS, 0, 0, 4),
        (JEQ_K, 1, 0, 0xC00000B7),
        (RET_K, 0, 0, KILL),
        (LD_ABS, 0, 0, 0),
        *deny_pairs(ARM64_DENIED),
        (RET_K, 0, 0, ALLOW),
    ]
    assert decode(seccomp_program("aarch64")) == expected
    assert seccomp_program("arm64") == seccomp_program("aarch64")


def test_x32_rejected() -> None:
    """The x32 tag test precedes every deny-list comparison and kills the process."""
    instructions = decode(seccomp_program("x86_64"))
    tag = instructions.index((AND_K, 0, 0, 0x40000000))
    assert instructions[tag + 1] == (JEQ_K, 1, 0, 0)  # untagged skips the kill
    assert instructions[tag + 2] == (RET_K, 0, 0, KILL)
    first_deny = instructions.index((JEQ_K, 0, 1, 41))
    assert tag < first_deny


def test_unknown_arch_killed() -> None:
    """A foreign audit arch falls through the JEQ into the kill; unsupported hosts refuse to build."""
    instructions = decode(seccomp_program("x86_64"))
    assert instructions[:3] == [(LD_ABS, 0, 0, 4), (JEQ_K, 1, 0, 0xC000003E), (RET_K, 0, 0, KILL)]
    with pytest.raises(ValueError, match="unsupported on riscv64"):
        seccomp_program("riscv64")


def test_io_uring_and_extra_socket_calls_denied() -> None:
    denied = {k for code, _, _, k in decode(seccomp_program("x86_64")) if code == JEQ_K}
    assert {288, 299, 307, 425, 426, 427} <= denied
