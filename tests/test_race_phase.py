"""Tests for `race_phase`.

Every label used here was observed in the 2005-2026 K-file archive; the
counts in comments are that archive's occurrence counts, so a case with
a large count is a real risk rather than a hypothetical one.
"""

import unittest

from boat_prediction.race_phase import (
    RACE_PHASE_FINAL,
    RACE_PHASE_GENERAL,
    RACE_PHASE_QUALIFIER,
    RACE_PHASE_SELECTION,
    RACE_PHASE_SEMIFINAL,
    RACE_PHASE_TRIAL,
    RACE_PHASE_UNKNOWN,
    classify_race_phase,
    is_final,
    is_semifinal,
    is_standing_seeded,
    normalize_race_class,
)


class NormalizeRaceClassTest(unittest.TestCase):
    def test_removes_ascii_and_ideographic_padding(self) -> None:
        self.assertEqual(normalize_race_class("予 選"), "予選")
        self.assertEqual(normalize_race_class("一　　般　　"), "一般")

    def test_removes_the_entry_modifier(self) -> None:
        self.assertEqual(normalize_race_class("予選　進入固定"), "予選")

    def test_treats_none_and_blank_as_empty(self) -> None:
        self.assertEqual(normalize_race_class(None), "")
        self.assertEqual(normalize_race_class("　 "), "")


class ClassifyRacePhaseTest(unittest.TestCase):
    def test_classifies_the_plain_labels(self) -> None:
        cases = {
            "予選": RACE_PHASE_TRIAL,  # 484,744
            "準優勝戦": RACE_PHASE_SEMIFINAL,  # 41,365
            "優勝戦": RACE_PHASE_FINAL,  # 16,345
            "選抜戦": RACE_PHASE_SELECTION,  # 21,721
            "特選": RACE_PHASE_SELECTION,  # 19,275
            "一般戦": RACE_PHASE_GENERAL,  # 103,472
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), expected)

    def test_reads_a_final_named_after_its_event(self) -> None:
        for label in ("王将位決定戦", "王座決定戦", "海の王者決定戦", "ファイナル", "決勝戦"):
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), RACE_PHASE_FINAL)

    def test_reads_a_final_truncated_by_the_fixed_width_field(self) -> None:
        # The class field cuts at 12 half-width columns, leaving a bare 優.
        for label in ("サントリー優", "スピード王優", "住信ＳＢＩ優", "県内選手権優"):
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), RACE_PHASE_FINAL)

    def test_does_not_read_a_semifinal_as_a_final(self) -> None:
        # 準優勝戦 contains 優勝 and 準優 ends in 優 -- the ordering trap.
        for label in ("準優勝戦", "準優勝", "準優", "カニ坊準優", "おはよう準優"):
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), RACE_PHASE_SEMIFINAL)

    def test_does_not_read_a_qualifier_as_the_round_it_leads_into(self) -> None:
        for label in ("準優進出戦", "準優勝進出戦", "優勝戦進出決", "準々決勝戦", "準々優勝戦"):
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), RACE_PHASE_QUALIFIER)

    def test_does_not_read_順位決定戦_as_a_final(self) -> None:
        # 475 races, none in the final slot: the reason 決定 alone is not
        # enough. 代表決定 (a qualifier for another event) is the same.
        self.assertEqual(classify_race_phase("順位決定戦"), RACE_PHASE_UNKNOWN)
        self.assertEqual(classify_race_phase("Ａ組順位決定"), RACE_PHASE_UNKNOWN)
        self.assertEqual(classify_race_phase("男子代表決定"), RACE_PHASE_UNKNOWN)

    def test_leaves_branded_race_names_unknown(self) -> None:
        # Venue marketing names carry no series role; guessing one would
        # put an ordinary race into a standing-seeded group.
        for label in ("ドリーム戦", "ランチタイム", "モーニング一", "朝得ガァ〜コ"):
            with self.subTest(label=label):
                self.assertEqual(classify_race_phase(label), RACE_PHASE_UNKNOWN)

    def test_prefers_予選_over_the_selection_suffix(self) -> None:
        # "予選特選" is a heat, not a graded selection race.
        self.assertEqual(classify_race_phase("予選特選"), RACE_PHASE_TRIAL)
        self.assertEqual(classify_race_phase("予選特賞"), RACE_PHASE_TRIAL)

    def test_ignores_the_entry_modifier(self) -> None:
        self.assertEqual(classify_race_phase("予選　進入固定"), RACE_PHASE_TRIAL)
        self.assertEqual(classify_race_phase("一般戦進入固定"), RACE_PHASE_GENERAL)

    def test_returns_unknown_for_a_missing_label(self) -> None:
        self.assertEqual(classify_race_phase(None), RACE_PHASE_UNKNOWN)
        self.assertEqual(classify_race_phase(""), RACE_PHASE_UNKNOWN)


class PredicateTest(unittest.TestCase):
    def test_is_final_and_is_semifinal_do_not_overlap(self) -> None:
        self.assertTrue(is_final("優勝戦"))
        self.assertFalse(is_final("準優勝戦"))
        self.assertTrue(is_semifinal("準優勝戦"))
        self.assertFalse(is_semifinal("優勝戦"))
        self.assertFalse(is_semifinal("準優進出戦"))

    def test_is_standing_seeded_covers_both_seeded_rounds_only(self) -> None:
        self.assertTrue(is_standing_seeded("優勝戦"))
        self.assertTrue(is_standing_seeded("準優勝戦"))
        self.assertFalse(is_standing_seeded("予選"))
        self.assertFalse(is_standing_seeded("一般戦"))
        self.assertFalse(is_standing_seeded("順位決定戦"))


if __name__ == "__main__":
    unittest.main()
