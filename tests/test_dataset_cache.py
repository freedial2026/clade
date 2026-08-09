"""Tests for `db.dataset_cache`.

Two things are being pinned here, and only one of them is the round
trip. The other is *invalidation*: a cache that returns rows built under
superseded rules produces a wrong number that looks entirely right, so
every fingerprint input gets a test that changes it and asserts the key
moves. `test_recipe_fingerprint_covers_every_tunable_constant` is the
guard against the failure mode no other test can see -- a new constant
added to `dataset.py` and never wired into the hash.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from sqlalchemy.orm import Session
from test_dataset import DatasetTestBase

from boat_prediction.db import dataset, dataset_cache
from boat_prediction.db.dataset_cache import (
    DatasetCacheError,
    build_dataset_cached,
    cache_key,
    data_fingerprint,
    load_dataset,
    recipe_fingerprint,
    save_dataset,
)
from boat_prediction.db.models import RaceEntry

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 10)


class DatasetCacheTestBase(DatasetTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)
        with Session(self.engine) as session:
            for day in range(1, 5):
                for race_number in (1, 2):
                    self._add_race(
                        session,
                        dt.date(2024, 1, day),
                        race_number,
                        winner_lanes=(race_number,),
                    )
            session.commit()

    def _events(self):
        seen: list[str] = []
        return seen, lambda event, _path: seen.append(event)


class RoundTripTest(DatasetCacheTestBase):
    def test_cached_dataset_is_identical_to_a_freshly_built_one(self) -> None:
        with Session(self.engine) as session:
            direct = dataset.build_dataset(session, start_date=START, end_date=END)
            cached = build_dataset_cached(
                session, start_date=START, end_date=END, cache_dir=self.cache_dir
            )
            reread = build_dataset_cached(
                session, start_date=START, end_date=END, cache_dir=self.cache_dir
            )

        self.assertTrue(len(direct))
        for other in (cached, reread):
            self.assertEqual(direct.X, other.X)
            self.assertEqual(direct.y, other.y)
            self.assertEqual(direct.dates, other.dates)
            self.assertEqual(direct.phases, other.phases)
            self.assertEqual(direct.feature_names, other.feature_names)
            self.assertEqual(direct.race_ids, other.race_ids)
            self.assertEqual(direct.y_second, other.y_second)
            self.assertEqual(str(direct.stats), str(other.stats))

    def test_float_values_round_trip_bit_exactly(self) -> None:
        """A cached run must reproduce an uncached run's log-loss, not
        approximate it, so float64 equality is the assertion -- not
        `assertAlmostEqual`."""
        with Session(self.engine) as session:
            direct = dataset.build_dataset(session, start_date=START, end_date=END)
            path = self.cache_dir / "rt.npz"
            save_dataset(path, direct, meta={})
            loaded, _ = load_dataset(path)
        for row_a, row_b in zip(direct.X, loaded.X):
            for a, b in zip(row_a, row_b):
                self.assertEqual(a.hex() if isinstance(a, float) else a, b.hex())

    def test_second_hit_does_not_rebuild(self) -> None:
        seen, on_event = self._events()
        with Session(self.engine) as session:
            build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
            with mock.patch.object(
                dataset_cache, "build_dataset", side_effect=AssertionError("rebuilt on a hit")
            ):
                build_dataset_cached(
                    session,
                    start_date=START,
                    end_date=END,
                    cache_dir=self.cache_dir,
                    on_event=on_event,
                )
        self.assertEqual(seen, ["miss", "hit"])

    def test_nullable_second_place_survives_the_sentinel(self) -> None:
        """`y_second` is None for a race with no single runner-up, and 0
        is the on-disk stand-in. A lane can never be 0, but the mapping
        still has to come back as None rather than as an integer."""
        with Session(self.engine) as session:
            self._add_race(
                session, dt.date(2024, 1, 5), 1, winner_lanes=(1,), dnf_lanes=(2, 3, 4, 5, 6)
            )
            session.commit()
            direct = dataset.build_dataset(session, start_date=START, end_date=dt.date(2024, 1, 5))
            path = self.cache_dir / "sentinel.npz"
            save_dataset(path, direct, meta={})
            loaded, _ = load_dataset(path)
        self.assertIn(None, direct.y_second)
        self.assertEqual(direct.y_second, loaded.y_second)
        self.assertNotIn(0, loaded.y_second)


class RecipeInvalidationTest(DatasetCacheTestBase):
    def test_changing_meeting_form_shrinkage_moves_the_key(self) -> None:
        """`tasks/CURRENT.md` plans a sweep of this constant. A cache
        keyed on dates alone would serve every point of that sweep the
        same pre-sweep features."""
        before = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        with mock.patch.object(dataset, "MEETING_FORM_SHRINKAGE_STARTS", 9.0):
            after = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        self.assertNotEqual(before, after)

    def test_changing_meeting_window_margin_moves_the_key(self) -> None:
        before = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        with mock.patch.object(dataset, "MEETING_WINDOW_MARGIN_DAYS", 20):
            after = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        self.assertNotEqual(before, after)

    def test_changing_a_per_course_constant_moves_the_key_even_when_unread(self) -> None:
        """Hashed unconditionally: over-invalidation costs one rebuild,
        under-invalidation costs a wrong number that looks right."""
        for name in ("COURSE_BASE_WIN_RATE", "COURSE_SHRINKAGE_STARTS"):
            with self.subTest(constant=name):
                before = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
                changed = {**getattr(dataset, name), 1: 0.9999}
                with mock.patch.object(dataset, name, changed):
                    after = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
                self.assertNotEqual(before, after)

    def test_changing_the_class_rank_encoding_moves_the_key(self) -> None:
        before = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        with mock.patch.object(
            dataset, "_CLASS_RANK", {"A1": 1.0, "A2": 2.0, "B1": 3.0, "B2": 4.0}
        ):
            after = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        self.assertNotEqual(before, after)

    def test_format_version_moves_the_key(self) -> None:
        before = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        with mock.patch.object(dataset_cache, "CACHE_FORMAT_VERSION", 999):
            after = recipe_fingerprint(include_before_info=False, include_racer_stats=False)
        self.assertNotEqual(before, after)

    def test_each_feature_flag_combination_has_its_own_key(self) -> None:
        """A card-only row and a 直前情報 row differ in width, but a model
        handed the wrong width does not always raise -- see
        `predict_daily.py`'s note on the same hazard."""
        seen = {
            recipe_fingerprint(include_before_info=bi, include_racer_stats=rs)
            for bi in (False, True)
            for rs in (False, True)
        }
        self.assertEqual(len(seen), 4)

    def test_recipe_fingerprint_covers_every_tunable_constant(self) -> None:
        """Guard against a constant being added to `dataset.py` and never
        wired into the hash -- the one failure mode that produces a stale
        cache with no other symptom.

        Adding a name here is a decision, not a formality: either it
        changes a feature value (hash it in `recipe_fingerprint` first)
        or it does not (then record why below).
        """
        structural = {
            # Consumed through `feature_columns()`, which is hashed.
            "FEATURE_NAMES",
            "BEFORE_INFO_FEATURE_NAMES",
            "RACER_STATS_FEATURE_NAMES",
            "GLOBAL_FEATURE_NAMES",
            "RACER_STATS_GLOBAL_NAMES",
            # Six lanes is the sport, not a tunable.
            "LANES",
        }
        hashed = {
            "COURSE_BASE_WIN_RATE",
            "COURSE_SHRINKAGE_STARTS",
            "MEETING_FORM_SHRINKAGE_STARTS",
            "MEETING_WINDOW_MARGIN_DAYS",
            "_CLASS_RANK",
        }
        found = {
            name
            for name, value in vars(dataset).items()
            if name.lstrip("_").isupper()
            and not name.startswith("_MEETING_CTE")
            and isinstance(value, (int, float, tuple, dict))
            and not isinstance(value, bool)
        }
        self.assertEqual(found, structural | hashed)


