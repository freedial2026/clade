import unittest
from datetime import date

from boat_prediction.walk_forward import WalkForwardError, generate_monthly_folds


def _dates_across_months() -> list[date]:
    return [
        date(2026, 1, 5),
        date(2026, 1, 20),
        date(2026, 2, 3),
        date(2026, 2, 15),
        date(2026, 3, 1),
        date(2026, 3, 28),
    ]


class GenerateMonthlyFoldsTest(unittest.TestCase):
    def test_rejects_empty_dates(self) -> None:
        with self.assertRaises(WalkForwardError):
            generate_monthly_folds([])

    def test_rejects_too_few_distinct_months(self) -> None:
        with self.assertRaises(WalkForwardError):
            generate_monthly_folds([date(2026, 1, 1), date(2026, 1, 15)], min_train_months=1)

    def test_generates_one_fold_per_month_after_warmup(self) -> None:
        folds = generate_monthly_folds(_dates_across_months(), min_train_months=1)

        # 3 distinct months (Jan/Feb/Mar), 1 warm-up month -> 2 folds (Feb, Mar)
        self.assertEqual(len(folds), 2)
        self.assertEqual(folds[0].test_start, date(2026, 2, 1))
        self.assertEqual(folds[0].test_end, date(2026, 3, 1))
        self.assertEqual(folds[1].test_start, date(2026, 3, 1))
        self.assertEqual(folds[1].test_end, date(2026, 4, 1))

    def test_train_window_always_starts_at_the_earliest_date(self) -> None:
        folds = generate_monthly_folds(_dates_across_months(), min_train_months=1)

        for fold in folds:
            self.assertEqual(fold.train_start, date(2026, 1, 1))

    def test_train_end_equals_test_start_no_gap_no_overlap(self) -> None:
        folds = generate_monthly_folds(_dates_across_months(), min_train_months=1)

        for fold in folds:
            self.assertEqual(fold.train_end, fold.test_start)

    def test_train_indices_exclude_the_test_month_and_future(self) -> None:
        dates = _dates_across_months()
        fold = generate_monthly_folds(dates, min_train_months=1)[0]  # test = Feb

        train_idx = fold.train_indices(dates)

        self.assertEqual(train_idx, [0, 1])  # only the two January dates
        for i in train_idx:
            self.assertLess(dates[i], fold.test_start)

    def test_test_indices_only_include_dates_within_the_test_month(self) -> None:
        dates = _dates_across_months()
        fold = generate_monthly_folds(dates, min_train_months=1)[0]  # test = Feb

        test_idx = fold.test_indices(dates)

        self.assertEqual(test_idx, [2, 3])  # the two February dates

    def test_same_input_always_produces_identical_folds(self) -> None:
        dates = _dates_across_months()

        first = generate_monthly_folds(dates, min_train_months=1)
        second = generate_monthly_folds(dates, min_train_months=1)

        self.assertEqual(first, second)

    def test_fold_indices_are_sequential_starting_at_zero(self) -> None:
        folds = generate_monthly_folds(_dates_across_months(), min_train_months=1)
        self.assertEqual([f.fold_index for f in folds], [0, 1])

    def test_december_to_january_month_rollover(self) -> None:
        dates = [date(2025, 11, 1), date(2025, 12, 15), date(2026, 1, 10)]

        folds = generate_monthly_folds(dates, min_train_months=1)

        self.assertEqual(folds[0].test_start, date(2025, 12, 1))
        self.assertEqual(folds[0].test_end, date(2026, 1, 1))
        self.assertEqual(folds[1].test_start, date(2026, 1, 1))
        self.assertEqual(folds[1].test_end, date(2026, 2, 1))


if __name__ == "__main__":
    unittest.main()
