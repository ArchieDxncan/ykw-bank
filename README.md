# Yo-kai Watch Bank

A simple desktop tool for moving your Yo-kai between your saved games for
**Yo-kai Watch 1, 2, 3, Blasters, Busters 2, and Yo-kai Watch 4**.

<img width="1282" height="792" alt="aergadsrg" src="https://github.com/user-attachments/assets/334521e2-4b87-45b8-a29c-cfaa565d7c7c" />

<img width="2160" height="2880" alt="IMG_0075" src="https://github.com/user-attachments/assets/661490d1-8d09-4819-91f1-7db4e5339580" />
<img width="2160" height="2880" alt="IMG_0074" src="https://github.com/user-attachments/assets/5a5737ea-90f0-4b60-b613-ae47cd7c9623" />


## What you can do with it

- See your current game's Yo-kai and your Bank side by side
- Check each Yo-kai's level and experience at a glance
- Transfer to and from same generation (YW2 -> YW2)
- Transfer to and from newer generations (YW1 -> YW3)
- Transfer to and from older generations (YW3 -> YW2)
- Import and export YKSM's native `bank.ykb` files
- Bank Yo-kai from a YW4 `data.bin` save

## Notes
- Nothing is ever duplicated — once you move a Yo-kai out of the Bank, it's
  gone from the Bank
- Nothing changes for real until you choose to save
- You can undo everything since your last save with one click
- A backup copy is made automatically every time you save, so you always have
  something to fall back on

## Getting started

1. Open **Configure saves…** and point the app at your save file for each
   game you play. For YW4, choose the `data.bin` inside the Checkpoint/JKSV
   `UserData` or `AutoSave` backup you want to edit.
2. Pick which game you want to work with from the list.
3. Click on one or more Yo-kai, then choose **Deposit** (send them from your
   game into the Bank) or **Withdraw** (bring them from the Bank into your
   game).
4. Look over the result before committing to anything.
5. Click **Save game** to make it permanent, or **Discard changes** to cancel
   everything you just did.

If you try to switch games or close the app while you have unsaved changes,
it will ask whether you want to save or discard them first.

## Moving a Yo-kai to a different game

Moving a Yo-kai between two different games isn't a straight copy, the app
has to rebuild that Yo-kai so it fits the new game's format. Here's roughly
what survives the move and what doesn't:

**Usually kept:**
- Nickname
- Level
- Experience
- Personality/attitude (when moving between the main story games)

**Usually lost, and reset to that game's defaults:**
- Learned moves
- Equipment
- Any other extra details tied to how you originally got that Yo-kai

A Yo-kai can only move to a game if that same Yo-kai actually exists in
that game. A few Yo-kai are exclusive to certain titles and simply can't be
sent there and obviously Yo-Kai such as Hovernyan from newer games cant be transferred to an older game like YW1.

### Yo-kai Watch 4 transfers

YW4 uses a very different battle system. A same-game YW4 deposit and withdrawal
keeps the complete 469-byte Yo-kai record; only the destination save's unique
record IDs and ordering index are reassigned. A cross-game transfer keeps the
species, nickname, level, and XP. YW4-only skills, equipment, HP/YP/PG values,
and training bonuses cannot be translated from the 3DS games, so new YW4
records begin with safe defaults. Transfers from YW4 back to a 3DS game also
start with legal default IVs and temperament because YW4 has no equivalent
3DS representation.

Only species in the YW4 editor's tested 197-entry healthy roster are offered as
cross-game destinations. Existing YW4 records from the full known signature
list can still be deposited and restored to YW4 unchanged.

YW4 empty roster slots contain required initialized defaults. Version 0.6.1
preserves that complete slot template when creating a cross-game record and
rejects the older incomplete zero-built records that could crash YW4 while
loading a save.

### YKSM bank interoperability

- **Import YKSM bank…** reads YKSM's binary `bank.ykb`, validates its CRC-32,
  and merges its entries into the Local Bank. Re-importing the same YKSM IDs
  updates those entries instead of cloning them.
- **Export to YKSM…** writes the highlighted 3DS entries in YKSM's native
  `YKB1` format. YKSM's format supports YW1, YW2, YW3, Blasters, and Busters 2;
  it does not have a YW4 game code.
- Back up the existing SD-card file before replacing
  `/3ds/YKSM/bank.ykb` with an exported bank.
