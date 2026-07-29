import unittest

from boat_prediction.integrity import check_duplicates, check_references


class CheckDuplicatesTest(unittest.TestCase):
    def test_no_duplicates_all_kept(self) -> None:
        records = [{"race_key": "a"}, {"race_key": "b"}]

        report = check_duplicates(records)

        self.assertEqual(report.kept, records)
        self.assertEqual(report.quarantined, [])

    def test_exact_duplicate_is_quarantined(self) -> None:
        records = [{"race_key": "a", "value": 1}, {"race_key": "a", "value": 1}]

        report = check_duplicates(records)

        self.assertEqual(len(report.kept), 1)
        self.assertEqual(len(report.quarantined), 1)
        self.assertEqual(report.quarantined[0][1].code, "E007")

    def test_business_key_duplicate_quarantined_even_if_other_fields_differ(self) -> None:
        records = [
            {"race_key": "2026-07-29-01-01", "note": "first"},
            {"race_key": "2026-07-29-01-01", "note": "second (different content)"},
        ]

        report = check_duplicates(records, business_key=lambda r: r["race_key"])

        self.assertEqual(len(report.kept), 1)
        self.assertEqual(report.kept[0]["note"], "first")
        self.assertEqual(len(report.quarantined), 1)
        self.assertEqual(report.quarantined[0][1].code, "E007")

    def test_repeated_run_is_repeatable(self) -> None:
        records = [
            {"race_key": "a"},
            {"race_key": "a"},
            {"race_key": "b"},
        ]

        first = check_duplicates(records, business_key=lambda r: r["race_key"])
        second = check_duplicates(records, business_key=lambda r: r["race_key"])

        self.assertEqual(first.kept, second.kept)
        self.assertEqual(
            [f.code for _, f in first.quarantined], [f.code for _, f in second.quarantined]
        )


class CheckReferencesTest(unittest.TestCase):
    def test_valid_reference_is_kept(self) -> None:
        records = [{"race_id": "r1"}, {"race_id": "r2"}]

        report = check_references(
            records, foreign_key=lambda r: r["race_id"], known_parent_keys={"r1", "r2"}
        )

        self.assertEqual(report.kept, records)
        self.assertEqual(report.quarantined, [])

    def test_unresolved_reference_is_quarantined_with_e004(self) -> None:
        records = [{"race_id": "r1"}, {"race_id": "missing"}]

        report = check_references(
            records, foreign_key=lambda r: r["race_id"], known_parent_keys={"r1"}
        )

        self.assertEqual(len(report.kept), 1)
        self.assertEqual(len(report.quarantined), 1)
        self.assertEqual(report.quarantined[0][1].code, "E004")

    def test_repeated_run_is_repeatable(self) -> None:
        records = [{"race_id": "r1"}, {"race_id": "missing"}]

        first = check_references(
            records, foreign_key=lambda r: r["race_id"], known_parent_keys={"r1"}
        )
        second = check_references(
            records, foreign_key=lambda r: r["race_id"], known_parent_keys={"r1"}
        )

        self.assertEqual(first.kept, second.kept)
        self.assertEqual(len(first.quarantined), len(second.quarantined))


if __name__ == "__main__":
    unittest.main()
