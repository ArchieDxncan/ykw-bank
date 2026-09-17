"""Automatic encryption routing for original game*.yw exports."""
from __future__ import annotations
import sys
import binascii
import struct
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor" / "yw_save"
if str(VENDOR) not in sys.path: sys.path.insert(0, str(VENDOR))

def _module():
    try:
        import yw_save
        if not getattr(yw_save, "haveCrypto", False):
            raise RuntimeError("YW2/YW3 support requires pycryptodome. Run: py -m pip install pycryptodome")
        return yw_save
    except ImportError as exc:
        raise RuntimeError("YW2/YW3 support requires pycryptodome. Run: py -m pip install pycryptodome") from exc

def find_head(save_path: Path) -> Path:
    head = save_path.parent / "head.yw"
    if not head.is_file(): raise RuntimeError(f"The matching head.yw was not found beside {save_path.name}.")
    return head

def _yw_layer(data: bytes, encrypt: bool = False) -> bytes:
    """Process Level-5's symmetric Yo-kai cipher and its ciphertext CRC."""
    from save_crypto import YWCipher
    if len(data) < 8:
        raise RuntimeError("Save cipher payload is too small.")
    crc, seed = struct.unpack_from("<II", data, len(data) - 8)
    if not encrypt and (binascii.crc32(data[:-8]) & 0xFFFFFFFF) != crc:
        raise RuntimeError("Save cipher checksum does not match.")
    result = bytearray(YWCipher(seed).apply(data[:-8]) + data[-8:])
    if encrypt:
        struct.pack_into("<I", result, len(result) - 8,
                         binascii.crc32(result[:-8]) & 0xFFFFFFFF)
    return bytes(result)

def _aes_ccm_decrypt(raw: bytes, key: bytes) -> bytes | None:
    nonce, tag, ciphertext = raw[:12], raw[16:32], raw[32:]
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CCM, nonce=nonce, mac_len=16)
        return cipher.decrypt_and_verify(ciphertext, tag)
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESCCM
            return AESCCM(key, tag_length=16).decrypt(nonce, ciphertext + tag, None)
        except ImportError as exc:
            raise RuntimeError("Save support requires pycryptodome. Run: py -m pip install pycryptodome") from exc
    except ValueError:
        return None

def _aes_ccm_encrypt(plain: bytes, key: bytes, nonce: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CCM, nonce=nonce, mac_len=16)
        ciphertext = cipher.encrypt(plain)
        tag = cipher.digest()
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESCCM
            joined = AESCCM(key, tag_length=16).encrypt(nonce, plain, None)
            ciphertext, tag = joined[:-16], joined[-16:]
        except ImportError as exc:
            raise RuntimeError("Save support requires pycryptodome. Run: py -m pip install pycryptodome") from exc
    return nonce + bytes(4) + tag + ciphertext

def _busters2_keys(save_path: Path) -> list[bytes]:
    """Derive both possible Busters 2 slot keys from the matching head.yw."""
    from save_crypto import Xorshift
    head = _yw_layer(find_head(save_path).read_bytes())
    def u32(offset: int) -> int:
        return struct.unpack_from("<I", head, offset)[0]
    keys = []
    for index in (1, 2):
        base = (index - 1) * 0xA8 + 0x36F8
        rng = Xorshift(u32(0x0C) ^ u32(base + 0x38))
        count = sum(u32(base + 0x40 + i * 4) for i in range(6)) & 0xFF
        for _ in range(count):
            rng.next(0)
        keys.append(bytes(rng.next(0x100) for _ in range(16)))
    return keys

def _matching_head(save_path: Path) -> Path:
    preferred = save_path.parent / ("head.yw_g" if save_path.name.endswith(".yw_g") else "head.yw")
    if preferred.is_file():
        return preferred
    for name in ("head.yw_g", "head.yw"):
        candidate = save_path.parent / name
        if candidate.is_file(): return candidate
    raise RuntimeError(f"The matching head.yw/head.yw_g was not found beside {save_path.name}.")

