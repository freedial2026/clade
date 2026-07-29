import unittest

from boat_prediction.validation import (
    RACE_ENTRY_SCHEMA,
    FieldSpec,
    validate_record,
    validate_records,
)

SCHEMA = [
    FieldSpec(name="lane_number", field_type=int, required=True, min_value=1, max_value=6),
    FieldSpec(
        name="status",
        field_type=str,
        required=True,
        enum=frozenset({"scheduled", "final", "cancelled"}),
    ),
    FieldSpec(name="note", field_type=str, required=False),
]


class ValidateRecordTest(unittest.TestCase):
    def test_valid_record_passes_through_unchanged(self) -> None:
        record = {"lane_number": 3, "status": "final"}

        result = validate_record(record, SCHEMA)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.record, record)
        self.assertIs(result.record, record)

    def test_missing_required_field_reports_missing_code(self) -> None:
        result = validate_record({"lane_number": 3}, SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual([f.field for f in result.failures], ["status"])
        self.assertEqual(result.failures[0].code, "E003")

    def test_optional_field_omitted_is_still_valid(self) -> None:
        result = validate_record({"lane_number": 3, "status": "final"}, SCHEMA)
        self.assertTrue(result.is_valid)

    def test_wrong_type_reports_type_code(self) -> None:
        result = validate_record({"lane_number": "3", "status": "final"}, SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E900")

    def test_enum_violation_reports_enum_code(self) -> None:
        result = validate_record({"lane_number": 3, "status": "unknown"}, SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E901")

    def test_below_minimum_reports_range_code(self) -> None:
        result = validate_record({"lane_number": 0, "status": "final"}, SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E902")

    def test_above_maximum_reports_range_code(self) -> None:
        result = validate_record({"lane_number": 7, "status": "final"}, SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E902")


class ValidateRecordsTest(unittest.TestCase):
    def test_reports_counts_and_reason_code_distribution(self) -> None:
        records = [
            {"lane_number": 1, "status": "final"},
            {"lane_number": 2, "status": "final"},
            {"lane_number": 9, "status": "final"},  # E902
            {"lane_number": 3},  # E003 (missing status)
        ]

        report = validate_records(records, SCHEMA)

        self.assertEqual(report.total, 4)
        self.assertEqual(len(report.valid), 2)
        self.assertEqual(len(report.rejected), 2)
        self.assertEqual(report.reason_code_counts(), {"E902": 1, "E003": 1})

    def test_valid_records_are_unaffected_by_rejections(self) -> None:
        good = {"lane_number": 1, "status": "final"}
        bad = {"lane_number": 99, "status": "final"}

        report = validate_records([good, bad], SCHEMA)

        self.assertEqual(report.valid, [good])


class RaceEntrySchemaTest(unittest.TestCase):
    def test_valid_race_entry_passes(self) -> None:
        result = validate_record({"lane_number": 4, "registration_number": 4321}, RACE_ENTRY_SCHEMA)
        self.assertTrue(result.is_valid)

    def test_lane_number_out_of_range_uses_e005(self) -> None:
        result = validate_record({"lane_number": 7, "registration_number": 4321}, RACE_ENTRY_SCHEMA)

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E005")

    def test_invalid_registration_number_uses_e006(self) -> None:
        result = validate_record(
            {"lane_number": 1, "registration_number": "not-a-number"}, RACE_ENTRY_SCHEMA
        )

        self.assertFalse(result.is_valid)
        self.assertEqual(result.failures[0].code, "E006")


if __name__ == "__main__":
    unittest.main()
