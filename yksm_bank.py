"""Read and write YKSM's native ``bank.ykb`` format.

The format is intentionally small and portable.  All integers are little
endian and the payload is protected by the same standard CRC-32 used by YKSM.
"""
from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass
from pathlib import Path


MAGIC = b"YKB1"
VERSION = 1
GAME_TO_CODE = {"YW1": 0, "YW2": 1, "YW3": 2, "BLASTERS": 3, "BUSTERS2": 4}
CODE_TO_GAME = {value: key for key, value in GAME_TO_CODE.items()}
RECORD_SIZES = {"YW1": 0x5C, "YW2": 0x5C, "YW3": 0x54, "BLASTERS": 0x4C, "BUSTERS2": 0x4C}


class YKSMBankError(ValueError):
    pass


@dataclass(frozen=True)
class YKSMEntry:
    ident: int
    sequence: int
    game: str
    species_id: int
    species: str
    nickname: str
    level: int
    xp: int
    raw_record: bytes


def _take(data: bytes, cursor: int, size: int) -> tuple[bytes, int]:
    end = cursor + size
    if end > len(data):
        raise YKSMBankError("YKSM bank is truncated")
    return data[cursor:end], end


def _read_blob(data: bytes, cursor: int) -> tuple[bytes, int]:
    raw, cursor = _take(data, cursor, 2)
    length = struct.unpack("<H", raw)[0]
    return _take(data, cursor, length)


def _blob(value: bytes) -> bytes:
    if len(value) >= 0x10000:
        raise YKSMBankError("YKSM bank field is too large")
    return struct.pack("<H", len(value)) + value


def decode(data: bytes) -> list[YKSMEntry]:
    if len(data) < 16 or data[:4] != MAGIC:
        raise YKSMBankError("Not a YKSM Yo-kai bank")
    version, reserved, count, expected_crc = struct.unpack_from("<HHII", data, 4)
    if version != VERSION:
        raise YKSMBankError(f"Unsupported YKSM bank version: {version}")
    if reserved != 0:
        raise YKSMBankError("Unsupported YKSM bank flags")
    payload = data[16:]
    if (binascii.crc32(payload) & 0xFFFFFFFF) != expected_crc:
        raise YKSMBankError("YKSM bank checksum does not match")
    entries: list[YKSMEntry] = []
    cursor = 16
    for _ in range(count):
        fixed, cursor = _take(data, cursor, 28)
        ident, sequence, game_code, level, reserved_entry, species_id, xp = struct.unpack("<QQBBHII", fixed)
        if game_code not in CODE_TO_GAME:
            raise YKSMBankError(f"Unsupported YKSM game code: {game_code}")
        game = CODE_TO_GAME[game_code]
        species_raw, cursor = _read_blob(data, cursor)
        nickname_raw, cursor = _read_blob(data, cursor)
        record, cursor = _read_blob(data, cursor)
        try:
            species = species_raw.decode("utf-8")
            nickname = nickname_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise YKSMBankError("YKSM bank contains invalid UTF-8") from exc
        if reserved_entry or not ident or not 1 <= level <= 99 or len(record) != RECORD_SIZES[game] or not species:
            raise YKSMBankError("YKSM bank contains an invalid Yo-kai entry")
        entries.append(YKSMEntry(ident, sequence, game, species_id, species, nickname, level, xp, record))
    if cursor != len(data):
        raise YKSMBankError("YKSM bank contains trailing data")
    return sorted(entries, key=lambda entry: entry.sequence)


def encode(entries: list[YKSMEntry]) -> bytes:
    payload = bytearray()
    seen: set[int] = set()
    for entry in entries:
        if entry.game not in GAME_TO_CODE:
            raise YKSMBankError(f"YKSM does not support {entry.game}")
        if not entry.ident or entry.ident in seen:
            raise YKSMBankError("YKSM bank entry IDs must be unique and non-zero")
        if not 1 <= entry.level <= 99 or len(entry.raw_record) != RECORD_SIZES[entry.game]:
            raise YKSMBankError("Cannot export an invalid Yo-kai record")
        seen.add(entry.ident)
        payload += struct.pack("<QQBBHII", entry.ident, entry.sequence, GAME_TO_CODE[entry.game], entry.level, 0,
                               entry.species_id & 0xFFFFFFFF, entry.xp & 0xFFFFFFFF)
        payload += _blob(entry.species.encode("utf-8"))
        payload += _blob(entry.nickname.encode("utf-8"))
        payload += _blob(entry.raw_record)
    checksum = binascii.crc32(payload) & 0xFFFFFFFF
    return MAGIC + struct.pack("<HHII", VERSION, 0, len(entries), checksum) + payload


def load(path: Path) -> list[YKSMEntry]:
    return decode(path.read_bytes())


def save(path: Path, entries: list[YKSMEntry]) -> None:
    encoded = encode(entries)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    if decode(temporary.read_bytes()) != sorted(entries, key=lambda entry: entry.sequence):
        temporary.unlink(missing_ok=True)
        raise YKSMBankError("Written YKSM bank failed verification")
    temporary.replace(path)
