"""Combination-bet marginals derived from a first-place distribution
(`combination_model`).

The risk guarded here is silent incoherence. Every pool's probability is
a sum over the same 30-class exacta joint, so a bug in one marginal does
not raise -- it produces a plausible number that disagrees with the P1
distribution it supposedly came from, and an EV computed against it would
be wrong in a way no threshold check would catch. These tests assert the
identities that have to hold rather than fixed expected values.
"""

from __future__ import annotations

import unittest

from boat_prediction.baseline import LANES
from boat_prediction.combination_model import (
    CombinationModelError,
    PlackettLuceSecondPlace,
    quinella_probabilities,
    top2_probabilities,
)
from boat_prediction.exacta import (
    ALL_COMBINATIONS,
    construct_exacta_probabilities,
    decode_combination,
)
from boat_prediction.second_place import ConditionalSecondPlaceModel

# Deliberately lopsided -- a near-uniform distribution would let an
# averaging bug pass every identity below.
FIRST_PLACE = {1: 0.55, 2: 0.18, 3: 0.12, 4: 0.08, 5: 0.05, 6: 0.02}


def _joint(first_place=None):
    probs = first_place or FIRST_PLACE
    return construct_exacta_probabilities(probs, PlackettLuceSecondPlace(probs))


class PlackettLuceSecondPlaceTest(unittest.TestCase):
    def test_conditional_is_the_renormalized_remainder(self) -> None:
        conditional = PlackettLuceSecondPlace(FIRST_PLACE).predict(1)
        remaining = 1.0 - FIRST_PLACE[1]
        for lane in (2, 3, 4, 5, 6):
            self.assertAlmostEqual(conditional[lane], FIRST_PLACE[lane] / remaining, places=9)

    def test_first_place_lane_cannot_repeat(self) -> None:
        for first in LANES:
            self.assertEqual(PlackettLuceSecondPlace(FIRST_PLACE).predict(first)[first], 0.0)

    def test_conditional_sums_to_one_for_every_first_place(self) -> None:
        model = PlackettLuceSecondPlace(FIRST_PLACE)
        for first in LANES:
            self.assertAlmostEqual(sum(model.predict(first).values()), 1.0, places=9)

    def test_relative_order_of_the_others_is_preserved(self) -> None:
        """The whole content of the Luce assumption: removing the winner
        rescales the rest, it does not reorder them."""
        conditional = PlackettLuceSecondPlace(FIRST_PLACE).predict(1)
        others = [lane for lane in LANES if lane != 1]
        by_conditional = sorted(others, key=lambda lane: conditional[lane], reverse=True)
        by_prior = sorted(others, key=lambda lane: FIRST_PLACE[lane], reverse=True)
        self.assertEqual(by_conditional, by_prior)

    def test_near_certain_winner_falls_back_to_uniform_over_the_others(self) -> None:
        """`1 - p_i` too small to divide by must not produce a conditional
        made of floating-point noise."""
        certain = {1: 1.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0}
        conditional = PlackettLuceSecondPlace(certain).predict(1)
        for lane in (2, 3, 4, 5, 6):
            self.assertAlmostEqual(conditional[lane], 0.2, places=9)

    def test_rejects_a_distribution_that_does_not_sum_to_one(self) -> None:
        with self.assertRaises(CombinationModelError):
            PlackettLuceSecondPlace({1: 0.5, 2: 0.2, 3: 0.1, 4: 0.1, 5: 0.1, 6: 0.5})

    def test_rejects_missing_lanes(self) -> None:
        with self.assertRaises(CombinationModelError):
            PlackettLuceSecondPlace({1: 0.6, 2: 0.4})

    def test_rejects_negative_probability(self) -> None:
        with self.assertRaises(CombinationModelError):
            PlackettLuceSecondPlace({1: 1.1, 2: -0.1, 3: 0.0, 4: 0.0, 5: 0.0, 6: 0.0})

    def test_rejects_lane_out_of_range(self) -> None:
        with self.assertRaises(CombinationModelError):
            PlackettLuceSecondPlace(FIRST_PLACE).predict(7)

    def test_plugs_into_the_existing_exacta_constructor(self) -> None:
        """The coherence check lives in `exacta`, so this conditional has
        to satisfy it rather than carry a parallel one."""
        joint = _joint()
        self.assertEqual(set(joint), set(ALL_COMBINATIONS))
        self.assertAlmostEqual(sum(joint.values()), 1.0, places=9)