def _blasters_keys(save_path: Path) -> list[bytes]:
    """Derive Blasters' three possible profile keys for JP and localized saves."""
    from save_crypto import Xorshift
    head_raw = _matching_head(save_path).read_bytes()
    head = _yw_layer(head_raw)
    non_jp = len(head_raw) >= 15180
    user_length, ignored_length = (0x80, 0x1C) if non_jp else (0x78, 0x18)
    def u32(offset: int) -> int:
        return struct.unpack_from("<I", head, offset)[0]
    def sub(field: int, index: int) -> int:
        return u32((index - 1) * user_length + 0x39C8 + ignored_length + field * 4)
    keys=[]
    for index in (1,2,3):
        seed=u32(0x0C)^sub(0x0C,index)
        if sub(0,index)&0x4000: seed=(~seed)&0xFFFFFFFF
        rng=Xorshift(seed)
        for _ in range(sub(0x0A,index)&0xFF): rng.next(0)
        keys.append(bytes(rng.next(0x100) for _ in range(16)))
    return keys

def _decrypt_blasters(save_path: Path) -> tuple[bytes,str]:
    raw=save_path.read_bytes()
    if len(raw)<40: raise RuntimeError("Blasters save is too small.")
    for slot,key in enumerate(_blasters_keys(save_path),1):
        first=_aes_ccm_decrypt(raw,key)
        if first is None: continue
        plain=_yw_layer(first)
        return raw[:0x20]+plain,f"blasters:{slot}"
    raise RuntimeError("Blasters authentication failed; the game and head files may not match.")

def _decrypt_busters2(save_path: Path) -> tuple[bytes, str]:
    raw = save_path.read_bytes()
    if len(raw) < 40:
        raise RuntimeError("Busters 2 save is too small.")
    for slot, key in enumerate(_busters2_keys(save_path), 1):
        first = _aes_ccm_decrypt(raw, key)
        if first is None:
            continue
        plain = _yw_layer(first)
        # Preserve the decrypted format used by the existing native adapters:
        # nonce, padding, old tag, decrypted section stream, CRC and YW key.
        return raw[:0x20] + plain, f"busters2:{slot}"
    raise RuntimeError("Busters 2 authentication failed; game*.yw and head.yw may not match.")

def decrypt_original(game: str, save_path: Path) -> tuple[bytes, str]:
    if game == "BLASTERS":
        return _decrypt_blasters(save_path)
    if game == "BUSTERS2":
        return _decrypt_busters2(save_path)
    mod = _module(); raw = save_path.read_bytes()
    if game == "YW2":
        try: out = mod.yw2_proc(raw, False)
        except Exception: out = None
        if out: return out, "yw2"
        out = mod.yw2x_proc(raw, False, head=str(find_head(save_path)))
        if out: return out, "yw2x"
        raise RuntimeError("YW2 authentication failed; game*.yw and head.yw may not match.")
    if game == "YW3":
        out = mod.yw3_proc(raw, False, head=str(find_head(save_path)))
        if out: return out, "yw3"
        raise RuntimeError("YW3 authentication failed; game*.yw and head.yw may not match.")
    raise RuntimeError(f"Unsupported crypto game: {game}")

def encrypt_original(game: str, decrypted: bytes, save_path: Path, variant: str) -> bytes:
    if game == "BLASTERS" and variant.startswith("blasters:"):
        slot=int(variant.split(":",1)[1])
        reordered=_reorder_blasters(decrypted)
        inner=_yw_layer(reordered[0x20:],encrypt=True)
        return _aes_ccm_encrypt(inner,_blasters_keys(save_path)[slot-1],reordered[:12])
    if game == "BUSTERS2" and variant.startswith("busters2:"):
        slot = int(variant.split(":", 1)[1])
        reordered = _reorder_busters2(decrypted)
        inner = _yw_layer(reordered[0x20:], encrypt=True)
        return _aes_ccm_encrypt(inner, _busters2_keys(save_path)[slot - 1], reordered[:12])
    mod = _module()
    if game == "YW2" and variant == "yw2": return mod.yw2_proc(decrypted, True)
    if game == "YW2" and variant == "yw2x": return mod.yw2x_proc(decrypted, True, head=str(find_head(save_path)))
    if game == "YW3" and variant == "yw3":
        decrypted = _reorder_yw3(decrypted)
        return mod.yw3_proc(decrypted, True, head=str(find_head(save_path)), validator=None)
    raise RuntimeError(f"Unsupported encryption variant: {variant}")