class DataInvalidationTest(DatasetCacheTestBase):
    def test_a_new_race_moves_the_data_fingerprint(self) -> None:
        with Session(self.engine) as session:
            before = data_fingerprint(session, start_date=START, end_date=END)
            self._add_race(session, dt.date(2024, 1, 6), 1)
            session.commit()
            after = data_fingerprint(session, start_date=START, end_date=END)
        self.assertNotEqual(before, after)

    def test_an_in_place_update_moves_the_data_fingerprint(self) -> None:
        """`COUNT(*)` cannot see this and neither can `MAX(id)` -- which
        is why the fingerprint reads `MAX(updated_at)`.
        `backfill_winning_method.py` already updates rows in place.

        `updated_at` is set explicitly rather than left to
        `TimestampMixin`'s `onupdate=func.now()`, because on SQLite that
        resolves to `CURRENT_TIMESTAMP` at one-second resolution and this
        test would race it. PostgreSQL's `now()` is microsecond, so
        production gets a distinct value from the mixin alone -- see the
        `dataset_cache` module docstring."""
        with Session(self.engine) as session:
            before = data_fingerprint(session, start_date=START, end_date=END)
            entry = session.query(RaceEntry).first()
            entry.listed_national_win_rate = 9.99
            entry.updated_at = entry.updated_at + dt.timedelta(minutes=1)
            session.commit()
            after = data_fingerprint(session, start_date=START, end_date=END)
        self.assertNotEqual(before, after)

    def test_a_race_just_before_the_window_moves_the_fingerprint(self) -> None:
        """`_MEETING_CTE` reaches `MEETING_WINDOW_MARGIN_DAYS` back, so a
        race outside the requested range can still change a
        `meeting_form_score` inside it. The fingerprint window has to
        match the query's, not the caller's."""
        with Session(self.engine) as session:
            before = data_fingerprint(session, start_date=START, end_date=END)
            self._add_race(
                session, START - dt.timedelta(days=dataset.MEETING_WINDOW_MARGIN_DAYS - 1), 1
            )
            session.commit()
            after = data_fingerprint(session, start_date=START, end_date=END)
        self.assertNotEqual(before, after)

    def test_a_race_far_outside_the_window_does_not(self) -> None:
        with Session(self.engine) as session:
            before = data_fingerprint(session, start_date=START, end_date=END)
            self._add_race(
                session, START - dt.timedelta(days=dataset.MEETING_WINDOW_MARGIN_DAYS + 5), 1
            )
            session.commit()
            after = data_fingerprint(session, start_date=START, end_date=END)
        self.assertEqual(before, after)

    def test_new_data_produces_a_miss_rather_than_a_stale_hit(self) -> None:
        seen, on_event = self._events()
        with Session(self.engine) as session:
            first = build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
            self._add_race(session, dt.date(2024, 1, 7), 1)
            session.commit()
            second = build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
        self.assertEqual(seen, ["miss", "miss"])
        self.assertEqual(len(second), len(first) + 1)


