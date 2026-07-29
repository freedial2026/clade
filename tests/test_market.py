import unittest

from boat_prediction.market import MarketError, normalize_market_odds, overround

WORKED_EXAMPLE_ODDS = {1: 2.0, 2: 3.0, 3: 4.0, 4: 5.0, 5: 6.0, 6: 10.0}


class NormalizeMarketOddsTest(unittest.TestCase):
    def test_probabilities_sum_to_one(self) -> None:
        result = normalize_market_odds(WORKED_EXAMPLE_ODDS)
        self.assertAlmostEqual(sum(result.values()), 1.0)

    def test_matches_hand_computed_worked_example(self) -> None:
        result = normalize_market_odds(WORKED_EXAMPLE_ODDS)

        expected = {
            1: 0.322581,
            2: 0.215054,
            3: 0.161290,
            4: 0.129032,
            5: 0.107527,
            6: 0.064516,
        }
        for lane, expected_prob in expected.items():
            self.assertAlmostEqual(result[lane], expected_prob, places=5)

    def test_favorite_gets_the_highest_probability(self) -> None:
        result = normalize_market_odds(WORKED_EXAMPLE_ODDS)
        self.assertEqual(max(result, key=result.get), 1)  # lowest odds -> lane 1

    def test_rejects_empty_odds(self) -> None:
        with self.assertRaises(MarketError):
            normalize_market_odds({})

    def test_rejects_zero_odds(self) -> None:
        with self.assertRaises(MarketError):
            normalize_market_odds({1: 0.0, 2: 3.0})

    def test_rejects_negative_odds(self) -> None:
        with self.assertRaises(MarketError):
            normalize_market_odds({1: -1.5, 2: 3.0})


class OverroundTest(unittest.TestCase):
    def test_matches_hand_computed_worked_example(self) -> None:
        self.assertAlmostEqual(overround(WORKED_EXAMPLE_ODDS), 1.55, places=5)

    def test_rejects_invalid_odds(self) -> None:
        with self.assertRaises(MarketError):
            overround({1: 0.0})

    def test_rejects_empty_odds(self) -> None:
        with self.assertRaises(MarketError):
            overround({})


if __name__ == "__main__":
    unittest.main()
