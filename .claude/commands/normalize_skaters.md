# Normalize Skaters

Build or rebuild the `skaters` table and populate `skater_id` FK columns on all four result
tables (`results`, `skater_entries`, `classification`, `time_classification`).

## Usage
```
/normalize_skaters
```

No arguments. The script is interactive (review CSV output), so run the steps below in order.

---

## Overview

The normalization pipeline has three phases:

1. **Run `normalize_skaters.py`** — seed from CSV, match raw names, create new skater rows
2. **Post-normalization merges** — manual fuzzy merges not caught by the script
3. **Persistent manual fixes** — same-name different-person splits that survive `--force` re-runs

---

## Phase 1 — Run the normalization script

```bash
source .venv/bin/activate && python3 scripts/normalize_skaters.py [--force]
```

Use `--force` to clear the skaters table and re-run from scratch (required after any DB-level
name fixes). Without `--force`, the script exits if the skaters table already has rows.

### What the script does

**Seed:** loads 3,582 deduplicated skaters from `knowledge_base/skaters_cleaner.csv` into the
`skaters` table (`source='csv'`).

**Pre-cleaning per raw name (before matching):**
- `clean_name()` — strips gender markers (`*`, `^`, `(F)/(M)`), parentheticals, Masters suffixes,
  trailing `m/f` letters, comma-format `Last, First` inversion
- `flip_caps_first()` — detects ALL-CAPS first token (speedskatinglive / AmCup convention:
  `ZHANG Dylan`) and reverses to `Dylan Zhang` before matching. Triggered when the first token
  is ≥ 3 all-uppercase alpha chars.

**6-pass matching:**

| Pass | Method | Base score |
|---|---|---|
| 1 | Exact `norm()` after unicode clean + caps-flip | auto-accept |
| 2 | Exact `norm_sorted()` — order-invariant, catches LASTNAME Firstname ↔ Firstname LASTNAME | 60 |
| 3 | Initial expansion — `A. Zhang` → `Aiden Zhang` (same last name, same first initial) | 30 |
| 4 | Levenshtein ≤ 2 within same last-name group | 35–45 |
| 5–6 | rapidfuzz token_sort_ratio ≥ 85/92 | 25–40 |

**Score bonuses/penalties:** gender match +15, gender conflict −25; birth year overlap +20,
mismatch −15; time inconsistency (times >10% faster than known best) −10.

**Thresholds:** score ≥ 60 → auto-accept; score 45–59 → written to review CSV; < 45 → new skater.

**Season-aware regression guard (fuzzy passes only):** if combining two name variants would
produce a >40% performance regression on any distance across seasons, the match is rejected.
This prevents merging a high-level variant name with a beginner variant name.

**Outputs:**
- Populated `skater_id` FK columns on all four tables (98–99% link rate)
- `data/skater_matching_review.csv` — medium-confidence pairs (score 45–59) for manual review
- New `source='db_new'` rows for unmatched names

**Expected results (as of 2026-05):**
```
results               linked: ~81,344/82,215 (98%)
skater_entries        linked: ~10,735/10,838 (99%)
classification        linked: ~52,475/53,208 (98%)
time_classification   linked: ~16,617/16,949 (98%)

Skaters: ~3,860 total  |  3,582 from CSV  |  ~280 new (db_new)

Match breakdown:
  exact_norm    ~3,134
  exact_sorted     ~18
  levenshtein_1     ~4
  new             ~280
  review           ~53
```

---

## Phase 2 — Review and merge new skaters

After the script completes, inspect `data/new_skaters_review.csv`. It lists every `db_new` skater
with their event coverage, seasons, clubs, and raw aliases.

### Step 2a — Remove relay/team entries

```python
# Already handled in script — team names auto-detected and excluded
# If any slip through, delete manually:
#   DELETE FROM skaters WHERE full_name LIKE '%Team%' OR full_name LIKE '%Relay%'
```

### Step 2b — Fuzzy merge pass

Run the merge candidate finder to catch names the script missed (unicode issues, nicknames,
lev_1 across different last-name groups):

```bash
source .venv/bin/activate && python3 - <<'EOF'
# See scripts/normalize_skaters.py for the full merge candidate logic.
# Key patterns caught:
#   unicode_exact: Gabriel Wöchtl → Gabriel Wochtl
#   nickname:      Zach → Zachary, Dan → Daniel, Charlie → Charles, etc.
#   lev_1:         Sean Shua → Sean Shuai, Aiden Mecham → Aiden Meacham
EOF
```

**IMPORTANT — always fix the raw `skater_name` in addition to `skater_id`:**

