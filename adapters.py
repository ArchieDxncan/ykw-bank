"""Verified Yo-kai Watch main-series and Busters 2 save adapters."""
from __future__ import annotations

import base64
import shutil
import struct
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from save_crypto import decrypt_yw1, encrypt_yw1, is_encrypted_yw1
from crypto_all import (decrypt_original, encrypt_original,
                        is_blasters_section_order_valid,
                        is_busters2_section_order_valid,
                        is_yw3_section_order_valid)
try:
    from yw1_dump import youkai as YW1_NAMES
except ImportError:
    YW1_NAMES = {}

YW1_OFFSET = 0x1D08
YW1_SLOTS = 240
YW1_RECORD_SIZE = 0x5C
MIN_YW1_SIZE = YW1_OFFSET + YW1_SLOTS * YW1_RECORD_SIZE

# Common IDs confirmed by the published YW1 dumper. Unknown IDs remain usable.
class AdapterError(RuntimeError):
    pass

NATIVE_LAYOUTS = {
    "YW2": {"slots": 406, "size": 0x5C, "level": 0x4F, "xp": 0x34, "xml": "yw2_species.xml"},
    "YW3": {"slots": 656, "size": 0x54, "level": 0x49, "xp": 0x28, "xml": "yw3_species.xml"},
    "BLASTERS": {"slots": 418, "size": 0x4C, "level": 0x49, "xp": 0x38,
                 "nickname_end": 0x24, "xml": "blasters_species.xml"},
    "BUSTERS2": {"slots": 766, "size": 0x4C, "level": 0x48, "xp": None,
                 "xml": "busters2_species.xml"},
}

def _native_layout(game: str, decrypted: bytes | bytearray | None=None) -> dict:
    layout=dict(NATIVE_LAYOUTS[game])
    if game=="BLASTERS" and decrypted is not None:
        body=bytes(decrypted[0x20:-8]); _,size=_section(body,0x07)
        if size%layout["size"]: raise AdapterError("Blasters Yo-kai section has an invalid size.")
        layout["slots"]=size//layout["size"]
    return layout

def _species_map(game: str) -> dict[int, str]:
    path = Path(__file__).resolve().parent / "data" / NATIVE_LAYOUTS[game]["xml"]
    return {int(item.attrib["id"]): item.attrib["name"] for item in ET.parse(path).getroot()}

def _section(data: bytes, section_id: int, minimum_size: int = 0) -> tuple[int, int]:
    for off in range(0, len(data) - 8):
        h1, h2 = struct.unpack_from("<II", data, off)
        size = h2 >> 8
        if h1 & 0xFFFF == 0xFFFE and h2 & 0xFF == section_id and size >= minimum_size:
            end = off + 8 + size
            if end + 4 <= len(data) and struct.unpack_from("<I", data, end)[0] & 0xFFFF == 0xFEFF:
                return off + 8, size
    raise AdapterError(f"Required save section {section_id:#x} was not found.")

def _parse_native(game: str, path: Path) -> tuple[list[dict], bytes, str, int]:
    try: decrypted, variant = decrypt_original(game, path)
    except RuntimeError as exc: raise AdapterError(str(exc)) from exc
    layout = _native_layout(game,decrypted); body = decrypted[0x20:-8]
    start, size = _section(body, 0x07, layout["slots"] * layout["size"])
    names = _species_map(game); rows = []
    for slot in range(layout["slots"]):
        pos = start + slot * layout["size"]; record = body[pos:pos + layout["size"]]
        species_id = struct.unpack_from("<I", record, 4)[0]
        if species_id == 0: continue
        if species_id not in names and game!="BLASTERS":
            raise AdapterError(f"Unrecognized {game} species ID {species_id} in slot {slot + 1}.")
        level = record[layout["level"]]
        if not 1 <= level <= 99: raise AdapterError(f"Invalid {game} level in slot {slot + 1}.")
        nickname = _nickname(record[8:layout.get("nickname_end",0x20)])
        species=names.get(species_id,f"Unknown #{species_id}")
        rows.append({"slot":slot,"species_id":species_id,"species":species,
                     "name":nickname or species,"nickname":nickname,"level":level,
                     "xp":(max(0,struct.unpack_from("<i" if game=="BLASTERS" else "<I",record,layout["xp"])[0])
                           if layout["xp"] is not None else 0),
                     "raw_record":base64.b64encode(record).decode("ascii"),"crypto_variant":variant})
    return rows, decrypted, variant, start

