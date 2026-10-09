"""Roadmap parser: four ## columns, - [ ]/- [x] items, optional ' — note' and trailing (YYYY-MM-DD)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from roadmap import COLUMNS, parse

SAMPLE = """# MIMS roadmap

Intro text is allowed above the first column.

## Done
- [x] Isomer panel (241 queries) — training-free baselines 0.324 / 0.530 / 0.543 (2026-10-01)
- [x] S1 masked-peak pretraining adopted — panel 0.616 (2026-10-03)

## Now
- [ ] Clean harness -- the 0.413 harness is contaminated
  fe_D is clean (0.590 on np)
- [ ] Phase-2 G3 shards (2026-10-09)

## Planned
- [ ] Final submission choice (2026-12-07)
* [X] stray star bullet, upper-case X

## Ideas
- [ ] Engine-2 revisit
some paragraph that isn't an item

## Someday
- [ ] lost item
"""

r = parse(SAMPLE)
assert list(r["columns"]) == COLUMNS == ["Done", "Now", "Planned", "Ideas"]
done, now, planned, ideas = (r["columns"][c] for c in COLUMNS)
assert done[0] == {"text": "Isomer panel (241 queries)", "done": True,
                   "note": "training-free baselines 0.324 / 0.530 / 0.543", "date": "2026-10-01"}, done[0]
assert done[1]["note"] == "panel 0.616" and done[1]["date"] == "2026-10-03"
assert now[0] == {"text": "Clean harness", "done": False,
                  "note": "the 0.413 harness is contaminated fe_D is clean (0.590 on np)", "date": None}, now[0]
assert now[1] == {"text": "Phase-2 G3 shards", "done": False, "note": None, "date": "2026-10-09"}
assert planned[1]["done"] and planned[1]["text"] == "stray star bullet, upper-case X"
assert ideas == [{"text": "Engine-2 revisit", "done": False, "note": None, "date": None}]
# what didn't fit the format is reported, not silently dropped
probs = " | ".join(r["problems"])
assert "some paragraph" in probs and "Someday" in probs and "lost item" in probs, r["problems"]
assert not any("Intro text" in p for p in r["problems"])  # prose before the first column is fine

empty = parse("# nothing here\n")
assert all(empty["columns"][c] == [] for c in COLUMNS) and any("Done" in p for p in empty["problems"])
print("ok test_roadmap")
