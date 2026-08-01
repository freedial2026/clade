# Data holdings and measurement methods

What this project has, where it came from, and how claims about it get
tested. Written 2026-08-01; the row counts are that day's.

`tasks/HANDOFF.md` records findings in the order they were made. This
records the *inventory* and the *toolkit*, so a later session can see what
exists without rereading the narrative.

---

## Part 1 — Data

All of it lives on `192.168.11.21` in PostgreSQL 17 (`boat_prediction`,
schema revision `57a2b01ded66`). Raw source files sit under
`data/raw/boatrace/` on both the host and the development PC.

### Sources

| source | what | terms | how obtained |
|---|---|---|---|
| **B-file** (番組表) | race cards | official download | `b_file_source.py`, 3 s rate limit |
| **K-file** (競走成績) | results, payouts | same | `official_source.py` |
| **boatrace.jp odds** | 締切時オッズ, live pre-deadline | site prohibits large-volume access | `odds_source.py`, ~300 req/day |
| **boatrace.jp 直前情報** | exhibition, tilt, 進入, surface weather | same | `beforeinfo_source.py`, ~150 req/day |
| **JMA** | daily weather per venue | 公共データ利用規約 1.0 | `jma_weather_source.py` |
| **ファン手帳** | racer period stats incl. per-course | official download | `fan_file_source.py` |
| **Boatrace Open API** | 直前情報 mirror, 2023-05+ | **unofficial**; repo deprecated. MIT covers the *code*, not the underlying data | `boatrace_openapi_source.py` |

The Open API is a third-party mirror and is treated as such: it was
cross-validated against the official page before use (147 of 150
boat-level values identical; the three that differed were a start-timing
sign convention). It is used for **backfill only** — it updates every few
hours and could never serve a pre-deadline decision.

**Two things about terms that are easy to get wrong, and were:**

- A repository's MIT licence covers **its code**, not the content it
  mirrors. The underlying BOATRACE data carries its own rights and the
  mirror cannot grant them. An earlier version of this table implied
  otherwise.
- **A permissive `robots.txt` is not permission.** It states what a
  crawler may fetch; it says nothing about licensing, redistribution, or
  the site policy's prohibition on large-volume access. The rate limits
  in the table are this project's own restraint, not a stated ceiling.

Raw HTML, video and comment text are held for internal research and are
not redistributed.

### Raw archive (host)

| directory | files |
|---|---|
| `K/` | 7,865 |
| `B/` | 7,866 |
| `odds/` | 12,085 |
| `jma/` | 6,216 |
| `fan/` | 50 |
| `venue/` | 48 |

### Database

| table | rows | coverage |
|---|---|---|
| `races` | 1,164,099 | 2005-01-01 … 2026-08-01 |
| `race_entries` | 6,984,306 | " |
| `race_results` | 1,152,133 | 2005-01-01 … 2026-07-31 |
| `race_result_entries` | 6,912,798 | " |
| `race_payouts` | 11,303,542 | " |
| `before_info_entries` | 1,094,385 | 2023-05-01 … 2026-08-01 |
| `race_surface_conditions` | 182,397 | " |
| `odds_snapshots` | 214,518 | 2025-07-29 … 2026-08-01 |
| `weather_observations` | 189,096 | 2005-01-01 … 2026-07-28 |
| `racer_period_stats` | 40,204 | periods 2013-11 … 2026-04 |
| `racer_period_course_stats` | 241,224 | " |
| `race_meetings` | 17,860 | 2005 … 2026 |
| `race_predictions` | 630 | 2026-08-01 (starts here) |
| `exhibition_entries` | 5,829 | **see known gap below** |

Notable contents:

- `winning_method` (決まり手) populated on **1,152,001** results.
- `odds_snapshots`: **211,420 closing** rows, **3,098 live pre-deadline**
  — the second number is the one that matters and it started growing on
  2026-08-01.
- 直前情報 carries an exhibition time on 1,094,381 rows and shows
  **89,281 進入変更** (8.2%), the fraction for which joining per-course
  statistics on lane number is simply wrong.

### Collected daily (cron on .21)

| JST | job | requests/day |
|---|---|---|
| 06:30 | `ingest_daily card` | ~1 |
| 06:45 | `predict_daily` | 0 (database only) |
| every 2 min (even), 08-21 | `capture_odds` | ~300 |
| every 2 min (odd), 08-21 | `capture_beforeinfo` | ~150 |
| 02:00 | `ingest_daily results` | ~1 |

The odd/even split keeps the two scrapers from firing together; each
paces its own requests 3 s apart.

### Known gaps

- **`exhibition_entries` holds 5,829 rows against 6.98 M entries.** The
  K-file carries an exhibition time for every race, and the parser reads
  it, but `loader.load_k_file_day` only writes the row when a
  `race_entry_id` already exists — so any day whose K-file was loaded
  before its B-file lost it. Fixing this needs `race_id` + `lane_number`
  on the table and a nullable `race_entry_id`. Lower priority now that
  直前情報 supplies the same values with *pre-race* availability.
- **Fields only the Open API has**: 平均ST, F/L counts, 3連対率 for
  racer/motor/boat, grade (SG/G1/G2/G3), birthplace. Confirmed absent
  from the B-file by reading its own column header. Not yet loaded.
- **Nothing reads** `racer_period_course_stats`, `before_info_entries` or
  `winning_method` in the production feature path yet.
