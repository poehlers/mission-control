"""docs/ROADMAP.md -> four columns. Format:

## Done | ## Now | ## Planned | ## Ideas
- [x] text — optional note (2026-10-05)
  indented lines continue the previous item's note

Anything else under a heading is reported in `problems` rather than silently dropped.
"""
import re

COLUMNS = ["Done", "Now", "Planned", "Ideas"]
ITEM = re.compile(r"^[-*] \[([ xX])\]\s+(.*)$")
DATE = re.compile(r"\s*\((\d{4}-\d{2}-\d{2})\)\s*$")
NOTE = re.compile(r"\s+(?:—|--)\s+")


def parse(text):
    cols = {c: [] for c in COLUMNS}
    problems, col, item, seen_heading = [], None, None, False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if line.startswith("## "):
            name = line[3:].strip()
            seen_heading, item = True, None
            col = name if name in cols else None
            if col is None:
                problems.append(f"line {n}: unknown column '## {name}' (expected {', '.join(COLUMNS)})")
            continue
        if not line.strip() or line.startswith("# "):
            continue
        m = ITEM.match(line)
        if m and col:
            body, date = m.group(2), None
            if d := DATE.search(body):
                date, body = d.group(1), body[:d.start()]
            parts = NOTE.split(body, maxsplit=1)
            item = {"text": parts[0].strip(), "done": m.group(1) != " ",
                    "note": parts[1].strip() if len(parts) > 1 else None, "date": date}
            cols[col].append(item)
        elif item and raw[:1] in (" ", "\t"):
            item["note"] = f"{item['note']} {line.strip()}" if item["note"] else line.strip()
        elif seen_heading or m:  # prose above the first heading is fine; anything else is off-format
            where = "not a '- [ ] item'" if col else "outside the four columns"
            problems.append(f"line {n}: {where}: {line.strip()[:80]}")
    for c in COLUMNS:
        if not re.search(rf"^## {c}\s*$", text, re.M):
            problems.append(f"missing '## {c}' heading")
    return {"columns": cols, "problems": problems}
