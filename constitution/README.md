# constitution/README

Files: `CONSTITUTION.md` (C-001/C-003/C-005), `data.md` (D-101/D-103/D-107/D-108/D-114), `CHANGELOG.md`.

```bash
python tools/check_invariants.py --all
python tools/ledger.py verify
```

New entries must pass before merge: falsifiable statement, no degree words; concrete violation exhibitable; binary violation; inputs directly readable. Fail ⇒ design note, not invariant.