- 2001-2013 fan files use a 400-character record that has not been
  reverse-engineered; coverage starts at application period 2014-2.

---

## Part 2 — Methods

### The estimators

**Flat-bet return.** Betting ¥100 on every combination of a bet type
costs `100 × N` and returns the winning payout, so the average ratio
measures `(1-t) · mean(p/m)` from recorded payouts alone — no odds
needed. Used for per-lane, per-bet-type and per-condition comparisons.

> **It is not the takeout.** It equals `1-t` only if the market is
> calibrated. Reporting the six-lane figure (0.6603) as the takeout was
> an error made and corrected in one session; the real takeout, from
> `Σ1/odds` over closing odds, is **26.41%**, and the gap is the
> favourite-longshot bias.

**Return by popularity rank.** `race_payouts.popularity_rank` records
where the winning combination stood in the betting, so
`P(rank-k wins) × mean payout when it did / 100` is the exact return to
backing the k-th favourite every race — again without odds. This is the
fair way to compare bet types, since the flat portfolio penalises exotics
merely for containing more longshots.

**The calibration test.** In a pari-mutuel pool the return is
`(1-t) · p/m`. So **the win rate may vary across any cut; if pricing is
correct the return will not.** Drift in return across buckets is
mispricing. This one test carried the bet-type, day-of-week, field
strength and water-conditions results.

### The validation designs

**Walk-forward, scored twice.** Expanding train window, one-month test,
never a random split — races within a 節 share a motor, a boat and a
field, so shuffling puts near-duplicates on both sides. Every model
claim is scored on **log-loss and on return settled at the real 単勝
payout**, because those two disagreed on nearly everything: card features
improved log-loss by 31% over uniform and lost the entire gain to the
price.

**Temporal split-half persistence, with a control.** For "is X a real
attribute": strip the additive effects non-parametrically, split by time
(never at random), and ask whether the early residual predicts the late
one. Always carried alongside the **racer main effect** as a control,
because it is known real and calibrates what a genuine attribute scores
in this data (~0.78). Course aptitude scored 0.49; 相性 0.14; 調整力 0.14
on bad motors and 0.045 on good.

**Difference-in-differences.** For "does event E change behaviour":
compare each subject before and after their own E, against a control
group split at an equivalent point, so a calendar trend cannot masquerade
as an effect. Used for F持ち.

> **DiD is not the tradeable number.** It compares a racer to their own
> counterfactual self, which nobody can bet against. The F持ち effect was
> −0.0769 by DiD and **−0.0198** cross-sectionally, and only the second is
> what a bettor faces.

**Out-of-sample conditioning.** For "under what conditions does X fail":
estimate X on period 1, measure entirely on period 2, so regression to
the mean is already spent, and **fix the condition list in advance** so
the search cannot manufacture a result.

**Within-race z-scoring.** A quantity shared by all six boats cannot
change which one wins, so levels are converted to within-race
differences. This is why "add weather as a feature" does nothing on its
own: it acts through the lane prior or through an interaction with a
per-boat attribute, never as a main effect.

**Cross-validating a third-party source.** Before the Open API was
trusted for anything, the same races were read from it and from the
official page and compared field by field.

**Measuring the noise floor.** The same baseline, same folds, same data,
returned ROI 0.9231 and 0.9214 on two runs. **Differences below ~0.002
ROI are run-to-run variation** and are not read as effects.

### Traps found the hard way

Each of these produced a confident wrong answer before being caught.

| trap | how it bit | how it was caught |
|---|---|---|
| Relative metrics are **zero-sum** | "no within-節 drift" — but a change common to all six crews is invisible to finish position, and the caveat was missing | challenged, then re-tested with a fixed in-節 label |
| Flat portfolio ≠ takeout | 0.6603 reported as the takeout | it disagreed with the known ~25% |
| DiD ≠ tradeable | −0.0769 reported as money left on the table | cross-sectional check gave −0.0198 |
| Average-odds settlement | inflates any EV-selected backtest, because accuracy converts into *shorter* prices (winning payout ¥161 vs ¥192) | measured the conversion directly |
| Population arithmetic without the distribution | "1.3 M pairs, matchups unidentifiable" | 171,228 pairs actually have 15+ meetings |
| A check stricter than the column | 4 races "not summing to 1" | deviation was 2e-6, the `Numeric(8,6)` rounding bound |
| `status IS NULL` for a normal finish | normal finishes carry `'01'`..`'06'`; the aggregate emptied and every z-score became 0, reading as "the feature does nothing" | the script printed **coverage**, not just the result |
| An unlabelled weather reading | the mirror drops 「NR時点」/「HH:MM現在」; race 1's is the day's *last* reading | found on a real page timestamped six hours after race 1 |
| Venue as a confound | pairs and lanes that co-occur share a venue, and venue aptitude is real | every quantity recomputed within venue |

**The general lesson**: print coverage and denominators, not just
results. Two of the above were caught only because a run reported how
much data it had found.

### Standing rules this produced

1. Score every model claim on **log-loss and payouts**. They disagree.
2. Report the **tradeable** effect size, not the mechanism's size.
3. State the **noise floor** before reading a difference as an effect.
4. Fix condition lists **before** looking.
5. Prefer a source we hold 21 years of over a mirror that has 3 — check
   the archive before reaching for the API.
6. Record **inputs, not decisions**, so a policy change does not
   invalidate accumulated evidence.