class CacheBehaviourTest(DatasetCacheTestBase):
    def test_date_range_and_flags_are_part_of_the_key(self) -> None:
        common = {"recipe": "r", "data": "d"}
        keys = {
            cache_key(
                start_date=START,
                end_date=END,
                include_before_info=False,
                include_racer_stats=False,
                **common,
            ),
            cache_key(
                start_date=START,
                end_date=END - dt.timedelta(days=1),
                include_before_info=False,
                include_racer_stats=False,
                **common,
            ),
            cache_key(
                start_date=START + dt.timedelta(days=1),
                end_date=END,
                include_before_info=False,
                include_racer_stats=False,
                **common,
            ),
            cache_key(
                start_date=START,
                end_date=END,
                include_before_info=True,
                include_racer_stats=False,
                **common,
            ),
            cache_key(
                start_date=START,
                end_date=END,
                include_before_info=False,
                include_racer_stats=True,
                **common,
            ),
        }
        self.assertEqual(len(keys), 5)

    def test_refresh_rebuilds_over_an_existing_entry(self) -> None:
        seen, on_event = self._events()
        with Session(self.engine) as session:
            build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
            build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                refresh=True,
                on_event=on_event,
            )
        self.assertEqual(seen, ["miss", "refresh"])

    def test_cache_dir_none_never_touches_the_disk(self) -> None:
        with Session(self.engine) as session:
            data = build_dataset_cached(session, start_date=START, end_date=END, cache_dir=None)
        self.assertTrue(len(data))
        self.assertEqual(list(self.cache_dir.iterdir()), [])

    def test_a_corrupt_entry_falls_back_to_a_rebuild(self) -> None:
        """An unreadable entry is a miss, not a crash: rebuilding is
        always correct, so a truncated file must not take an evaluation
        run down with it."""
        seen, on_event = self._events()
        with Session(self.engine) as session:
            build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
            written = next(self.cache_dir.glob("dataset-*.npz"))
            written.write_bytes(b"not an npz")
            data = build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
        self.assertEqual(seen, ["miss", "unreadable"])
        self.assertTrue(len(data))

    def test_load_dataset_raises_on_an_unreadable_file(self) -> None:
        bad = self.cache_dir / "bad.npz"
        bad.write_bytes(b"nope")
        with self.assertRaises(DatasetCacheError):
            load_dataset(bad)

    def test_the_write_is_atomic(self) -> None:
        """A half-written `.npz` read back as a hit is indistinguishable
        from a real entry, so the file appears under its final name only
        once complete and no temporary is left behind."""
        with Session(self.engine) as session:
            build_dataset_cached(session, start_date=START, end_date=END, cache_dir=self.cache_dir)
        self.assertEqual(list(self.cache_dir.glob("*.tmp")), [])
        self.assertEqual(len(list(self.cache_dir.glob("dataset-*.npz"))), 1)

    def test_meta_records_what_the_entry_was_built_from(self) -> None:
        with Session(self.engine) as session:
            build_dataset_cached(session, start_date=START, end_date=END, cache_dir=self.cache_dir)
            written = next(self.cache_dir.glob("dataset-*.npz"))
            _data, meta = load_dataset(written)
        self.assertEqual(meta["start_date"], START.isoformat())
        self.assertEqual(meta["end_date"], END.isoformat())
        self.assertFalse(meta["include_before_info"])
        self.assertEqual(
            meta["recipe_fingerprint"],
            recipe_fingerprint(include_before_info=False, include_racer_stats=False),
        )
        json.dumps(meta)  # must stay plain JSON for inspection by hand

    def test_prune_keeps_the_newest_and_removes_the_rest(self) -> None:
        for i in range(5):
            entry = self.cache_dir / f"dataset-{i:032d}.npz"
            entry.write_bytes(b"x")
            os.utime(entry, (1_700_000_000 + i, 1_700_000_000 + i))
        removed = dataset_cache.prune(self.cache_dir, keep=2)
        self.assertEqual(
            sorted(p.name for p in removed),
            [f"dataset-{i:032d}.npz" for i in range(3)],
        )
        self.assertEqual(
            sorted(p.name for p in self.cache_dir.glob("*.npz")),
            [f"dataset-{i:032d}.npz" for i in (3, 4)],
        )

    def test_prune_ignores_files_it_did_not_write(self) -> None:
        """`--cache-dir` pointed at a populated directory by mistake must
        not be able to delete anything but this module's own entries."""
        bystander = self.cache_dir / "important.npz"
        bystander.write_bytes(b"not ours")
        (self.cache_dir / "dataset-a.npz").write_bytes(b"ours")
        dataset_cache.prune(self.cache_dir, keep=0)
        self.assertTrue(bystander.exists())
        self.assertEqual(list(self.cache_dir.glob("dataset-*.npz")), [])

    def test_a_write_evicts_beyond_the_cap(self) -> None:
        for i in range(dataset_cache.MAX_CACHE_ENTRIES):
            filler = self.cache_dir / f"dataset-{i:032d}.npz"
            filler.write_bytes(b"x")
            os.utime(filler, (1_700_000_000 + i, 1_700_000_000 + i))
        seen, on_event = self._events()
        with Session(self.engine) as session:
            build_dataset_cached(
                session,
                start_date=START,
                end_date=END,
                cache_dir=self.cache_dir,
                on_event=on_event,
            )
        self.assertEqual(seen, ["miss", "evicted"])
        self.assertEqual(
            len(list(self.cache_dir.glob("dataset-*.npz"))), dataset_cache.MAX_CACHE_ENTRIES
        )

    def test_end_before_start_is_rejected_before_any_disk_access(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            build_dataset_cached(session, start_date=END, end_date=START, cache_dir=self.cache_dir)
        self.assertEqual(list(self.cache_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
