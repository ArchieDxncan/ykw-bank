"""Yo-kai Watch 1 save encryption.

Derived from togenyan/yw_save (MIT License, Copyright 2016 togenyan).
The complete license is included in THIRD_PARTY_LICENSES.md.
"""
from __future__ import annotations

import binascii
import struct

ODD_PRIMES = [n for n in range(3, 1622, 2) if all(n % d for d in range(3, int(n ** 0.5) + 1, 2))][:256]


class Xorshift:
    def __init__(self, seed: int):
        self.states = [0x6C078966, 0xDD5254A5, 0xB9523B81, 0x03DF95B3]
        if seed:
            for index, add in enumerate((1, 2, 3)):
                seed ^= seed >> 30
                seed = ((seed * (0x6C078966 - 1)) + add) & 0xFFFFFFFF
                self.states[index] = seed

    def next(self, modulus: int) -> int:
        x, y = self.states[0], self.states[3]
        self.states[:3] = self.states[1:]
        x ^= (x << 11) & 0xFFFFFFFF; x ^= x >> 8; y ^= y >> 19
        self.states[3] = (x ^ y) & 0xFFFFFFFF
        return self.states[3] % modulus if modulus else self.states[3]


class YWCipher(Xorshift):
    def __init__(self, seed: int, count: int = 0x1000):
        self.table = list(range(256)); super().__init__(seed)
        for _ in range(count):
            r = self.next(0x10000); r1, r2 = r & 0xFF, (r >> 8) & 0xFF
            if r1 != r2:
                a, b = self.table[r1], self.table[r2]
                self.table[a], self.table[b] = self.table[b], self.table[a]

    def apply(self, data: bytes) -> bytes:
        out = bytearray()
        for i, value in enumerate(data):
            if i % 0x100 == 0: key_a = ODD_PRIMES[self.table[(i & 0xFF00) >> 8]]
            key_b = self.table[key_a * (i + 1) & 0xFF]
            out.append(value ^ key_b)
        return bytes(out)


def is_encrypted_yw1(data: bytes) -> bool:
    return len(data) >= 8 and (binascii.crc32(data[:-8]) & 0xFFFFFFFF) == struct.unpack_from("<I", data, len(data)-8)[0]


def decrypt_yw1(data: bytes) -> bytes:
    if not is_encrypted_yw1(data):
        raise ValueError("YW1 encrypted-save checksum does not match")
    seed = struct.unpack_from("<I", data, len(data)-4)[0]
    return YWCipher(seed).apply(data[:-8]) + data[-8:]


def encrypt_yw1(data: bytes) -> bytes:
    if len(data) < 8: raise ValueError("YW1 save is too small")
    seed = struct.unpack_from("<I", data, len(data)-4)[0]
    out = bytearray(YWCipher(seed).apply(data[:-8]) + data[-8:])
    struct.pack_into("<I", out, len(out)-8, binascii.crc32(out[:-8]) & 0xFFFFFFFF)
    return bytes(out)