class Top2ProbabilitiesTest(unittest.TestCase):
    def test_sums_to_two_because_two_boats_collect(self) -> None:
        self.assertAlmostEqual(sum(top2_probabilities(_joint()).values()), 2.0, places=9)

    def test_equals_first_place_plus_second_place_marginal(self) -> None:
        joint = _joint()
        top2 = top2_probabilities(joint)
        for lane in LANES:
            second_marginal = sum(
                p for code, p in joint.items() if decode_combination(code)[1] == lane
            )
            self.assertAlmostEqual(top2[lane], FIRST_PLACE[lane] + second_marginal, places=9)

    def test_never_below_the_win_probability(self) -> None:
        """Placing is implied by winning, so 複勝 can only be the larger
        number. A sign or ordering slip in the sum shows up here."""
        top2 = top2_probabilities(_joint())
        for lane in LANES:
            self.assertGreaterEqual(top2[lane] + 1e-12, FIRST_PLACE[lane])

    def test_rejects_a_joint_missing_combinations(self) -> None:
        joint = _joint()
        del joint[12]
        with self.assertRaises(CombinationModelError):
            top2_probabilities(joint)


class QuinellaProbabilitiesTest(unittest.TestCase):
    def test_has_fifteen_unordered_pairs_summing_to_one(self) -> None:
        pairs = quinella_probabilities(_joint())
        self.assertEqual(len(pairs), 15)
        self.assertAlmostEqual(sum(pairs.values()), 1.0, places=9)

    def test_keys_are_ordered_low_high_to_match_the_payout_table(self) -> None:
        for low, high in quinella_probabilities(_joint()):
            self.assertLess(low, high)

    def test_pair_is_the_sum_of_both_orderings(self) -> None:
        joint = _joint()
        pairs = quinella_probabilities(joint)
        self.assertAlmostEqual(pairs[(1, 2)], joint[12] + joint[21], places=9)


class ConsistencyWithLaneFrequencyModelTest(unittest.TestCase):
    """The two conditionals must be interchangeable at the call site --
    `evaluate_bet_types` selects between them by name."""

    def test_lane_frequency_model_produces_the_same_shaped_joint(self) -> None:
        observations = [(1, 2)] * 40 + [(1, 3)] * 20 + [(2, 1)] * 15 + [(3, 4)] * 10
        model = ConditionalSecondPlaceModel().fit(observations)
        joint = construct_exacta_probabilities(FIRST_PLACE, model)

        self.assertEqual(set(joint), set(ALL_COMBINATIONS))
        self.assertAlmostEqual(sum(top2_probabilities(joint).values()), 2.0, places=9)
        self.assertEqual(len(quinella_probabilities(joint)), 15)

    def test_the_two_conditionals_disagree_on_the_same_card(self) -> None:
        """Guards against the adapter accidentally ignoring its input:
        a lane-frequency conditional fitted on lopsided counts must not
        reproduce the Plackett-Luce numbers."""
        observations = [(1, 6)] * 100 + [(2, 1)] * 10
        fitted = ConditionalSecondPlaceModel().fit(observations)
        self.assertNotAlmostEqual(
            fitted.predict(1)[6],
            PlackettLuceSecondPlace(FIRST_PLACE).predict(1)[6],
            places=3,
        )


if __name__ == "__main__":
    unittest.main()