def parse_native(game: str, path: Path) -> list[dict]:
    return _parse_native(game, path)[0]

def _write_verified(game: str, path: Path, decrypted: bytes, variant: str) -> None:
    try: encrypted = encrypt_original(game, decrypted, path, variant)
    except Exception as exc: raise AdapterError(f"Could not encrypt {game}: {exc}") from exc
    if not encrypted: raise AdapterError(f"Could not encrypt {game} save.")
    path.write_bytes(encrypted)
    try: check, detected = decrypt_original(game, path)
    except Exception as exc: raise AdapterError(f"Written {game} save failed verification: {exc}") from exc
    if detected != variant: raise AdapterError(f"Written {game} save changed encryption variant unexpectedly.")
    if game=="YW3" and not is_yw3_section_order_valid(check):
        raise AdapterError("Written YW3 save has an invalid section sequence.")
    if game=="BUSTERS2" and not is_busters2_section_order_valid(check):
        raise AdapterError("Written Busters 2 save has an invalid section sequence.")
    if game=="BLASTERS" and not is_blasters_section_order_valid(check):
        raise AdapterError("Written Blasters save has an invalid section sequence.")
    # The official validator may repair section ordering/checksums during
    # encryption, so semantic verification is performed by parsing the output.
    _parse_native(game, path)

def _sync_native_index(game: str, data: bytearray, slot: int, removed_number: bytes | None=None) -> None:
    layout=_native_layout(game,data); body=bytes(data[0x20:-8])
    records,_=_section(body,0x07,layout["slots"]*layout["size"])
    index,_=_section(body,0x0A,layout["slots"]*4)
    rec=0x20+records+slot*layout["size"]
    if game in ("BLASTERS","BUSTERS2"):
        # The action games' section 0A is an ordered list of record numbers, not a
        # slot-parallel table. Remove the matching number or append the new
        # number at the first free list position.
        wanted=removed_number if removed_number is not None else bytes(data[rec:rec+4])
        for item in range(layout["slots"]):
            idx=0x20+index+item*4
            if removed_number is not None and bytes(data[idx:idx+4])==wanted:
                data[idx:idx+4]=bytes(4); return
            if removed_number is None and data[idx:idx+4]==bytes(4):
                data[idx:idx+4]=wanted; return
        return
    idx=0x20+index+slot*4
    data[idx:idx+4]=data[rec:rec+4] if struct.unpack_from("<I",data,rec+4)[0] else bytes(4)

def _assign_native_numbers(game: str, data: bytearray, record_pos: int) -> None:
    layout=_native_layout(game,data); body=bytes(data[0x20:-8]); records,_=_section(body,0x07,layout["slots"]*layout["size"])
    used=[]
    for slot in range(layout["slots"]):
        pos=0x20+records+slot*layout["size"]
        if pos!=record_pos and struct.unpack_from("<I",data,pos+4)[0]: used.append(struct.unpack_from("<H",data,pos)[0])
    number=max(used,default=-1)+1; struct.pack_into("<HH",data,record_pos,number,number+1)

def remove_native(game: str, path: Path, slot: int, expected_raw: str, make_backup: bool=True) -> Path | None:
    rows, decrypted, variant, start = _parse_native(game, path); layout = _native_layout(game,decrypted)
    if not 0 <= slot < layout["slots"]: raise AdapterError("Invalid save slot.")
    data = bytearray(decrypted); absolute = 0x20 + start + slot * layout["size"]
    expected = base64.b64decode(expected_raw)
    if bytes(data[absolute:absolute+layout["size"]]) != expected: raise AdapterError("The save changed; reload before transferring.")
    removed_number=bytes(data[absolute:absolute+4])
    saved = backup(path) if make_backup else None; data[absolute:absolute+layout["size"]] = bytes(layout["size"]); _sync_native_index(game,data,slot,removed_number)
    _write_verified(game,path,bytes(data),variant); return saved

