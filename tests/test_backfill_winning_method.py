"""Targeted backfill of `race_results.winning_method`.

A repair script's tests are mostly about what it must *not* do. This one
runs against a database holding 1.15 M results, so the assertions below
are the guardrails: dry-run writes nothing, only the one column changes,
an existing value is never overwritten, and a race the file does not name
a technique for stays NULL rather than acquiring a guess.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import loader
from boat_prediction.db.backfill_winning_method import backfill_file
from boat_prediction.db.models import Base, Race, RaceResult

RACE_DATE = dt.date(2024, 8, 1)

K_TEXT = "\n".join(
    [
        "01KBGN",
        "  1R   予選                    H1800m  晴　  風  南西　 2m  波　  2cm",
        "  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ まくり差し",
        "-" * 60,
        "  01  1 4444 試　験　　太　郎 11   22  6.71   1    0.17     1.59.9",
        "  2R   予選                    H1800m  晴　  風  南西　 2m  波　  2cm",
        "  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ",
        "-" * 60,
        "  01  1 4445 試　験　　次　郎 11   22  6.71   1    0.17     1.59.9",
        "01KEND",
    ]
)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class _Path(Path):
    """A path whose name parses as a K-file date but whose contents are
    supplied directly, so no LZH fixture is needed."""


class BackfillWinningMethodTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "01")
        self.results = {}
        for number in (1, 2):
            race = Race(
                venue_id=venue.id,
                race_date=RACE_DATE,
                race_number=number,
                status="finished",
            )
            self.session.add(race)
            self.session.flush()
            result = RaceResult(race_id=race.id, available_at=dt.datetime.now(dt.UTC))
            self.session.add(result)
            self.session.flush()
            self.results[number] = result
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "k240801.lzh"

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self._tmp.cleanup()

    def _run(self, *, apply: bool):
        import boat_prediction.db.backfill_winning_method as module

        original = module.extract_k_file_text
        module.extract_k_file_text = lambda _path: K_TEXT
        try:
            return backfill_file(self.session, self.path, apply=apply)
        finally:
            module.extract_k_file_text = original

    def test_dry_run_writes_nothing(self) -> None:
        stats = self._run(apply=False)

        self.assertEqual(stats.updated, 1)
        self.assertIsNone(
            self.session.scalar(
                select(RaceResult.winning_method).where(
                    RaceResult.id == self.results[1].id
                )
            )
        )

    def test_apply_sets_only_the_named_race(self) -> None:
        self._run(apply=True)
        self.session.flush()

        self.assertEqual(
            self.session.scalar(
                select(RaceResult.winning_method).where(RaceResult.id == self.results[1].id)
            ),
            "まくり差し",
        )
        # race 2's header carries no technique, so it must stay NULL
        self.assertIsNone(
            self.session.scalar(
                select(RaceResult.winning_method).where(RaceResult.id == self.results[2].id)
            )
        )

    def test_a_file_without_a_technique_is_counted_not_guessed(self) -> None:
        stats = self._run(apply=False)

        self.assertEqual(stats.no_method_in_file, 1)

    def test_an_existing_value_is_never_overwritten(self) -> None:
        self.results[1].winning_method = "逃げ"
        self.session.flush()

        stats = self._run(apply=True)
        self.session.flush()

        self.assertEqual(stats.already_set, 1)
        self.assertEqual(stats.updated, 0)
        self.assertEqual(
            self.session.scalar(
                select(RaceResult.winning_method).where(RaceResult.id == self.results[1].id)
            ),
            "逃げ",
        )

    def test_a_race_absent_from_the_database_is_counted_not_created(self) -> None:
        self.session.delete(self.results[1])
        self.session.delete(self.session.scalar(select(Race).where(Race.race_number == 1)))
        self.session.flush()

        stats = self._run(apply=True)

        self.assertEqual(stats.race_not_found, 1)
        self.assertEqual(len(list(self.session.scalars(select(Race)))), 1)

    def test_no_other_column_is_touched(self) -> None:
        before = self.session.scalar(
            select(RaceResult.available_at).where(RaceResult.id == self.results[1].id)
        )

        self._run(apply=True)
        self.session.flush()

        after = self.session.scalar(
            select(RaceResult.available_at).where(RaceResult.id == self.results[1].id)
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