def _reorder_yw3(decrypted: bytes) -> bytes:
    """Apply the section shuffle used by the original YW3 editor."""
    from save_crypto import Xorshift
    prefix, body, suffix = decrypted[:0x20], decrypted[0x20:-8], decrypted[-8:]

    class Node:
        def __init__(self, ident, header, payload, footer, children=None):
            self.ident, self.header, self.payload, self.footer = ident, header, payload, footer
            self.children = children
        def bytes(self):
            content = b"".join(c.bytes() for c in self.children) if self.children is not None else self.payload
            return self.header + content + self.footer

    def node_at(data, off):
        h1,h2=struct.unpack_from("<II",data,off)
        if h1 & 0xFFFF != 0xFFFE: raise ValueError("Invalid section header")
        ident,size=h2&0xFF,h2>>8; start=off+8; end=start+size
        if end+4>len(data) or struct.unpack_from("<I",data,end)[0]&0xFFFF!=0xFEFF: raise ValueError("Invalid section footer")
        payload=data[start:end]; children=None
        if len(payload)>=12 and struct.unpack_from("<I",payload,0)[0]&0xFFFF==0xFFFE:
            trial=[]; pos=0
            try:
                while pos<len(payload):
                    child,total=node_at(payload,pos); trial.append(child); pos+=total
                if pos==len(payload): children=trial
            except (ValueError,struct.error): pass
        return Node(ident,data[off:off+8],payload,data[end:end+4],children),8+size+4

    root,total=node_at(body,0)
    if total!=len(body): raise ValueError("Trailing section data")
    target=None
    def walk(n):
        nonlocal target
        if n.ident==0xF3: target=n
        for c in n.children or []: walk(c)
    walk(root)
    if not target or target.children is None: raise ValueError("YW3 section container not found")
    by_id={c.ident:c for c in target.children}
    if 0x01 not in by_id or 0x07 not in by_id: raise ValueError("YW3 shuffle sections missing")
    order=[0x01,0x03,0x0B,0x0F,0x11,0x02,0x17,0x18,0x23,0x07,0x08,0x1D,0x0C,0x0D,0x0E,0x12,0x14,0x15,0x20,0x21,0x22,0x29]
    r1=Xorshift(binascii.crc32(by_id[0x01].bytes())&0xFFFFFFFF)
    r7=Xorshift(binascii.crc32(by_id[0x07].bytes())&0xFFFFFFFF)
    for i in range(7,0,-1):
        pos=r1.next(i+1); order[pos+1],order[i+1]=order[i+1],order[pos+1]
    for i in range(0x0B,0,-1):
        pos=r7.next(i+1); order[pos+0x0A],order[i+0x0A]=order[i+0x0A],order[pos+0x0A]
    # Save-format version 4+ adds section 0x2A after the shuffled sections.
    # Leaving it at the front produces a file whose crypto authenticates but
    # whose section sequence is rejected by the game as corrupt.
    if 0x2A in by_id: order.append(0x2A)
    selected=[by_id[i] for i in order if i in by_id]
    target.children=[c for c in target.children if c.ident not in order]+selected
    return prefix+root.bytes()+suffix

def is_yw3_section_order_valid(decrypted: bytes) -> bool:
    """Return whether a decrypted YW3 save already has its game-derived order."""
    try: return _reorder_yw3(decrypted)==decrypted
    except (ValueError,struct.error): return False

