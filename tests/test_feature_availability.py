import unittest
from datetime import datetime

from boat_prediction.feature_availability import (
    FEATURE_SET_V1_NAMES,
    FeatureAvailabilityError,
    FeatureLineage,
    check_feature_availability,
    check_feature_set_availability,
)

from _helpers import make_temporal_record
from _helpers import utc as _utc


def _lineage(feature_name: str, available_at: datetime, *, is_result_derived: bool = False) -> FeatureLineage:
    return FeatureLineage(
        feature_name=feature_name,
        source="listed_stats",
        temporal=make_temporal_record(available_at),
        is_result_derived=is_result_derived,
    )


class CheckFeatureAvailabilityTest(unittest.TestCase):
    def test_feature_available_before_prediction_passes(self) -> None:
        lineage = _lineage("listed_national_win_rate", _utc(2026, 1, 1, 0, 0))
        check_feature_availability(lineage, _utc(2026, 1, 2, 0, 0))  # no raise

    def test_feature_not_yet_available_raises(self) -> None:
        lineage = _lineage("listed_national_win_rate", _utc(2026, 1, 5, 0, 0))
        with self.assertRaises(FeatureAvailabilityError):
            check_feature_availability(lineage, _utc(2026, 1, 1, 0, 0))

    def test_result_derived_feature_without_finalization_raises(self) -> None:
        lineage = _lineage("finish_position", _utc(2026, 1, 1, 0, 0), is_result_derived=True)
        with self.assertRaises(FeatureAvailabilityError):
            check_feature_availability(lineage, _utc(2026, 1, 2, 0, 0))

    def test_result_derived_feature_finalized_before_prediction_passes(self) -> None:
        lineage = _lineage("finish_position", _utc(2026, 1, 1, 0, 0), is_result_derived=True)
        check_feature_availability(
            lineage, _utc(2026, 1, 2, 0, 0), race_finalized_at=_utc(2026, 1, 1, 12, 0)
        )  # no raise

    def test_result_derived_feature_finalized_after_prediction_raises(self) -> None:
        lineage = _lineage("finish_position", _utc(2026, 1, 1, 0, 0), is_result_derived=True)
        with self.assertRaises(FeatureAvailabilityError):
            check_feature_availability(
                lineage, _utc(2026, 1, 1, 6, 0), race_finalized_at=_utc(2026, 1, 1, 12, 0)
            )


class CheckFeatureSetAvailabilityTest(unittest.TestCase):
    def test_clean_feature_set_v1_style_features_all_pass(self) -> None:
        prediction_at = _utc(2026, 1, 2, 0, 0)
        lineages = [_lineage(name, _utc(2026, 1, 1, 0, 0)) for name in sorted(FEATURE_SET_V1_NAMES)]

        check_feature_set_availability(lineages, prediction_at)  # no raise

    def test_leakage_fixture_with_future_available_at_fails_the_gate(self) -> None:
        prediction_at = _utc(2026, 1, 2, 0, 0)
        lineages = [
            _lineage("listed_national_win_rate", _utc(2026, 1, 1, 0, 0)),
            _lineage("leaked_future_odds", _utc(2026, 1, 3, 0, 0)),  # not yet available
        ]

        with self.assertRaises(FeatureAvailabilityError):
            check_feature_set_availability(lineages, prediction_at)

    def test_leakage_fixture_with_unfinalized_result_feature_fails_the_gate(self) -> None:
        prediction_at = _utc(2026, 1, 1, 6, 0)
        lineages = [
            _lineage("listed_national_win_rate", _utc(2026, 1, 1, 0, 0)),
            _lineage("finish_position", _utc(2026, 1, 1, 0, 0), is_result_derived=True),
        ]

        with self.assertRaises(FeatureAvailabilityError):
            check_feature_set_availability(lineages, prediction_at)  # race not finalized yet


if __name__ == "__main__":
    unittest.main()
