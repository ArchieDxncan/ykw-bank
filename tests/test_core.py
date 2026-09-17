import json
import base64
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import App, BankDB, Yokai, sha256_file
from adapters import MIN_YW1_SIZE, YW1_OFFSET, YW1_RECORD_SIZE, insert_yw1, parse_yw1, record_xp, remove_yw1
from save_crypto import encrypt_yw1, is_encrypted_yw1


class CoreTests(unittest.TestCase):
    def test_validation(self):
        y = Yokai.from_dict({"name":"Jibanyan","species":"Jibanyan","source_game":"yw1","level":12,"rank":"D"})
        self.assertEqual((y.source_game, y.level), ("YW1", 12))
        with self.assertRaises(ValueError): Yokai.from_dict({"name":"x","source_game":"YW4"})

    def test_database_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            db=BankDB(Path(d)/"bank.sqlite3")
            y=Yokai.from_dict({"name":"Komasan","species":"Komasan","source_game":"YW2"}); db.upsert(y)
            self.assertEqual(db.get(y.id).species,"Komasan")
            self.assertEqual(len(db.list(game="YW2")),1)

    def test_bank_keeps_arrival_order_after_edits(self):
        with tempfile.TemporaryDirectory() as d:
            db=BankDB(Path(d)/"bank.sqlite3")
            first=Yokai.from_dict({"name":"Shogunyan","species":"Shogunyan","source_game":"YW1"})
            second=Yokai.from_dict({"name":"Cadin","species":"Cadin","source_game":"YW1"})
            third=Yokai.from_dict({"name":"Jibanyan","species":"Jibanyan","source_game":"YW1"})
            for yokai in (first,second,third): db.upsert(yokai)
            self.assertEqual([row["id"] for row in db.list()],[first.id,second.id,third.id])
            second.notes="edited"; db.upsert(second)
            self.assertEqual([row["id"] for row in db.list()],[first.id,second.id,third.id])

    def test_database_restore_rolls_back_transfer_session(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); db=BankDB(root/"bank.sqlite3")
            first=Yokai.from_dict({"name":"Jibanyan","species":"Jibanyan","source_game":"YW1"})
            second=Yokai.from_dict({"name":"Komasan","species":"Komasan","source_game":"YW2"})
            db.upsert(first); snapshot=root/"before.sqlite3"; db.backup(snapshot)
            db.delete([first.id]); db.upsert(second); db.restore(snapshot)
            self.assertEqual(db.count(),1)
            self.assertEqual(db.get(first.id).species,"Jibanyan")

    def test_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"x"; p.write_bytes(b"abc")
            self.assertEqual(sha256_file(p),"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_yw1_reversible_transfer(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"game1_decrypted.yw"; data=bytearray(MIN_YW1_SIZE)
            record=bytearray(YW1_RECORD_SIZE)
            import struct
            struct.pack_into("<HH",record,0,0,1); struct.pack_into("<i",record,4,-1175475131); record[8:16]=b"Jibanyan"; record[0x54]=12
            data[YW1_OFFSET:YW1_OFFSET+YW1_RECORD_SIZE]=record; path.write_bytes(data)
            parsed=parse_yw1(path); self.assertEqual(parsed[0]["species"],"Jibanyan")
            remove_yw1(path,0,parsed[0]["raw_record"]); self.assertEqual(parse_yw1(path),[])
            slot,_=insert_yw1(path,parsed[0]["raw_record"]); self.assertEqual(slot,0)
            self.assertEqual(path.read_bytes()[YW1_OFFSET:YW1_OFFSET+YW1_RECORD_SIZE],bytes(record))

    def test_staged_yw1_transfer_skips_per_operation_backup_and_keeps_xp(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"game1_decrypted.yw"; data=bytearray(MIN_YW1_SIZE)
            record=bytearray(YW1_RECORD_SIZE)
            import struct
            struct.pack_into("<HH",record,0,0,1); struct.pack_into("<i",record,4,-1175475131)
            struct.pack_into("<I",record,0x38,5774); record[0x54]=25
            data[YW1_OFFSET:YW1_OFFSET+YW1_RECORD_SIZE]=record; path.write_bytes(data)
            parsed=parse_yw1(path)[0]
            self.assertEqual(parsed["xp"],5774)
            self.assertEqual(record_xp("YW1",parsed["raw_record"]),5774)
            self.assertIsNone(remove_yw1(path,0,parsed["raw_record"],make_backup=False))
            self.assertEqual(list(Path(d).rglob("*backup*")),[])

    def test_encrypted_yw1_reversible_transfer(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"game1.yw"; data=bytearray(MIN_YW1_SIZE)
            record=bytearray(YW1_RECORD_SIZE)
            import struct
            struct.pack_into("<HH",record,0,0,1); struct.pack_into("<i",record,4,-1175475131); record[0x54]=12
            data[YW1_OFFSET:YW1_OFFSET+YW1_RECORD_SIZE]=record
            struct.pack_into("<I",data,len(data)-4,0x12345678); path.write_bytes(encrypt_yw1(bytes(data)))
            self.assertTrue(is_encrypted_yw1(path.read_bytes()))
            parsed=parse_yw1(path); remove_yw1(path,0,parsed[0]["raw_record"])
            self.assertTrue(is_encrypted_yw1(path.read_bytes())); insert_yw1(path,parsed[0]["raw_record"])
            self.assertTrue(is_encrypted_yw1(path.read_bytes())); self.assertEqual(parse_yw1(path)[0]["species"],"Jibanyan")

    def test_complete_yw1_species_table(self):
        from adapters import YW1_NAMES
        self.assertGreaterEqual(len(YW1_NAMES), 350)
        self.assertEqual(YW1_NAMES[-1655118996], "Jibanyan")
        self.assertEqual(YW1_NAMES[-1364399143], "Dulluma")

    def test_native_species_databases(self):
        from adapters import _species_map
        self.assertGreater(len(_species_map("YW2")), 400)
        self.assertGreater(len(_species_map("YW3")), 600)
        self.assertIn("Jibanyan", _species_map("YW2").values())
        self.assertIn("Jibanyan", _species_map("YW3").values())
        self.assertGreater(len(_species_map("BLASTERS")), 400)
        self.assertGreater(len(_species_map("BUSTERS2")), 700)
        self.assertIn("Jibanyan", _species_map("BLASTERS").values())
        self.assertIn("Jibanyan", _species_map("BUSTERS2").values())
        self.assertEqual(_species_map("BLASTERS")[668804560],"Lord Enma")

    def test_moon_rabbit_lord_enma_alias_keeps_canonical_write_id(self):
        from adapters import species_id_for
        self.assertEqual(species_id_for("BLASTERS","Lord Enma"),823602974)

    def test_cross_game_compatibility_gate(self):
        from adapters import compatible_with, transfer_species_id, transfer_species_name
        for game in ("YW1","YW2","YW3"):
            self.assertTrue(compatible_with(game,"Jibanyan"))
        self.assertFalse(compatible_with("YW1","Usapyon"))
        self.assertFalse(compatible_with("YW2","Usapyon"))
        self.assertTrue(compatible_with("YW3","Usapyon"))
        self.assertTrue(compatible_with("YW3","Hungramps (Starver)","YW2"))
        self.assertEqual(transfer_species_id("YW2","YW3","Hungramps (Starver)"),3554148426)
        self.assertEqual(transfer_species_name("YW2","YW3","Hungramps (Starver)"),"Hungramps")
        self.assertTrue(compatible_with("YW2","Hungramps","YW3"))
        self.assertEqual(transfer_species_name("YW3","YW2","Hungramps"),"Hungramps (Starver)")
        # YW2's Sumodon ID is reused for Ebisu in YW3, but YW3 has Sumodon at
        # another ID. Exact-name resolution must win over the reused ID.
        self.assertEqual(transfer_species_name("YW2","YW3","Sumodon"),"Sumodon")
        self.assertNotEqual(transfer_species_id("YW2","YW3","Sumodon"),952346640)

    def test_yw1_to_yw3_generates_legal_iv_allocation(self):
        from adapters import _converted_stats
        source=bytes(YW1_RECORD_SIZE)
        iv,correction=_converted_stats("YW1","YW3",source)
        self.assertEqual(iv[0]//2+sum(iv[1:]),40)
        self.assertEqual(iv[0]%2,0)
        self.assertEqual(correction,bytes(5))

    def test_yw1_iv_distribution_scales_exactly_upward(self):
        from adapters import _converted_stats
        source=bytearray(YW1_RECORD_SIZE)
        source[0x45:0x4A]=bytes((1,2,3,1,3))
        iv,correction=_converted_stats("YW1","YW3",source)
        self.assertEqual(iv,bytes((8,8,12,4,12)))
        self.assertEqual(iv[0]//2+sum(iv[1:]),40)
        self.assertEqual(correction,bytes(5))

    def test_iv_distribution_scales_legally_downward(self):
        from adapters import _converted_stats
        source=bytearray(0x54)
        source[0x34:0x39]=bytes((10,7,9,8,11))
        source[0x39:0x3E]=bytes((1,2,3,4,5))
        iv,correction=_converted_stats("YW3","YW1",source)
        self.assertEqual(sum(iv),10)
        self.assertEqual(correction,bytes(5))

    def test_attitude_and_seriousness_offsets(self):
        from adapters import _portable_temperament
        yw1=bytearray(YW1_RECORD_SIZE); yw1[0x55]=7; yw1[0x57]=4
        yw2=bytearray(0x5C); yw2[0x54]=0x47
        yw3=bytearray(0x54); yw3[0x4C]=0x47
        self.assertEqual(_portable_temperament("YW1",yw1),(7,0))
        self.assertEqual(_portable_temperament("YW2",yw2),(7,4))
        self.assertEqual(_portable_temperament("YW3",yw3),(7,4))

    def test_yw1_serious_and_stiff_map_to_modern_labels(self):
        from adapters import _portable_temperament
        serious=bytearray(YW1_RECORD_SIZE); serious[0x55]=7; serious[0x57]=4
        stiff=bytearray(YW1_RECORD_SIZE); stiff[0x55]=7; stiff[0x57]=0
        self.assertEqual(_portable_temperament("YW1",serious),(7,0))
        self.assertEqual(_portable_temperament("YW1",stiff),(7,1))

    def test_native_zero_attitude_is_preserved(self):
        from adapters import _portable_temperament
        yw3=bytearray(0x54); yw3[0x4C]=0x00
        self.assertEqual(_portable_temperament("YW3",yw3),(0,0))

    def test_cross_game_health_fields(self):
        from adapters import _portable_health
        yw1=bytearray(YW1_RECORD_SIZE); yw1[0x58:0x5C]=bytes.fromhex("4a01ba01")
        yw3=bytearray(0x54); yw3[0x50:0x54]=bytes.fromhex("4a01ba01")
        self.assertEqual(_portable_health("YW1",yw1),bytes.fromhex("4a01ba01"))
        self.assertEqual(_portable_health("YW3",yw3),bytes.fromhex("4a01ba01"))

    def test_multi_selection_follows_visible_order(self):
        class FakeTree:
            def selection(self): return ("third","first")
            def get_children(self): return ("first","second","third")
        self.assertEqual(App._selected_in_view_order(None,FakeTree()),["first","third"])

    def test_converted_record_validator_rejects_zero_yw3_ivs(self):
        from adapters import _validate_converted_record, species_id_for, AdapterError, NATIVE_LAYOUTS
        record=bytearray(NATIVE_LAYOUTS["YW3"]["size"])
        import struct
        struct.pack_into("<I",record,4,species_id_for("YW3","Goldenyan"))
        record[NATIVE_LAYOUTS["YW3"]["level"]]=55
        with self.assertRaises(AdapterError):
            _validate_converted_record("YW3",bytes(record))

    def test_cross_game_xp_offsets(self):
        from adapters import _portable_xp, XP_OFFSETS
        import struct
        for game,size in (("YW1",0x5C),("YW2",0x5C),("YW3",0x54)):
            record=bytearray(size); struct.pack_into("<I",record,XP_OFFSETS[game],5774)
            self.assertEqual(_portable_xp(game,bytes(record)),5774)

    def test_action_game_xp_contract(self):
        from adapters import _portable_xp
        import struct
        blasters=bytearray(0x4C);struct.pack_into("<I",blasters,0x38,1234)
        self.assertEqual(_portable_xp("BLASTERS",bytes(blasters)),1234)
        self.assertEqual(_portable_xp("BUSTERS2",bytes(0x4C)),0)

    def test_blasters_four_stat_iv_translation(self):
        from adapters import _converted_stats
        source=bytearray(0x4C);source[0x40:0x44]=bytes((20,10,10,10))
        upward,_=_converted_stats("BLASTERS","YW3",source)
        self.assertEqual(upward,bytes((20,10,10,10,0)))
        back,_=_converted_stats("YW3","BLASTERS",bytes(0x34)+upward+bytes(0x1B))
        self.assertEqual(back[0]//2+sum(back[1:]),40)


if __name__ == "__main__": unittest.main()
