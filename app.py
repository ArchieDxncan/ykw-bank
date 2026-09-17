from __future__ import annotations

import hashlib
import binascii
import json
import os
import shutil
import sqlite3
import struct
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from adapters import AdapterError, backup, compatible_with, insert_cross_game, insert_native, insert_yw1, parse_save, record_xp, remove_native, remove_yw1

APP_NAME = "Yo-kai Watch Bank"
VERSION = "0.5.1"
GAMES = ("YW1", "YW2", "YW3", "BLASTERS", "BUSTERS2")
RANKS = ("E", "D", "C", "B", "A", "S")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def data_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or Path(sys.argv[0]).resolve().parent)
    result = root / "YoKaiWatchBank"
    result.mkdir(parents=True, exist_ok=True)
    return result


@dataclass
class Yokai:
    name: str
    species: str
    source_game: str
    level: int = 1
    rank: str = "E"
    tribe: str = "Unknown"
    nickname: str = ""
    notes: str = ""
    source_slot: str = ""
    metadata: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    box: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, raw: dict) -> "Yokai":
        game = str(raw.get("source_game", "YW1")).upper()
        if game not in GAMES:
            raise ValueError(f"Unsupported source_game: {game}")
        level = int(raw.get("level", 1))
        if not 1 <= level <= 99:
            raise ValueError("level must be between 1 and 99")
        rank = str(raw.get("rank", "E")).upper()
        if rank not in RANKS:
            raise ValueError(f"Unsupported rank: {rank}")
        name = str(raw.get("name") or raw.get("species") or "").strip()
        species = str(raw.get("species") or name).strip()
        if not name or not species:
            raise ValueError("name and species are required")
        return cls(
            id=str(raw.get("id") or uuid.uuid4()), name=name, species=species,
            source_game=game, level=level, rank=rank,
            tribe=str(raw.get("tribe", "Unknown")), nickname=str(raw.get("nickname", "")),
            notes=str(raw.get("notes", "")), source_slot=str(raw.get("source_slot", "")),
            metadata=dict(raw.get("metadata") or {}), box=max(1, min(32, int(raw.get("box", 1)))),
            created_at=str(raw.get("created_at") or utc_now()), updated_at=utc_now(),
        )


class BankDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS yokai (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, species TEXT NOT NULL,
          source_game TEXT NOT NULL, level INTEGER NOT NULL, rank TEXT NOT NULL,
          tribe TEXT NOT NULL, nickname TEXT NOT NULL, notes TEXT NOT NULL,
          source_slot TEXT NOT NULL, metadata TEXT NOT NULL, box INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
          id TEXT PRIMARY KEY, game TEXT NOT NULL, region TEXT NOT NULL,
          original_name TEXT NOT NULL, stored_path TEXT NOT NULL,
          sha256 TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        """)
        self.conn.commit()

    def upsert(self, yokai: Yokai) -> None:
        d = asdict(yokai); d["metadata"] = json.dumps(d["metadata"], ensure_ascii=False)
        cols = list(d)
        updates = ",".join(f"{column}=excluded.{column}" for column in cols if column != "id")
        self.conn.execute(
            f"INSERT INTO yokai ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [d[c] for c in cols],
        )
        self.conn.commit()

    def get(self, ident: str) -> Yokai:
        row = self.conn.execute("SELECT * FROM yokai WHERE id=?", (ident,)).fetchone()
        if not row: raise KeyError(ident)
        d = dict(row); d["metadata"] = json.loads(d["metadata"])
        return Yokai(**d)

    def list(self, query="", game="All", box=0) -> list[sqlite3.Row]:
        where, args = [], []
        if query:
            where.append("(name LIKE ? OR species LIKE ? OR nickname LIKE ? OR tribe LIKE ?)")
            args.extend([f"%{query}%"] * 4)
        if game != "All": where.append("source_game=?"); args.append(game)
        if box: where.append("box=?"); args.append(box)
        sql = "SELECT * FROM yokai" + (" WHERE " + " AND ".join(where) if where else "")
        # rowid is the Bank arrival order. Keep it stable so a batch deposited
        # in save-slot order is shown and later withdrawn in that same order.
        return self.conn.execute(sql + " ORDER BY rowid", args).fetchall()

    def delete(self, ids: list[str]) -> None:
        self.conn.executemany("DELETE FROM yokai WHERE id=?", [(x,) for x in ids]); self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM yokai").fetchone()[0]

    def add_snapshot(self, game, region, original, stored, digest, size):
        self.conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), game, region, original, str(stored), digest, size, utc_now()))
        self.conn.commit()

    def snapshots(self):
        return self.conn.execute("SELECT * FROM snapshots ORDER BY created_at DESC").fetchall()

    def backup(self, target: Path):
        with sqlite3.connect(target) as out: self.conn.backup(out)

    def restore(self, source: Path):
        with sqlite3.connect(source) as incoming: incoming.backup(self.conn)
        self.conn.commit()

    def settings(self):
        path = self.path.parent / "config.json"
        defaults = {game: "" for game in GAMES}
        if not path.exists(): return defaults
        try: defaults.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError): pass
        return defaults

    def save_settings(self, values):
        (self.path.parent / "config.json").write_text(json.dumps(values, indent=2), encoding="utf-8")


class ConfigureDialog(tk.Toplevel):
    def __init__(self, parent, values):
        super().__init__(parent); self.title("Configure game saves"); self.resizable(False, False)
        self.result=None; self.vars={}; self.transient(parent); self.grab_set()
        body=ttk.Frame(self,padding=18); body.pack(fill="both",expand=True)
        ttk.Label(body,text="One save per game",font=("Segoe UI",14,"bold")).grid(row=0,column=0,columnspan=3,sticky="w")
        ttk.Label(body,text="Select original game*.yw files exported from saves you own.",foreground="#667085").grid(row=1,column=0,columnspan=3,sticky="w",pady=(2,14))
        for row, game in enumerate(GAMES, start=2):
            ttk.Label(body,text=game,width=6).grid(row=row,column=0,sticky="w",pady=5)
            var=tk.StringVar(value=values.get(game,"")); self.vars[game]=var
            ttk.Entry(body,textvariable=var,width=58).grid(row=row,column=1,padx=8)
            ttk.Button(body,text="Browse…",command=lambda g=game:self.pick(g)).grid(row=row,column=2)
        note_row=2+len(GAMES)
        ttk.Label(body,text="Encrypted saves are handled automatically. Keep the matching head.yw/head.yw_g\nbeside YW2, YW3, Blasters, and Busters 2 saves.",foreground="#9a3412").grid(row=note_row,column=0,columnspan=3,sticky="w",pady=(14,8))
        buttons=ttk.Frame(body); buttons.grid(row=note_row+1,column=0,columnspan=3,sticky="e")
        ttk.Button(buttons,text="Cancel",command=self.destroy).pack(side="right",padx=(8,0))
        ttk.Button(buttons,text="Save configuration",command=self.save).pack(side="right")
    def pick(self, game):
        path=filedialog.askopenfilename(parent=self,title=f"Select {game} save",filetypes=[("Yo-kai saves","game*.yw *.yw *.bin"),("All files","*.*")])
        if path:self.vars[game].set(path)
    def save(self):
        values={g:self.vars[g].get().strip() for g in GAMES}
        missing=[g for g,p in values.items() if p and not Path(p).is_file()]
        if missing: messagebox.showerror("File not found",f"Check the configured path for: {', '.join(missing)}",parent=self); return
        self.result=values; self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(f"{APP_NAME}  {VERSION}"); self.geometry("1280x720"); self.minsize(980,600)
        self.root_dir = data_dir(); self.db = BankDB(self.root_dir / "bank.sqlite3")
        self.query=tk.StringVar(); self.game=tk.StringVar(value="All"); self.box=tk.StringVar(value="All boxes"); self.active_game=tk.StringVar(value="YW1")
        self.save_rows={}; self.loaded_game=None; self.original_path=None; self.working_path=None
        self.dirty=False; self.bank_snapshot=self.root_dir/"working"/"bank-before-session.sqlite3"
        self.pending_marker=self.root_dir/"working"/"pending-session.json"
        self.status=tk.StringVar(value="Ready"); self.pending_status=tk.StringVar(value="No pending changes")
        self._recover_interrupted_session(); self._style(); self._build(); self.protocol("WM_DELETE_WINDOW",self.on_close); self.refresh(); self.load_game()

    def _style(self):
        style=ttk.Style(self); 
        try: style.theme_use("vista")
        except tk.TclError: style.theme_use("clam")
        style.configure("Title.TLabel",font=("Segoe UI",20,"bold"),foreground="#173b70")
        style.configure("Panel.TLabel",font=("Segoe UI Semibold",12),foreground="#173b70")
        style.configure("Accent.TButton",font=("Segoe UI Semibold",10))
        style.configure("Treeview",rowheight=38,font=("Segoe UI",10)); style.configure("Treeview.Heading",font=("Segoe UI Semibold",10))

    def _build(self):
        header=ttk.Frame(self,padding=(20,14)); header.pack(fill="x")
        ttk.Label(header,text="Yo-kai Watch Bank",style="Title.TLabel").pack(side="left")
        ttk.Label(header,text="Transfer Center  •  Main games + Blasters/Busters 2",foreground="#667085").pack(side="left",padx=18)
        actions=ttk.Frame(header); actions.pack(side="right")
        ttk.Button(actions,text="Configure saves…",command=self.configure).pack(side="left",padx=4)
        self.discard_button=ttk.Button(actions,text="Discard changes",command=self.discard_changes,state="disabled"); self.discard_button.pack(side="left",padx=4)
        self.save_button=ttk.Button(actions,text="Save game",style="Accent.TButton",command=self.save_game,state="disabled"); self.save_button.pack(side="left",padx=4)

        filters=ttk.Frame(self,padding=(20,0,20,12)); filters.pack(fill="x")
        search=ttk.Entry(filters,textvariable=self.query); search.pack(side="left",fill="x",expand=True); search.insert(0,"")
        search.bind("<KeyRelease>",lambda _e:self.refresh())
        for var, vals in ((self.game,("All",)+GAMES),(self.box,("All boxes",)+tuple(f"Box {i}" for i in range(1,33)))):
            cb=ttk.Combobox(filters,textvariable=var,values=vals,state="readonly",width=12); cb.pack(side="left",padx=(8,0)); cb.bind("<<ComboboxSelected>>",lambda _e:self.refresh())

        content=ttk.Frame(self,padding=(20,0,20,12)); content.pack(fill="both",expand=True)
        content.columnconfigure(0,weight=1,uniform="lists"); content.columnconfigure(1,weight=0); content.columnconfigure(2,weight=1,uniform="lists"); content.rowconfigure(0,weight=1)
        game_side=ttk.Frame(content); transfer=ttk.Frame(content,padding=(14,64)); left=ttk.Frame(content)
        game_side.grid(row=0,column=0,sticky="nsew"); transfer.grid(row=0,column=1,sticky="ns"); left.grid(row=0,column=2,sticky="nsew")
        game_head=ttk.Frame(game_side); game_head.pack(fill="x",pady=(0,8))
        ttk.Label(game_head,text="Game Save",style="Panel.TLabel").pack(side="left")
        game_cb=ttk.Combobox(game_head,textvariable=self.active_game,values=GAMES,state="readonly",width=11); game_cb.pack(side="right"); game_cb.bind("<<ComboboxSelected>>",lambda _e:self.load_game())
        self.game_tree=ttk.Treeview(game_side,columns=("name","level","xp"),show="headings",selectmode="extended")
        for c,h,w in (("name","Yo-kai",260),("level","Level",70),("xp","Experience",110)):
            self.game_tree.heading(c,text=h); self.game_tree.column(c,width=w,anchor="w" if c=="name" else "center")
        game_sb=ttk.Scrollbar(game_side,orient="vertical",command=self.game_tree.yview); self.game_tree.configure(yscrollcommand=game_sb.set)
        self.game_tree.pack(side="left",fill="both",expand=True); game_sb.pack(side="right",fill="y")
        self.game_tree.bind("<Control-a>",lambda e:self._select_all(self.game_tree))
        ttk.Label(left,text="Local Bank",style="Panel.TLabel").pack(anchor="w",pady=(0,8))
        cols=("name","level","xp","game")
        self.tree=ttk.Treeview(left,columns=cols,show="headings",selectmode="extended")
        for c,h,w in zip(cols,("Yo-kai","Level","Experience","Origin"),(260,70,110,80)):
            self.tree.heading(c,text=h); self.tree.column(c,width=w,anchor="w" if c=="name" else "center")
        sb=ttk.Scrollbar(left,orient="vertical",command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left",fill="both",expand=True); sb.pack(side="right",fill="y")
        self.tree.bind("<Control-a>",lambda e:self._select_all(self.tree))
        ttk.Button(transfer,text="Deposit  →",style="Accent.TButton",command=self.deposit,width=18).pack(pady=5)
        ttk.Button(transfer,text="←  Withdraw",style="Accent.TButton",command=self.withdraw,width=18).pack(pady=5)
        ttk.Separator(transfer).pack(fill="x",pady=14)
        ttk.Button(transfer,text="Move to box…",command=self.move,width=18).pack(pady=4)
        ttk.Button(transfer,text="Export selected…",command=self.export_package,width=18).pack(pady=4)
        ttk.Label(transfer,textvariable=self.pending_status,foreground="#9a3412",wraplength=145,justify="center").pack(pady=(22,0))
        ttk.Label(self,textvariable=self.status,relief="sunken",anchor="w",padding=(8,4)).pack(fill="x",side="bottom")

    def ids(self): return list(self.tree.selection())
    def _selected_in_view_order(self, tree):
        selected=set(tree.selection())
        return [item for item in tree.get_children() if item in selected]
    def _select_all(self, tree):
        tree.selection_set(tree.get_children()); return "break"
    def _set_dirty(self):
        if not self.dirty:
            self.bank_snapshot.parent.mkdir(parents=True,exist_ok=True); self.db.backup(self.bank_snapshot)
            self.pending_marker.write_text(json.dumps({"state":"staging","game":self.loaded_game}),encoding="utf-8")
        self.dirty=True; self.pending_status.set("Unsaved game changes")
        self.save_button.configure(state="normal"); self.discard_button.configure(state="normal")

    def _set_clean(self):
        self.dirty=False; self.pending_status.set("No pending changes")
        self.save_button.configure(state="disabled"); self.discard_button.configure(state="disabled")
        self.pending_marker.unlink(missing_ok=True)

    def _recover_interrupted_session(self):
        if not self.pending_marker.is_file():return
        try:
            marker=json.loads(self.pending_marker.read_text(encoding="utf-8")); state=marker.get("state")
            restore_bank=state=="staging"
            if state=="committing":
                original=Path(marker.get("original", "")); expected=marker.get("working_sha256", "")
                restore_bank=not original.is_file() or not expected or sha256_file(original)!=expected
            if restore_bank and self.bank_snapshot.is_file(): self.db.restore(self.bank_snapshot)
        except (OSError,json.JSONDecodeError,sqlite3.Error): pass
        self.pending_marker.unlink(missing_ok=True)

    def _working_copy(self, game, original):
        folder=self.root_dir/"working"/game; folder.mkdir(parents=True,exist_ok=True)
        target=folder/original.name; shutil.copy2(original,target)
        for name in ("head.yw","head.yw_g"):
            (folder/name).unlink(missing_ok=True)
            source=original.parent/name
            if source.is_file(): shutil.copy2(source,folder/name)
        return target

    def _refresh_game_view(self):
        for item in self.game_tree.get_children(): self.game_tree.delete(item)
        self.save_rows={}
        if not self.working_path:return
        rows=parse_save(self.loaded_game,self.working_path)
        for row in rows:
            iid=str(row["slot"]); self.save_rows[iid]=row
            self.game_tree.insert("","end",iid=iid,values=(row["name"],row["level"],f"{row['xp']:,}"))

    def _resolve_pending(self):
        if not self.dirty:return True
        answer=messagebox.askyesnocancel("Unsaved changes","Save pending game and Bank changes?\n\nYes: save\nNo: discard\nCancel: keep editing")
        if answer is None:return False
        return self.save_game() if answer else self.discard_changes(confirm=False)

    def _bank_xp(self, row):
        try:
            metadata=json.loads(row["metadata"]); raw=metadata.get("raw_record")
            return record_xp(metadata.get("binary_game",row["source_game"]),raw) if raw else int(metadata.get("xp",0))
        except (ValueError,TypeError,KeyError,struct.error,binascii.Error): return 0

    def refresh(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        box=0 if self.box.get()=="All boxes" else int(self.box.get().split()[1])
        rows=self.db.list(self.query.get().strip(),self.game.get(),box)
        for r in rows:
            label=r["nickname"] or r["name"]
            if r["nickname"]: label += f"  ({r['species']})"
            xp=self._bank_xp(r)
            self.tree.insert("", "end", iid=r["id"],values=(label,r["level"],f"{xp:,}",r["source_game"]))
        self.status.set(f"{len(rows)} shown • {self.db.count()} stored • Data: {self.root_dir}")

    def configure(self):
        if not self._resolve_pending():return
        d=ConfigureDialog(self,self.db.settings()); self.wait_window(d)
        if d.result is not None: self.db.save_settings(d.result); self.load_game()

    def load_game(self):
        requested=self.active_game.get()
        if self.dirty and not self._resolve_pending():
            if self.loaded_game:self.active_game.set(self.loaded_game)
            return
        game=requested; path=self.db.settings().get(game,"")
        if not path:
            self.loaded_game=None; self.original_path=None; self.working_path=None; self.save_rows={}
            for item in self.game_tree.get_children(): self.game_tree.delete(item)
            self.status.set(f"No {game} save configured. Open Configure saves…")
            return
        try:
            original=Path(path); self.working_path=self._working_copy(game,original); self.original_path=original; self.loaded_game=game; self._set_clean(); self._refresh_game_view()
            self.status.set(f"Loaded {len(self.save_rows)} Yo-kai from {game} • Transfers are staged until Save game")
        except (OSError,AdapterError) as exc:
            self.loaded_game=None; self.original_path=None; self.working_path=None; self.save_rows={}
            self.status.set(f"{game} unavailable: {exc}")
            messagebox.showwarning(f"Cannot load {game}",str(exc))

    def save_game(self):
        if not self.dirty:return True
        if not self.original_path or not self.working_path:return False
        saved=None
        try:
            saved=backup(self.original_path)
            marker={"state":"committing","game":self.loaded_game,"original":str(self.original_path),
                    "working_sha256":sha256_file(self.working_path)}
            self.pending_marker.write_text(json.dumps(marker),encoding="utf-8")
            shutil.copy2(self.working_path,self.original_path)
            parse_save(self.loaded_game,self.original_path)
            self._set_clean(); self.status.set(f"Saved {self.loaded_game} successfully • Backup: {saved}")
            messagebox.showinfo("Game saved",f"All pending transfers were saved.\n\nBackup: {saved}")
            return True
        except (OSError,AdapterError) as exc:
            if saved and saved.is_file(): shutil.copy2(saved,self.original_path)
            self.pending_marker.write_text(json.dumps({"state":"staging","game":self.loaded_game}),encoding="utf-8")
            messagebox.showerror("Save failed",f"The original save was restored.\n\n{exc}")
            return False

    def discard_changes(self, confirm=True):
        if not self.dirty:return True
        if confirm and not messagebox.askyesno("Discard changes","Discard every pending game and Bank transfer since the last save?"):return False
        try:
            if self.bank_snapshot.is_file(): self.db.restore(self.bank_snapshot)
            self.working_path=self._working_copy(self.loaded_game,self.original_path); self._set_clean(); self.refresh(); self._refresh_game_view()
            self.status.set("Pending transfers discarded; the original save was not changed.")
            return True
        except (OSError,sqlite3.Error,AdapterError) as exc:
            messagebox.showerror("Could not discard",str(exc)); return False

    def on_close(self):
        if self._resolve_pending(): self.destroy()

    def deposit(self):
        game=self.active_game.get(); selected=self._selected_in_view_order(self.game_tree)
        if game!=self.loaded_game or not self.working_path:
            messagebox.showinfo("Configure save",f"Configure and load the {game} save first."); return
        if not selected: messagebox.showinfo("Deposit","Highlight one or more Yo-kai in the game-save view."); return
        rows=[self.save_rows[i] for i in selected]
        if len(rows)>1 and not messagebox.askyesno("Mass deposit",f"Move {len(rows)} highlighted Yo-kai from {game} into the Bank?\n\nNothing is written to the original save until you choose Save game."): return
        path=self.working_path; completed=[]; failures=[]
        self._set_dirty()
        for row in rows:
            try:
                (remove_yw1(Path(path),row["slot"],row["raw_record"],make_backup=False) if game=="YW1" else remove_native(game,Path(path),row["slot"],row["raw_record"],make_backup=False))
                y=Yokai.from_dict({"name":row["name"],"species":row["species"],"nickname":row["nickname"],"source_game":game,"level":row["level"],"rank":"E","source_slot":str(row["slot"]+1),"metadata":{"raw_record":row["raw_record"],"species_id":row["species_id"],"binary_game":game,"xp":row["xp"]}})
                self.db.upsert(y); completed.append(row["name"])
            except (OSError,AdapterError,ValueError) as exc: failures.append(f"{row['name']}: {exc}")
        self.refresh(); self._refresh_game_view()
        detail=f"Staged {len(completed)} of {len(rows)} highlighted Yo-kai for deposit.\n\nPress Save game to commit."
        if failures: detail+="\n\nFailed:\n"+"\n".join(failures[:8])
        (messagebox.showwarning if failures else messagebox.showinfo)("Mass deposit complete",detail)

    def withdraw(self):
        ids=self._selected_in_view_order(self.tree); game=self.active_game.get()
        if not ids: messagebox.showinfo("Withdraw","Highlight one or more Yo-kai in the Local Bank view."); return
        path=self.working_path
        if not path: messagebox.showinfo("Configure save",f"Configure the {game} save path first."); return
        items=[(ident,self.db.get(ident)) for ident in ids]; ready=[]; failures=[]
        for ident,y in items:
            raw=y.metadata.get("raw_record"); source_game=y.metadata.get("binary_game")
            if not source_game or not raw: failures.append(f"{y.name}: no native source record")
            elif not compatible_with(game,y.species,source_game): failures.append(f"{y.name}: {y.species} does not exist in {game}")
            else: ready.append((ident,y,raw,source_game))
        if not ready:
            messagebox.showwarning("Nothing to transfer","No highlighted Yo-kai can be transferred.\n\n"+"\n".join(failures[:8])); return
        cross=sum(source_game!=game for _,_,_,source_game in ready)
        prompt=f"Move {len(ready)} highlighted Yo-kai into {game}?"
        if cross:
            prompt+=f"\n\n{cross} require cross-game conversion. Species, nickname and level are preserved. IV distribution is translated into the destination's legal format; destination-only fields are rebuilt."
            if game=="BUSTERS2": prompt+=" Busters 2 has no per-Yo-kai XP or attitude fields."
            elif game=="BLASTERS": prompt+=" Blasters has no attitude field and uses four battle IV stats."
            else: prompt+=" XP and attitude are retained when the source format stores them."
        if failures: prompt+=f"\n\n{len(failures)} incompatible selection(s) will be skipped."
        if len(ready)>1 or cross:
            if not messagebox.askyesno("Mass transfer",prompt): return
        completed=[]; self._set_dirty()
        for ident,y,raw,source_game in ready:
            try:
                if source_game==game: slot,_=(insert_yw1(Path(path),raw,make_backup=False) if game=="YW1" else insert_native(game,Path(path),raw,make_backup=False))
                else: slot,_=insert_cross_game(source_game,game,y.species,y.nickname,y.level,raw,Path(path),make_backup=False)
                self.db.delete([ident]); completed.append(f"{y.name} → slot {slot+1}")
            except (OSError,AdapterError,ValueError) as exc: failures.append(f"{y.name}: {exc}")
        self.refresh(); self._refresh_game_view()
        detail=f"Staged {len(completed)} of {len(items)} highlighted Yo-kai for withdrawal into {game}.\n\nPress Save game to commit."
        if failures: detail+="\n\nSkipped/failed:\n"+"\n".join(failures[:8])
        (messagebox.showwarning if failures else messagebox.showinfo)("Mass transfer complete",detail)

    def move(self):
        ids=self.ids()
        if not ids: return
        box=simpledialog.askinteger("Move to box","Box number (1–32):",parent=self,minvalue=1,maxvalue=32)
        if box:
            for ident in ids: y=self.db.get(ident); y.box=box; y.updated_at=utc_now(); self.db.upsert(y)
            self.refresh()
    def export_package(self):
        ids=self.ids()
        if not ids: messagebox.showinfo("Export","Select one or more Yo-kai."); return
        path=filedialog.asksaveasfilename(defaultextension=".ykbank",filetypes=[("Yo-kai Bank package","*.ykbank"),("JSON","*.json")])
        if not path: return
        records=[]
        for ident in ids:
            d=asdict(self.db.get(ident)); records.append(d)
        payload={"format":"yokai-watch-bank","version":1,"created_at":utc_now(),"yokai":records}
        canonical=json.dumps(records,ensure_ascii=False,sort_keys=True,separators=(",",":")); payload["records_sha256"]=hashlib.sha256(canonical.encode()).hexdigest()
        Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        self.status.set(f"Exported {len(records)} Yo-kai to {path}")

    def import_file(self):
        path=filedialog.askopenfilename(filetypes=[("Bank packages and JSON","*.ykbank *.json"),("All files","*.*")])
        if not path:return
        try:
            payload=json.loads(Path(path).read_text(encoding="utf-8")); records=payload if isinstance(payload,list) else payload.get("yokai",[])
            if not isinstance(records,list): raise ValueError("Expected a yokai array")
            if isinstance(payload,dict) and payload.get("records_sha256"):
                canonical=json.dumps(records,ensure_ascii=False,sort_keys=True,separators=(",",":"))
                if hashlib.sha256(canonical.encode()).hexdigest()!=payload["records_sha256"]: raise ValueError("Package integrity check failed")
            imported=0
            for raw in records:
                y=Yokai.from_dict(raw); y.id=str(uuid.uuid4()); self.db.upsert(y); imported+=1
            self.refresh(); messagebox.showinfo("Import complete",f"Imported {imported} Yo-kai.")
        except (OSError,json.JSONDecodeError,ValueError,TypeError) as exc: messagebox.showerror("Import failed",str(exc))

    def attach_save(self):
        path=filedialog.askopenfilename(title="Select a decrypted 3DS save",filetypes=[("Yo-kai saves","game*.yw *.yw *.bin"),("All files","*.*")])
        if not path:return
        game=simpledialog.askstring("Game","Game: YW1, YW2, YW3, BLASTERS, or BUSTERS2",parent=self)
        if not game:return
        game=game.upper().strip()
        if game not in GAMES: messagebox.showerror("Unsupported game","Use YW1, YW2, YW3, BLASTERS, or BUSTERS2."); return
        region=simpledialog.askstring("Region","Region/version (for example USA, EUR, JPN):",parent=self) or "Unknown"
        source=Path(path); snap_dir=self.root_dir/"snapshots"; snap_dir.mkdir(exist_ok=True)
        stamp=datetime.now().strftime("%Y%m%d-%H%M%S"); target=snap_dir/f"{game}-{region}-{stamp}-{source.name}"
        shutil.copy2(source,target); digest=sha256_file(target)
        self.db.add_snapshot(game,region,source.name,target,digest,target.stat().st_size)
        messagebox.showinfo("Save attached safely",f"Created a read-only working snapshot.\n\nSHA-256: {digest}\n\nThe original was not changed.")

    def show_snapshots(self):
        rows=self.db.snapshots()
        if not rows: messagebox.showinfo("Save snapshots","No snapshots yet."); return
        text="\n".join(f"{r['created_at']}  {r['game']} {r['region']}  {r['original_name']}\n  {r['sha256'][:20]}…" for r in rows[:20])
        messagebox.showinfo("Save snapshots",text)
    def backup_bank(self):
        path=filedialog.asksaveasfilename(defaultextension=".sqlite3",initialfile=f"yokai-bank-{datetime.now():%Y%m%d}.sqlite3",filetypes=[("SQLite database","*.sqlite3")])
        if path: self.db.backup(Path(path)); messagebox.showinfo("Backup complete",f"Bank backed up to:\n{path}")
    def demo(self):
        if self.db.count() and not messagebox.askyesno("Demo data","Add demo records to your existing Bank?"): return
        for raw in (
            {"name":"Jibanyan","species":"Jibanyan","source_game":"YW1","level":12,"rank":"D","tribe":"Charming"},
            {"name":"Komasan","species":"Komasan","source_game":"YW2","level":18,"rank":"D","tribe":"Charming"},
            {"name":"Usapyon","species":"Usapyon","source_game":"YW3","level":25,"rank":"B","tribe":"Shady"},): self.db.upsert(Yokai.from_dict(raw))
        self.refresh()


if __name__ == "__main__":
    App().mainloop()