def insert_native(game: str, path: Path, raw_record: str, assign_numbers: bool=False, make_backup: bool=True) -> tuple[int, Path | None]:
    rows, decrypted, variant, start = _parse_native(game, path); layout = _native_layout(game,decrypted)
    record = base64.b64decode(raw_record)
    if len(record) != layout["size"]: raise AdapterError(f"This is not a {game} record.")
    data = bytearray(decrypted)
    for slot in range(layout["slots"]):
        absolute = 0x20 + start + slot * layout["size"]
        if struct.unpack_from("<I",data,absolute+4)[0] == 0:
            saved=backup(path) if make_backup else None; data[absolute:absolute+layout["size"]]=record
            if assign_numbers: _assign_native_numbers(game,data,absolute)
            _sync_native_index(game,data,slot)
            _write_verified(game,path,bytes(data),variant); return slot,saved
    raise AdapterError(f"The {game} save has no empty Yo-kai slots.")

def species_id_for(game: str, species: str) -> int | None:
    mapping = YW1_NAMES if game == "YW1" else _species_map(game)
    matches=[ident for ident,name in mapping.items() if name==species]
    return matches[-1] if matches else None

def transfer_species_id(source_game: str, target_game: str, species: str) -> int | None:
    """Resolve safe destination aliases without trusting reused numeric IDs."""
    exact=species_id_for(target_game,species)
    if exact is not None:return exact
    if source_game in NATIVE_LAYOUTS and target_game in NATIVE_LAYOUTS:
        source_id=species_id_for(source_game,species)
        target_name=_species_map(target_game).get(source_id)
        # YW2 annotates some alternate ability records as "Species (Ability)"
        # while YW3 uses the base species name. Other shared IDs are repurposed
        # for unrelated Yo-kai, so matching the base name is mandatory.
        source_base=species.split(" (",1)[0].casefold()
        target_base=target_name.split(" (",1)[0].casefold() if target_name else ""
        if source_id is not None and source_base==target_base:return source_id
    return None

def transfer_species_name(source_game: str, target_game: str, species: str) -> str | None:
    ident=transfer_species_id(source_game,target_game,species)
    if ident is None:return None
    mapping=YW1_NAMES if target_game=="YW1" else _species_map(target_game)
    return mapping.get(ident)

def compatible_with(game: str, species: str, source_game: str | None=None) -> bool:
    return source_game==game or transfer_species_id(source_game or game,game,species) is not None

def _portable_stats(source_game: str, raw: bytes) -> tuple[bytes,bytes]:
    if source_game in ("YW1","YW2"): return raw[0x40:0x45],raw[0x4A:0x4F]
    if source_game=="BUSTERS2": return raw[0x40:0x45],bytes(5)
    if source_game=="BLASTERS": return raw[0x40:0x44]+bytes(1),bytes(5)
    return raw[0x34:0x39],raw[0x39:0x3E]

def _yw1_iv_points(raw: bytes) -> list[int]:
    """Return YW1's five legal 10-point IV allocations."""
    return [value & 0x0F for value in raw[0x45:0x4A]]

