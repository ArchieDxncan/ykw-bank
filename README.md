# Yo-kai Watch Bank

A Windows desktop transfer bank for **Yo-kai Watch 1–3, Blasters, and
Busters 2**.
It reads original encrypted `game*.yw` exports and keeps the local Bank in
SQLite.

## Version 0.5 interface

- Equal-width **Game Save** and **Local Bank** views
- Game list columns: Yo-kai, level, and experience
- Bank list columns: Yo-kai, level, experience, and origin game
- Multi-select deposit and withdrawal (`Ctrl+A` selects the visible list)
- Mass transfers preserve the selected Yo-kai's visible top-to-bottom order
- The Local Bank keeps arrival order instead of alphabetically re-sorting deposited batches
- Move-only transfers: withdrawing removes the Bank copy, so the interface
  cannot clone a Yo-kai
- Manual save workflow: transfers are staged in a working copy until **Save
  game** is pressed
- **Discard changes** restores both the game view and Bank to their last saved
  state
- One timestamped backup is created when a staged session is committed, not
  after every transfer
- Interrupted-session recovery keeps the game and Bank sides synchronized
- Import and advanced safety tools are hidden from this version's main screen

## Save and transfer support

- Configure one save path each for YW1, YW2, YW3, Blasters, and Busters 2
- Automatic YW1 decryption and re-encryption
- Automatic YW2/YW3/Blasters/Busters 2 authenticated decryption and
  re-encryption; keep matching `head.yw`/`head.yw_g` files beside the game save
- Blasters supports localized base-game and Moon Rabbit Crew `game*.yw_g`
  saves, including variable 414/418-slot layouts
- Busters 2 supports the Japanese Sword/Magnum 766-slot layout
- Reversible same-game deposit and withdrawal for every supported game
- Bidirectional cross-game conversion when the exact species/form exists in the
  destination game
- Safe base-name alias handling, such as YW2 `Hungramps (Starver)` mapping to
  YW3 `Hungramps`; unrelated reused numeric IDs remain blocked
- Species/form compatibility blocks for Yo-kai absent from the destination
- Destination-native record construction and stat validation
- Preserves nickname, level, current experience, and attitude during cross-game
  conversion; see the exact conversion contract below
- Preserves the cached HP fields so transferred Yo-kai do not arrive with zero
  health
- Preserves YW2/YW3 IVs exactly and translates YW1's 10-point IV distribution
  into the equivalent legal 40-point YW2/YW3 distribution

## Using the Bank

1. Open **Configure saves…** and select each original `game*.yw` file.
2. Choose a configured game above the Game Save list.
3. Highlight one or more Yo-kai and choose **Deposit** or **Withdraw**.
4. Review the staged result in both lists.
5. Choose **Save game** to commit, or **Discard changes** to undo the entire
   pending session.

Switching games or closing the app while changes are pending asks whether to
save or discard them.

## Exact transfer contract

Same-game transfers store and restore the complete native Yo-kai record. The
record bytes are preserved exactly; only its save slot and the save's slot index
can change.

Cross-game conversion deliberately creates a new destination-native record:

| Data | YW2 ↔ YW3 | Any transfer involving YW1 | Blasters / Busters 2 |
| --- | --- | --- | --- |
| Species/form | Preserved by exact name, or a shared ID whose base species name also matches | Preserved only by an exact destination name | Preserved only when the destination species table contains the same species/form |
| Nickname | Preserved, subject to destination byte limit | Preserved, subject to destination byte limit | Preserved, subject to destination byte limit |
| Level | Preserved | Preserved | Preserved |
| Current per-level XP | Preserved | Preserved | Blasters stores XP; Busters 2 does not |
| Attitude | Preserved | Preserved across YW1–YW3 | Not stored by either action-game format |
| Seriousness/loafing | Preserved | YW1 Serious/Stiff maps to matching YW2/YW3 values | Not stored |
| IV distribution | Preserved exactly when valid | Scaled between the 10- and 40-point rules | Busters 2 uses five stats; Blasters' four-stat distribution is proportionally translated |
| Moves | Reset | Reset | Rebuilt from the destination game's species defaults |
| Ownership/record numbers | Recalculated | Recalculated | Recalculated from the destination save |
| Every other record byte | Dropped/reset | Dropped/reset | Dropped/reset |

YW1 uses a 10-point IV distribution; YW2 and YW3 use 40 points and store HP at
double scale. Upward conversion scales the complete distribution exactly. A
YW1 allocation of `1/2/3/1/3`, for example, becomes the legal YW2/YW3 stored
allocation `8/8/12/4/12` (the first stored value represents four HP points).
Downward conversion uses deterministic proportional rounding back to 10 points.

“Every other record byte” includes game-specific state that the Bank does not
yet decode and map. Equipment, move/skill advancement, favorite
flags, origin/acquisition details, and similar values must therefore be treated
as not preserved during cross-game conversion.

## Run on Windows

Install Python 3.11 or newer from python.org with “Add Python to PATH” enabled,
then double-click `run.bat`, or run:

```powershell
py -3.11 app.py
```

Application data lives in `%APPDATA%\YoKaiWatchBank` (or beside the app if
`APPDATA` is unavailable).

## Build a standalone EXE

Double-click `build_windows.bat`. The result is
`dist\YoKaiWatchBank.exe`.

## Legal note

Use saves you own and export them with lawful save-management tools. This
project contains no game files or keys.
