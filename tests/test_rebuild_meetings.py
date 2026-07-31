"""Tests for `db.rebuild_meetings`.

Fixtures write the *old* (arithmetic) grouping directly, so each test
starts from data shaped the way the .21 database already is, and asserts
what the rebuild turns it into.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from boat_prediction.db.models import Base, Race, RaceMeeting, Venue
from boat_prediction.db.rebuild_meetings import (
    BACKUP_TABLE,
    RebuildMeetingsError,
    rebuild_meetings,
)

RACE_STATUS = "scheduled"


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class RebuildMeetingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed(self, session: Session, days: list[tuple[dt.date, int]]) -> Venue:
        """Create one venue's days, keyed the old arithmetic way."""
        venue = Venue(code="24", name="大村")
        session.add(venue)
        session.flush()
        meetings: dict[dt.date, RaceMeeting] = {}
        for race_date, series_day in days:
            start = race_date - dt.timedelta(days=series_day - 1)
            meeting = meetings.get(start)
            if meeting is None:
                meeting = RaceMeeting(venue_id=venue.id, meeting_start_date=start)
                session.add(meeting)
                session.flush()
                meetings[start] = meeting
            session.add(
                Race(
                    venue_id=venue.id,
                    meeting_id=meeting.id,
                    race_date=race_date,
                    race_number=1,
                    series_day=series_day,
                    status=RACE_STATUS,
                )
            )
        session.flush()
        return venue

    # Venue 24, 2005-09: 第5日 ran on three consecutive dates, so the old
    # key produced three meetings for one 節.
    POSTPONED = [
        (dt.date(2005, 9, 3), 3),
        (dt.date(2005, 9, 4), 4),
        (dt.date(2005, 9, 5), 5),
        (dt.date(2005, 9, 6), 5),
        (dt.date(2005, 9, 7), 5),
    ]

    def test_dry_run_reports_the_split_and_writes_nothing(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, self.POSTPONED)
            session.commit()

            stats = rebuild_meetings(session)

            self.assertEqual(stats.meetings_before, 3)
            self.assertEqual(stats.meetings_after, 1)
            self.assertEqual(stats.series_split_before, 1)
            self.assertEqual(stats.races_repointed, 2)
            session.rollback()
            self.assertEqual(session.query(RaceMeeting).count(), 3)
            self.assertFalse(inspect(self.engine).has_table(BACKUP_TABLE))

    def test_apply_merges_the_split_meetings(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, self.POSTPONED)
            session.commit()

            rebuild_meetings(session, apply=True)
            session.commit()

            self.assertEqual(session.query(RaceMeeting).count(), 1)
            meeting = session.scalar(select(RaceMeeting))
            self.assertEqual(meeting.meeting_start_date, dt.date(2005, 9, 1))
            self.assertEqual(
                {race.meeting_id for race in session.scalars(select(Race))}, {meeting.id}
            )

    def test_apply_leaves_separate_series_separate(self) -> None:
        with Session(self.engine) as session:
            self._seed(
                session,
                [
                    (dt.date(2016, 3, 15), 4),
                    (dt.date(2016, 3, 16), 5),
                    (dt.date(2016, 3, 17), 1),
                    (dt.date(2016, 3, 18), 2),
                ],
            )
            session.commit()

            stats = rebuild_meetings(session, apply=True)
            session.commit()

            self.assertEqual(stats.series_split_before, 0)
            self.assertEqual(session.query(RaceMeeting).count(), 2)

    def test_apply_writes_a_restorable_backup(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, self.POSTPONED)
            session.commit()
            # Compared as hex: SQLite's CREATE TABLE AS keeps no type
            # affinity, so UUIDs read back as strings. PostgreSQL, where
            # this actually runs, preserves the uuid type.
            def _hex(value) -> str | None:
                return None if value is None else str(value).replace("-", "")

            before = {
                _hex(race_id): _hex(meeting_id)
                for race_id, meeting_id in session.execute(select(Race.id, Race.meeting_id))
            }

            rebuild_meetings(session, apply=True)
            session.commit()

            backed_up = {
                _hex(race_id): _hex(meeting_id)
                for race_id, meeting_id in session.execute(
                    text(f"SELECT id, meeting_id FROM {BACKUP_TABLE}")
                )
            }
            self.assertEqual(backed_up, before)

    def test_apply_refuses_to_overwrite_an_existing_backup(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, self.POSTPONED)
            session.commit()
            rebuild_meetings(session, apply=True)
            session.commit()

            with self.assertRaises(RebuildMeetingsError):
                rebuild_meetings(session, apply=True)

    def test_a_title_from_a_merged_duplicate_is_kept(self) -> None:
        # 第1日 often has no title while later days do, and the day-1
        # meeting is the one that survives.
        with Session(self.engine) as session:
            venue = self._seed(session, self.POSTPONED)
            session.commit()
            later = session.scalars(
                select(RaceMeeting)
                .where(RaceMeeting.venue_id == venue.id)
                .order_by(RaceMeeting.meeting_start_date.desc())
            ).first()
            later.meeting_title = "西スポ杯"
            session.commit()

            rebuild_meetings(session, apply=True)
            session.commit()

            self.assertEqual(session.scalar(select(RaceMeeting)).meeting_title, "西スポ杯")

    def test_races_with_no_meeting_do_not_inflate_the_prediction(self) -> None:
        # A K-file load with no B-file card leaves meeting_id NULL. Those
        # days group like any other but gain no meeting, so the dry run
        # must not count them or it predicts more rows than --apply leaves.
        with Session(self.engine) as session:
            venue = self._seed(session, self.POSTPONED)
            session.add(
                Race(
                    venue_id=venue.id,
                    meeting_id=None,
                    race_date=dt.date(2005, 10, 1),
                    race_number=1,
                    series_day=1,
                    status=RACE_STATUS,
                )
            )
            session.commit()

            predicted = rebuild_meetings(session)
            session.rollback()
            rebuild_meetings(session, apply=True)
            session.commit()

            self.assertEqual(predicted.meetings_after, session.query(RaceMeeting).count())
            self.assertEqual(predicted.meetings_deleted, 2)

    def test_is_idempotent(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, self.POSTPONED)
            session.commit()
            rebuild_meetings(session, apply=True)
            session.commit()

            second = rebuild_meetings(session)

            self.assertEqual(second.series_split_before, 0)
            self.assertEqual(second.races_repointed, 0)
            self.assertEqual(second.meetings_before, second.meetings_after)


if __name__ == "__main__":
    unittest.main()