def _reorder_busters2(decrypted: bytes) -> bytes:
    """Apply Busters 2's CRC-derived ordering beneath section F3."""
    from save_crypto import Xorshift
    prefix, body, suffix = decrypted[:0x20], decrypted[0x20:-8], decrypted[-8:]

    class Node:
        def __init__(self, ident, header, payload, footer, children=None):
            self.ident, self.header, self.payload, self.footer = ident, header, payload, footer
            self.children = children
        def bytes(self):
            content = b"".join(c.bytes() for c in self.children) if self.children is not None else self.payload
            return self.header + content + self.footer

    def node_at(data, off):
        h1, h2 = struct.unpack_from("<II", data, off)
        if h1 & 0xFFFF != 0xFFFE:
            raise ValueError("Invalid section header")
        ident, size = h2 & 0xFF, h2 >> 8
        start, end = off + 8, off + 8 + size
        if end + 4 > len(data) or struct.unpack_from("<I", data, end)[0] & 0xFFFF != 0xFEFF:
            raise ValueError("Invalid section footer")
        payload, children = data[start:end], None
        if len(payload) >= 12 and struct.unpack_from("<I", payload, 0)[0] & 0xFFFF == 0xFFFE:
            trial, pos = [], 0
            try:
                while pos < len(payload):
                    child, total = node_at(payload, pos); trial.append(child); pos += total
                if pos == len(payload): children = trial
            except (ValueError, struct.error):
                pass
        return Node(ident, data[off:off + 8], payload, data[end:end + 4], children), 8 + size + 4

    root, total = node_at(body, 0)
    if total != len(body):
        raise ValueError("Trailing section data")
    target = None
    def walk(node):
        nonlocal target
        if node.ident == 0xF3: target = node
        for child in node.children or []: walk(child)
    walk(root)
    if not target or target.children is None:
        raise ValueError("Busters 2 section container not found")
    by_id = {child.ident: child for child in target.children}
    if 0x01 not in by_id or 0x07 not in by_id:
        raise ValueError("Busters 2 shuffle sections missing")
    order = [0x01,0x03,0x0B,0x0F,0x02,0x17,0x18,0x23,0x07,0x08,
             0x1D,0x0C,0x0E,0x12,0x14,0x20,0x21,0x22,0x29]
    rng1 = Xorshift(binascii.crc32(by_id[0x01].bytes()) & 0xFFFFFFFF)
    rng7 = Xorshift(binascii.crc32(by_id[0x07].bytes()) & 0xFFFFFFFF)
    for i in range(6, 0, -1):
        pos = rng1.next(i + 1); order[pos + 1], order[i + 1] = order[i + 1], order[pos + 1]
    for i in range(9, 0, -1):
        pos = rng7.next(i + 1); order[pos + 9], order[i + 9] = order[i + 9], order[pos + 9]
    order.append(0x2A)
    selected = [by_id[ident] for ident in order if ident in by_id]
    target.children = [child for child in target.children if child.ident not in order] + selected
    return prefix + root.bytes() + suffix

def is_busters2_section_order_valid(decrypted: bytes) -> bool:
    try:
        return _reorder_busters2(decrypted) == decrypted
    except (ValueError, struct.error):
        return False

def _reorder_blasters(decrypted: bytes) -> bytes:
    """Apply Blasters' three-stage CRC-derived section ordering."""
    from save_crypto import Xorshift
    prefix,body,suffix=decrypted[:0x20],decrypted[0x20:-8],decrypted[-8:]
    class Node:
        def __init__(self,ident,header,payload,footer,children=None):
            self.ident,self.header,self.payload,self.footer=ident,header,payload,footer;self.children=children
        def bytes(self):
            content=b"".join(c.bytes() for c in self.children) if self.children is not None else self.payload
            return self.header+content+self.footer
    def node_at(data,off):
        h1,h2=struct.unpack_from("<II",data,off)
        if h1&0xFFFF!=0xFFFE:raise ValueError("Invalid section header")
        ident,size=h2&0xFF,h2>>8;start,end=off+8,off+8+(h2>>8)
        if end+4>len(data) or struct.unpack_from("<I",data,end)[0]&0xFFFF!=0xFEFF:raise ValueError("Invalid section footer")
        payload,children=data[start:end],None
        if len(payload)>=12 and struct.unpack_from("<I",payload,0)[0]&0xFFFF==0xFFFE:
            trial,pos=[],0
            try:
                while pos<len(payload):child,total=node_at(payload,pos);trial.append(child);pos+=total
                if pos==len(payload):children=trial
            except (ValueError,struct.error):pass
        return Node(ident,data[off:off+8],payload,data[end:end+4],children),8+size+4
    root,total=node_at(body,0)
    if total!=len(body):raise ValueError("Trailing section data")
    target=None
    def walk(node):
        nonlocal target
        if node.ident==0xF3:target=node
        for child in node.children or []:walk(child)
    walk(root)
    if not target or target.children is None:raise ValueError("Blasters section container not found")
    by_id={child.ident:child for child in target.children}
    for required in (0x01,0x03,0x07):
        if required not in by_id:raise ValueError("Blasters shuffle sections missing")
    order=[0x0B,0x0E,0x02,0x08,0x0D,0x12,0x0F,0x0C]
    for seed_id in (0x01,0x03,0x07):
        rng=Xorshift(binascii.crc32(by_id[seed_id].bytes())&0xFFFFFFFF)
        for i in range(7,0,-1):
            pos=rng.next(i+1);order[pos],order[i]=order[i],order[pos]
    selected=[by_id[ident] for ident in order if ident in by_id]
    target.children=[child for child in target.children if child.ident not in order]+selected
    return prefix+root.bytes()+suffix

def is_blasters_section_order_valid(decrypted: bytes) -> bool:
    try:return _reorder_blasters(decrypted)==decrypted
    except (ValueError,struct.error):return False
