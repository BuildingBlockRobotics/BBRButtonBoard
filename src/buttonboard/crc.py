"""CRC-8/MAXIM and CRC-16/CCITT-FALSE, the Python side of `shared/crc.h`.

Bitwise and table-free for the same reason the C is: the vectors in
PROTOCOL.md §2.2 and §7 are the contract, and a table is a second place for
the polynomial to be wrong. `tools/crc_test.c` checks the C against those
vectors; `test_buttonboard.py` checks this against the C's own results.

CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final xor)
is what a firmware image is stamped with for `flash` (§9.4).
"""
from __future__ import annotations


def crc8_maxim(data):
    """CRC-8/MAXIM (poly 0x31 reflected to 0x8C, init 0x00). Frame CRC, §2.2."""
    crc = 0x00
    for b in bytearray(data):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def crc16_ccitt_false(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). Image CRC, §7 and §9.4."""
    crc = 0xFFFF
    for b in bytearray(data):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