def _scale_iv_points(points: list[int], target_total: int) -> list[int]:
    """Deterministically apportion an IV distribution to a new total."""
    total=sum(points)
    if total<=0:return []
    numerators=[value*target_total for value in points]
    scaled=[value//total for value in numerators]
    remaining=target_total-sum(scaled)
    order=sorted(range(len(points)),key=lambda i:(numerators[i]%total,points[i],-i),reverse=True)
    for i in order[:remaining]:scaled[i]+=1
    return scaled

def _portable_temperament(source_game: str, raw: bytes) -> tuple[int,int | None]:
    """Return the portable attitude and seriousness/loafing values."""
    if source_game=="YW1":
        # YW1 stores the state differently from YW2/YW3. In YW1, a non-zero
        # second counter is displayed as Serious; zero is displayed as Stiff.
        # The modern packed-nibble values for those labels are 0 and 1.
        loaf=0 if raw[0x57] else 1
        return raw[0x55]&0x0F,loaf
    if source_game in ("BLASTERS","BUSTERS2"):
        return 1,None
    offset=0x54 if source_game=="YW2" else 0x4C
    packed=raw[offset]
    return packed&0x0F,packed>>4

def _portable_health(source_game: str, raw: bytes) -> bytes:
    """Return the two cached 16-bit HP fields shared by all three formats."""
    if source_game in ("BLASTERS","BUSTERS2"):
        return bytes(4)
    offset=0x58 if source_game in ("YW1","YW2") else 0x50
    return raw[offset:offset+4]

XP_OFFSETS = {"YW1":0x38,"YW2":0x34,"YW3":0x28,"BLASTERS":0x38,"BUSTERS2":None}

def _portable_xp(source_game: str, raw: bytes) -> int:
    """Read the per-level experience progress stored in a Yo-kai record."""
    offset=XP_OFFSETS[source_game]
    if offset is None:return 0
    value=struct.unpack_from("<i" if source_game=="BLASTERS" else "<I",raw,offset)[0]
    return max(0,min(value,0x7FFFFFFF))

def record_xp(game: str, raw_record: str) -> int:
    return _portable_xp(game,base64.b64decode(raw_record))

def _converted_stats(source_game: str, target_game: str, raw: bytes) -> tuple[bytes,bytes]:
    if source_game=="YW1":
        source_points=_yw1_iv_points(raw)
        points=_scale_iv_points(source_points,40) if sum(source_points)==10 else []
        iv=bytes((points[0]*2,*points[1:])) if points else bytes((16,8,8,8,8))
        sc=bytes(5)
    else:
        iv,sc=_portable_stats(source_game,raw)
    if target_game=="BLASTERS":
        points=[iv[0]//2,*iv[1:4]]
        reduced=_scale_iv_points(points,40)
        if not reduced:reduced=[10]*4
        return bytes((reduced[0]*2,*reduced[1:])),bytes(5)
    if target_game=="YW1":
        points=[iv[0]//2,*iv[1:]]
        yw1=_scale_iv_points(points,10) if iv[0]%2==0 and sum(points)==40 else [2]*5
        return bytes(yw1),bytes(5)
    # YW2 and YW3 share the 40-point IV rule: HP is stored doubled.
    valid=(iv[0]%2==0 and iv[0]//2+sum(iv[1:])==40)
    if not valid:
        iv=bytes((16,8,8,8,8))
    return iv,sc

def _validate_converted_record(game: str, record: bytes) -> None:
    expected=YW1_RECORD_SIZE if game=="YW1" else NATIVE_LAYOUTS[game]["size"]
    if len(record)!=expected: raise AdapterError(f"Generated {game} record has the wrong size.")
    species_id=struct.unpack_from("<i" if game=="YW1" else "<I",record,4)[0]
    mapping=YW1_NAMES if game=="YW1" else _species_map(game)
    if species_id not in mapping: raise AdapterError(f"Generated {game} record has an invalid species ID.")
    level=record[0x54 if game=="YW1" else NATIVE_LAYOUTS[game]["level"]]
    if not 1<=level<=99: raise AdapterError(f"Generated {game} record has an invalid level.")
    if game in ("YW2","YW3","BLASTERS","BUSTERS2"):
        iv=(record[0x40:0x44] if game=="BLASTERS" else
            record[0x40:0x45] if game in ("YW2","BUSTERS2") else record[0x34:0x39])
        if iv[0]%2 or iv[0]//2+sum(iv[1:])!=40:
            raise AdapterError(f"Generated {game} record has an invalid IV allocation.")
    elif sum(value&0x0F for value in record[0x45:0x4A])!=10:
        raise AdapterError("Generated YW1 record has an invalid IV allocation.")

def build_converted_record(source_game: str, target_game: str, species: str,
                           nickname: str, level: int, raw_record: str,
                           destination: Path) -> str:
    target_id=transfer_species_id(source_game,target_game,species)
    if target_id is None: raise AdapterError(f"{species} does not exist in {target_game}.")
    source=base64.b64decode(raw_record); iv,sc=_converted_stats(source_game,target_game,source)
    attitude,loaf=_portable_temperament(source_game,source); health=_portable_health(source_game,source)
    # Zero is a valid native/default attitude code (for example Goldenyan in
    # YW3). Only the three unused nibble values are unsafe to carry across.
    if not 0<=attitude<=12:attitude=1
    xp=_portable_xp(source_game,source)
    size=YW1_RECORD_SIZE if target_game=="YW1" else NATIVE_LAYOUTS[target_game]["size"]
    record=bytearray(size); struct.pack_into("<I",record,4,target_id&0xFFFFFFFF)
    name_limit=35 if target_game=="YW1" else (27 if target_game=="BLASTERS" else 23)
    encoded=nickname.encode("utf-8")[:name_limit]; record[8:8+len(encoded)]=encoded
    if target_game=="YW1":
        struct.pack_into("<I",record,XP_OFFSETS[target_game],xp)
        record[0x45:0x4A]=iv; record[0x4A:0x4F]=sc; record[0x54]=max(1,min(99,level)); record[0x55]=attitude; record[0x58:0x5C]=health
    elif target_game=="YW2":
        struct.pack_into("<I",record,XP_OFFSETS[target_game],xp)
        record[0x40:0x45]=iv; record[0x4A:0x4F]=sc; record[0x4F]=max(1,min(99,level))
        record[0x54]=((loaf if loaf is not None else 3)&0x0F)<<4|attitude
        record[0x58:0x5C]=health
        rows=parse_native(target_game,destination)
        if rows: record[0x3C:0x40]=base64.b64decode(rows[0]["raw_record"])[0x3C:0x40]
    elif target_game=="YW3":
        struct.pack_into("<I",record,XP_OFFSETS[target_game],xp)
        record[0x34:0x39]=iv; record[0x39:0x3E]=sc; record[0x46:0x49]=b"\x01\x01\x01"; record[0x49]=max(1,min(99,level))
        record[0x4C]=((loaf if loaf is not None else 3)&0x0F)<<4|attitude
        record[0x50:0x54]=health
        rows=parse_native(target_game,destination)
        if rows: record[0x30:0x34]=base64.b64decode(rows[0]["raw_record"])[0x30:0x34]
    elif target_game=="BUSTERS2":
        # Busters 2 has no per-level XP, attitude, or cached HP fields. It
        # does share the legal 40-point IV representation with YW2/YW3.
        record[0x40:0x45]=iv; record[0x48]=max(1,min(99,level))
        tech_path=Path(__file__).resolve().parent/"data"/"busters2_technic.json"
        import json
        moves=json.loads(tech_path.read_text(encoding="utf-8")).get(str(target_id),[])
        for offset,move in zip((0x28,0x2C,0x30),moves): struct.pack_into("<I",record,offset,move)
        rows=parse_native(target_game,destination)
        if rows: record[0x3C:0x40]=base64.b64decode(rows[0]["raw_record"])[0x3C:0x40]
    else:
        struct.pack_into("<I",record,XP_OFFSETS[target_game],xp)
        record[0x40:0x44]=iv; record[0x49]=max(1,min(99,level))
        tech_path=Path(__file__).resolve().parent/"data"/"blasters_technic.json"
        import json
        moves=json.loads(tech_path.read_text(encoding="utf-8")).get(str(target_id),[])
        for offset,move in zip((0x2C,0x30,0x34),moves): struct.pack_into("<I",record,offset,move)
        rows=parse_native(target_game,destination)
        if rows: record[0x3C:0x40]=base64.b64decode(rows[0]["raw_record"])[0x3C:0x40]
    _validate_converted_record(target_game,record)
    return base64.b64encode(record).decode("ascii")

def insert_cross_game(source_game: str, target_game: str, species: str,
                      nickname: str, level: int, raw_record: str,
                      destination: Path, make_backup: bool=True) -> tuple[int,Path | None]:
    converted=build_converted_record(source_game,target_game,species,nickname,level,raw_record,destination)
    if target_game=="YW1": return insert_yw1(destination,converted,assign_numbers=True,make_backup=make_backup)
    return insert_native(target_game,destination,converted,assign_numbers=True,make_backup=make_backup)


def backup(path: Path) -> Path:
    folder = path.parent / "YoKaiWatchBank Backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S-%f}{path.suffix}.bak"
    shutil.copy2(path, target)
    return target


def _nickname(raw: bytes) -> str:
    raw = raw.split(b"\0", 1)[0]
    if not raw:
        return ""
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin1", errors="replace")


def load_yw1(path: Path) -> tuple[bytes, bool]:
    raw = path.read_bytes()
    encrypted = is_encrypted_yw1(raw)
    return (decrypt_yw1(raw) if encrypted else raw), encrypted


def write_yw1(path: Path, data: bytes, was_encrypted: bool) -> None:
    path.write_bytes(encrypt_yw1(data) if was_encrypted else data)


def parse_yw1(path: Path) -> list[dict]:
    data, encrypted = load_yw1(path)
    if len(data) < MIN_YW1_SIZE:
        raise AdapterError("File is too small for a decrypted Yo-kai Watch 1 save.")
    result = []
    for slot in range(YW1_SLOTS):
        pos = YW1_OFFSET + slot * YW1_RECORD_SIZE
        record = data[pos:pos + YW1_RECORD_SIZE]
        species_id = struct.unpack_from("<i", record, 4)[0]
        if species_id == 0:
            continue
        # The published layout stores level at byte 0x54.
        level = record[0x54]
        if not 1 <= level <= 99:
            raise AdapterError("The selected file does not look like a decrypted YW1 save (invalid level data).")
        species = YW1_NAMES.get(species_id, f"Unknown #{species_id}")
        nick = _nickname(record[8:0x2C])
        result.append({
            "slot": slot, "species_id": species_id, "species": species,
            "name": nick or species, "nickname": nick, "level": level,
            "xp": struct.unpack_from("<I",record,0x38)[0],
            "raw_record": base64.b64encode(record).decode("ascii"), "save_encrypted": encrypted,
        })
    return result


def remove_yw1(path: Path, slot: int, expected_raw: str, make_backup: bool=True) -> Path | None:
    if not 0 <= slot < YW1_SLOTS:
        raise AdapterError("Invalid YW1 slot.")
    decoded, encrypted = load_yw1(path); data = bytearray(decoded)
    pos = YW1_OFFSET + slot * YW1_RECORD_SIZE
    expected = base64.b64decode(expected_raw)
    if bytes(data[pos:pos + YW1_RECORD_SIZE]) != expected:
        raise AdapterError("The save changed since it was loaded. Reload it before transferring.")
    saved = backup(path) if make_backup else None
    data[pos:pos + YW1_RECORD_SIZE] = bytes(YW1_RECORD_SIZE); _sync_yw1_index(data,slot)
    write_yw1(path, bytes(data), encrypted)
    return saved


def insert_yw1(path: Path, raw_record: str, assign_numbers: bool=False, make_backup: bool=True) -> tuple[int, Path | None]:
    record = base64.b64decode(raw_record)
    if len(record) != YW1_RECORD_SIZE:
        raise AdapterError("This Bank record is not a valid YW1 Yo-kai record.")
    decoded, encrypted = load_yw1(path); data = bytearray(decoded)
    if len(data) < MIN_YW1_SIZE:
        raise AdapterError("File is too small for a decrypted Yo-kai Watch 1 save.")
    for slot in range(YW1_SLOTS):
        pos = YW1_OFFSET + slot * YW1_RECORD_SIZE
        if struct.unpack_from("<i", data, pos + 4)[0] == 0:
            saved = backup(path) if make_backup else None
            data[pos:pos + YW1_RECORD_SIZE] = record
            if assign_numbers: _assign_yw1_numbers(data,pos)
            _sync_yw1_index(data,slot)
            write_yw1(path, bytes(data), encrypted)
            return slot, saved
    raise AdapterError("The YW1 save has no empty Yo-kai slots.")

def _sync_yw1_index(data: bytearray, slot: int) -> None:
    pos=YW1_OFFSET+slot*YW1_RECORD_SIZE; idx=0x73DC+slot*4
    if idx+4<=len(data): data[idx:idx+4]=data[pos:pos+4] if struct.unpack_from("<I",data,pos+4)[0] else b"\0\0\0\0"

def _assign_yw1_numbers(data: bytearray, record_pos: int) -> None:
    used=[]
    for slot in range(YW1_SLOTS):
        pos=YW1_OFFSET+slot*YW1_RECORD_SIZE
        if pos!=record_pos and struct.unpack_from("<I",data,pos+4)[0]: used.append(struct.unpack_from("<H",data,pos)[0])
    number=max(used,default=-1)+1; struct.pack_into("<HH",data,record_pos,number,number+1)


def parse_save(game: str, path: Path) -> list[dict]:
    if game == "YW1":
        return parse_yw1(path)
    return parse_native(game, path)
