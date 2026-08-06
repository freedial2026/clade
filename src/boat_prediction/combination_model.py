"""Combination-bet probabilities derived from a first-place distribution.

`exacta.py` (P3-T003) already builds the coherent 30-class 2連単
distribution `P(first=i, second=j) = P(first=i) * P(second=j | first=i)`,
but the only conditional model available to it was
`second_place.ConditionalSecondPlaceModel` -- a smoothed count over
*lane pairs*, which knows nothing about who is in the boats. That is the
right baseline and the wrong production model: it gives every race the
same conditional, so two cards with identical lane numbers and wholly
different racers get identical exacta probabilities.

This module supplies the missing per-race conditional and the marginals
the other combination bets need.

The Plackett-Luce conditional
-----------------------------

`PlackettLuceSecondPlace` reads the second place off the *same* P1
distribution, by removing the winner and renormalizing:

    P(second = j | first = i) = p_j / (1 - p_i)

This is not a new fitted model and deliberately costs nothing: it is the
Luce choice axiom applied twice, and it is the conditional a multinomial
logit already implies. The P1 model is a multinomial logistic regression
(`evaluate_p1.sklearn_logistic_factory`), so taking its own implied
second-place distribution is more consistent than bolting a separately
estimated one onto it.

What it assumes, and where that is wrong
----------------------------------------

Luce's axiom says the relative odds of j against k do not change when i
is removed ("independence of irrelevant alternatives"). In a boat race
that is *false* in a known direction: lane 1 winning usually means it led
into the first turn, which favours the boats that were behind it on the
inside, while lane 1 losing usually means it was overtaken, which
implicates whichever boat did the overtaking. The winner's identity
carries information about *how* the race unfolded, and this conditional
throws that away.

So it is a baseline with a real defect, not a finished model, and
`evaluate_bet_types` scores it against the lane-frequency conditional on
held-out second-place log-loss rather than assuming it wins. What it has
over the lane-frequency model is that it varies with the card; what the
lane-frequency model has over it is that it has actually seen how races
unfold. Neither dominates on argument alone, which is why both are run.

Marginals
---------

Every other combination bet this module supports is a *sum over the
30-class joint*, never a separately estimated quantity, so all of them
stay consistent with the P1 distribution by construction:

- 複勝 (top 2): `P(lane j in top 2) = P(first=j) + sum_i P(first=i, second=j)`
- 2連複 (unordered pair): `P({i,j}) = P(i,j) + P(j,i)`

3連単/3連複/拡連複 are **not** built here. They need a conditional third
place, and more to the point no odds grid for them exists in this
database (only the winning combination's payout), so a probability for
them could not be scored against a price. See `evaluate_bet_types`.
"""

from __future__ import annotations

from .baseline import LANES
from .exacta import ALL_COMBINATIONS, decode_combination

__all__ = [
    "CombinationModelError",
    "PlackettLuceSecondPlace",
    "quinella_probabilities",
    "top2_probabilities",
]


class CombinationModelError(ValueError):
    """Raised for invalid combination-probability input."""


# A first-place probability this close to 1.0 leaves `1 - p_i` too small
# to divide by meaningfully. Rather than emit a conditional dominated by
# floating-point noise, the model falls back to the uniform distribution
# over the remaining lanes: at that point the winner is a near-certainty
# and the P1 model is saying nothing useful about who follows it.
_DEGENERATE_TOLERANCE = 1e-9


class PlackettLuceSecondPlace:
    """`P(second = j | first = i) = p_j / (1 - p_i)` for one race.

    Exposes the same `predict(first) -> {lane: prob}` interface as
    `second_place.ConditionalSecondPlaceModel`, so it drops straight into
    `exacta.construct_exacta_probabilities` and inherits that function's
    coherence check (sums to 1.0, marginals recover P1) rather than
    re-implementing one.

    Unlike that model this one is per-race and holds no fitted state --
    it is constructed from the race's own first-place distribution.
    """

    def __init__(self, first_place_probabilities: dict[int, float]) -> None:
        if set(first_place_probabilities) != set(LANES):
            raise CombinationModelError(
                f"first_place_probabilities must cover lanes {LANES}, "
                f"got {sorted(first_place_probabilities)}"
            )
        if any(p < 0.0 for p in first_place_probabilities.values()):
            raise CombinationModelError(
                f"first_place_probabilities must be non-negative: {first_place_probabilities}"
            )
        total = sum(first_place_probabilities.values())
        if abs(total - 1.0) > 1e-6:
            raise CombinationModelError(
                f"first_place_probabilities must sum to 1.0, got {total!r}"
            )
        self._probs = dict(first_place_probabilities)

    def predict(self, first_place: int) -> dict[int, float]:
        if first_place not in LANES:
            raise CombinationModelError(f"first_place out of range: {first_place!r}")

        remaining = 1.0 - self._probs[first_place]
        others = [lane for lane in LANES if lane != first_place]
        if remaining <= _DEGENERATE_TOLERANCE:
            probs = {lane: 1.0 / len(others) for lane in others}
        else:
            probs = {lane: self._probs[lane] / remaining for lane in others}
            # Renormalize against the residual left by dividing floats:
            # `construct_exacta_probabilities` checks the joint sums to
            # 1.0 within 1e-9 and would otherwise reject a distribution
            # that is correct to every digit that matters.
            scale = sum(probs.values())
            probs = {lane: p / scale for lane, p in probs.items()}
        probs[first_place] = 0.0
        return probs


def top2_probabilities(combination_probs: dict[int, float]) -> dict[int, float]:
    """`P(lane finishes first or second)` for each lane -- the 複勝 target.

    Summed out of the exacta joint rather than estimated separately, so
    it cannot disagree with the first-place distribution it came from.
    The six values sum to 2.0, not 1.0: two boats collect on every race,
    which is why 複勝 is not a probability distribution over lanes and
    must not be validated as one.
    """
    _validate_joint(combination_probs)
    top2 = dict.fromkeys(LANES, 0.0)
    for code, prob in combination_probs.items():
        first, second = decode_combination(code)
        top2[first] += prob
        top2[second] += prob
    return top2


def quinella_probabilities(combination_probs: dict[int, float]) -> dict[tuple[int, int], float]:
    """`P({i, j} fill the first two places, in either order)` -- 2連複.

    Keyed by an ordered `(low, high)` tuple, matching how
    `race_payouts.combination` writes a 2連複 ("1-2", never "2-1"), so a
    settlement lookup needs no second normalization step.
    """
    _validate_joint(combination_probs)
    pairs: dict[tuple[int, int], float] = {}
    for code, prob in combination_probs.items():
        first, second = decode_combination(code)
        key = (min(first, second), max(first, second))
        pairs[key] = pairs.get(key, 0.0) + prob
    return pairs


def _validate_joint(combination_probs: dict[int, float]) -> None:
    if set(combination_probs) != set(ALL_COMBINATIONS):
        raise CombinationModelError(
            "combination_probs must cover exactly the 30 valid exacta combinations"
        )