When merging a db_new skater into a CSV skater, update both the FK *and* the raw name in all
four tables, then delete the db_new row. If only `skater_id` is updated, a future `--force`
re-run will re-create the duplicate (the script won't match names like "Sean Shua" to "Sean
Shuai" because they have different last-name tokens and bypass the lev pass).

```sql
-- Template for each merge:
UPDATE results         SET skater_name=:canonical, skater_id=:csv_id WHERE skater_name=:wrong;
UPDATE skater_entries  SET skater_name=:canonical, skater_id=:csv_id WHERE skater_name=:wrong;
UPDATE classification  SET skater_name=:canonical, skater_id=:csv_id WHERE skater_name=:wrong;
UPDATE time_classification SET skater_name=:canonical, skater_id=:csv_id WHERE skater_name=:wrong;
UPDATE skaters SET known_aliases = known_aliases || ' | ' || :wrong WHERE id=:csv_id;
DELETE FROM skaters WHERE id=:db_new_id;
```

### Step 2c — Specific name corrections

Known corrections to always apply after a fresh run:

| Wrong raw name | Correct name | CSV id | Notes |
|---|---|---|---|
| `AO Richard Hao` | `Richard Hao` | 222 | "AO" is a relay/age-group prefix |
| `AQ Jei Lim` | `Jei Lim` | 492 | "AQ" is a relay/age-group prefix |

---

## Phase 3 — Same-name different-person splits

The script cannot handle two genuinely different people sharing the exact same raw name (e.g.,
two different "Nathan Zhang"). These are detected post-hoc by checking for implausible season
regressions (>40% slower) across all distances.

**Confirmed splits (re-apply after every `--force` run):**

### Nathan Zhang split

Skater id=615 (csv) is an elite skater (500m ~45s in 2018–2022).
The 2025-2026 "Nathan Zhang" is a different beginner (500m ~113s).

```python
# Create new skater for the 2025-2026 beginner
db.execute(text("""
    INSERT INTO skaters (full_name, first_name, last_name, normalized_name, source, known_aliases)
    VALUES ('Nathan Zhang', 'Nathan', 'Zhang', 'nathan zhang', 'db_new', 'Nathan Zhang 2025-2026')
"""))
new_sid = db.execute(text("SELECT last_insert_rowid()")).scalar()

# Re-link 2025-2026 rows to new skater (all 4 tables)
for tbl in ('results', 'skater_entries', 'classification', 'time_classification'):
    db.execute(text(f"""
        UPDATE {tbl} SET skater_id = :new_sid
        WHERE skater_id = 615
          AND event_id IN (SELECT id FROM events WHERE season = '2025-2026')
    """), {"new_sid": new_sid})
db.commit()
```

**Expected times after split:**

| Skater | Season | 333m | 500m | 777m | 1000m | 1500m |
|---|---|---|---|---|---|---|
| Nathan Zhang csv (id=615) | 2018-2019 | 35.4 | 52.6 | 84.3 | 110.2 | 177.0 |
| Nathan Zhang csv (id=615) | 2019-2020 | — | 48.4 | 81.5 | 99.3 | 155.3 |
| Nathan Zhang csv (id=615) | 2021-2022 | — | 45.6 | — | 91.5 | 147.5 |
| Nathan Zhang new (2025-2026) | 2025-2026 | 73.0 | 113.2 | — | — | — |

### Andrew Kim split

Skater id=841 (csv) raced in 2018-2020 (500m ~42s). The 2022-2023 "KIM Andrew" is a different
skater (~30% slower across all distances).

```python
db.execute(text("""
    INSERT INTO skaters (full_name, first_name, last_name, normalized_name, source, known_aliases)
    VALUES ('Andrew Kim', 'Andrew', 'Kim', 'andrew kim', 'db_new', 'KIM Andrew 2022-2023')
"""))
new_sid = db.execute(text("SELECT last_insert_rowid()")).scalar()

for tbl in ('results', 'skater_entries', 'classification', 'time_classification'):
    db.execute(text(f"""
        UPDATE {tbl} SET skater_id = :new_sid
        WHERE skater_id = 841
          AND event_id IN (SELECT id FROM events WHERE season = '2022-2023')
    """), {"new_sid": new_sid})
db.commit()
```

---

## Verification

```python
from src.db.session_v2 import get_session
from sqlalchemy import text

db = get_session()
for tbl in ('results', 'skater_entries', 'classification', 'time_classification'):
    linked = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE skater_id IS NOT NULL")).scalar()
    total  = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
    print(f"{tbl:<25} {linked:,}/{total:,} ({100*linked//total}%)")

total_sk = db.execute(text("SELECT COUNT(*) FROM skaters")).scalar()
new_sk   = db.execute(text("SELECT COUNT(*) FROM skaters WHERE source='db_new'")).scalar()
print(f"\nSkaters: {total_sk:,} total, {new_sk:,} db_new")
db.close()
```

**Regression check** — after any merge or re-run, verify no skater has a >30% season-over-season
regression across multiple distances:

```python
# See Phase 3 above for the detection query.
# Any skater with >40% regression on a key distance warrants manual inspection.
```

---

## Key files

| File | Purpose |
|---|---|
| `scripts/normalize_skaters.py` | Main normalization script |
| `knowledge_base/skaters_cleaner.csv` | 3,582-row seed from v1 DB (read-only) |
| `data/skater_matching_review.csv` | Medium-confidence candidates (score 45–59) |
| `data/skater_merge_review.csv` | Lower-confidence fuzzy candidates from manual pass |
| `data/new_skaters_review.csv` | All db_new skaters with event/season/club context |

---

## Known limitations

1. **Same raw name, different people:** Pass 1 (exact_norm) accepts all rows with the same raw
   name unconditionally. Two skaters named "Nathan Zhang" cannot be split at match time — only
   detected post-hoc via season regression analysis (Phase 3 above).

2. **Regression guard scope:** the >40% regression check only applies to fuzzy passes (2–6), not
   Pass 1. Exact-name same-person conflicts require manual splitting.

3. **`--force` re-run resets all splits:** the Nathan Zhang and Andrew Kim splits must be
   re-applied manually after every `--force` run (Phase 3 above).

4. **Unlinked rows (expected ~2%):** primarily relay/team entries, single-token names (e.g.
   `MANNING`, `FISCHER`), or parsing artifacts. These are not errors.
