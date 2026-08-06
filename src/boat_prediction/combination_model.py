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

Every other combination bet this module supports is a *sum over a joint
distribution*, never a separately estimated quantity, so all of them stay
consistent with the P1 distribution by construction:

- 複勝 (top 2): `P(lane j in top 2) = P(first=j) + sum_i P(first=i, second=j)`
- 2連複 (unordered pair): `P({i,j}) = P(i,j) + P(j,i)`
- 3連複 (unordered triple): sum the 6 orderings of the trifecta joint that
  land on the same set of three lanes
- 拡連複 (wide -- pays on any 2 of the actual top 3): sum, over the
  trifecta joint, of every ordered triple that contains both lanes of the
  pair in any of its three positions

Third place: still Plackett-Luce, still a known defect
--------------------------------------------------------

3連単/3連複/拡連複 need a third-place conditional, which this module
builds as one more Plackett-Luce step:

    P(third = k | first = i, second = j) = p_k / (1 - p_i - p_j)

applied on top of *whichever* exacta joint the caller already built --
`construct_trifecta_probabilities` takes that joint as input rather than
rebuilding it, so a trifecta constructed from the lane-frequency exacta
joint is a hybrid: lane-frequency for the second place, Plackett-Luce for
the third. There is no lane-frequency equivalent for third place here --
that would need a 3-D count table (first, second, third) this module does
not fit, and the archive is thin enough at that granularity
(6*5*4 = 120 cells) that a smoothed count would mostly reflect the prior.

No odds grid exists in this database for any of these three pools --
only the winning combination's payout -- so a probability built here can
never be scored against a price, only settled as a fixed pick against
what actually paid. `evaluate_bet_types` handles that distinction; this
module just supplies the numbers.
"""

from __future__ import annotations

from .baseline import LANES
from .exacta import ALL_COMBINATIONS, decode_combination

__all__ = [
    "ALL_TRIFECTA_COMBINATIONS",
    "CombinationModelError",
    "PlackettLuceSecondPlace",
    "construct_trifecta_probabilities",
    "decode_trifecta",
    "encode_trifecta",
    "quinella_probabilities",
    "sanrenpuku_probabilities",
    "top2_probabilities",
    "wide_probabilities",
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


ALL_TRIFECTA_COMBINATIONS: tuple[int, ...] = tuple(
    sorted(
        first * 100 + second * 10 + third
        for first in LANES
        for second in LANES
        for third in LANES
        if len({first, second, third}) == 3
    )
)
"""The 120 valid ordered (first, second, third) triples, coded as a
3-digit int -- `first*100 + second*10 + third` -- mirroring
`exacta.ALL_COMBINATIONS`'s 2-digit scheme."""


def encode_trifecta(first: int, second: int, third: int) -> int:
    if first not in LANES or second not in LANES or third not in LANES:
        raise CombinationModelError(
            f"lane out of range: first={first!r} second={second!r} third={third!r}"
        )
    if len({first, second, third}) != 3:
        raise CombinationModelError(
            f"first, second and third must be distinct: {(first, second, third)!r}"
        )
    return first * 100 + second * 10 + third


def decode_trifecta(code: int) -> tuple[int, int, int]:
    if code not in ALL_TRIFECTA_COMBINATIONS:
        raise CombinationModelError(f"not a valid trifecta combination code: {code!r}")
    first, remainder = divmod(code, 100)
    second, third = divmod(remainder, 10)
    return first, second, third


def _third_place_conditional(
    first_place_probabilities: dict[int, float], first: int, second: int
) -> dict[int, float]:
    others = [lane for lane in LANES if lane not in (first, second)]
    remaining = 1.0 - first_place_probabilities[first] - first_place_probabilities[second]
    if remaining <= _DEGENERATE_TOLERANCE:
        probs = {lane: 1.0 / len(others) for lane in others}
    else:
        probs = {lane: first_place_probabilities[lane] / remaining for lane in others}
        scale = sum(probs.values())
        probs = {lane: p / scale for lane, p in probs.items()}
    return probs


def construct_trifecta_probabilities(
    exacta_joint: dict[int, float], first_place_probabilities: dict[int, float]
) -> dict[int, float]:
    """`P(first=i, second=j, third=k) = exacta_joint[i,j] * P(third=k|i,j)`.

    `exacta_joint` is whatever `exacta.construct_exacta_probabilities`
    already produced -- Plackett-Luce or lane-frequency, this function
    does not care which -- and only the third-place step is added here.
    """
    if set(exacta_joint) != set(ALL_COMBINATIONS):
        raise CombinationModelError(
            "exacta_joint must cover exactly the 30 valid exacta combinations"
        )
    if set(first_place_probabilities) != set(LANES):
        raise CombinationModelError(
            f"first_place_probabilities must cover lanes {LANES}, "
            f"got {sorted(first_place_probabilities)}"
        )

    trifecta: dict[int, float] = {}
    for code, p_first_second in exacta_joint.items():
        first, second = decode_combination(code)
        conditional = _third_place_conditional(first_place_probabilities, first, second)
        for third, p_third in conditional.items():
            trifecta[encode_trifecta(first, second, third)] = p_first_second * p_third

    total = sum(trifecta.values())
    if abs(total - 1.0) > 1e-6:
        raise CombinationModelError(f"trifecta probabilities must sum to 1.0, got {total!r}")
    return trifecta


def _validate_trifecta(trifecta_probs: dict[int, float]) -> None:
    if set(trifecta_probs) != set(ALL_TRIFECTA_COMBINATIONS):
        raise CombinationModelError(
            "trifecta_probs must cover exactly the 120 valid trifecta combinations"
        )


def sanrenpuku_probabilities(trifecta_probs: dict[int, float]) -> dict[tuple[int, int, int], float]:
    """`P({i, j, k} fill the first three places, in any order)` -- 3連複.

    Keyed by an ascending `(low, mid, high)` tuple, matching how
    `race_payouts.combination` writes a 3連複 ("1-2-3", always sorted).
    Each of the 20 unordered triples is the sum of the 6 orderings of the
    trifecta joint that produce it, so the 20 values sum to 1.0.
    """
    _validate_trifecta(trifecta_probs)
    triples: dict[tuple[int, int, int], float] = {}
    for code, prob in trifecta_probs.items():
        key = tuple(sorted(decode_trifecta(code)))
        triples[key] = triples.get(key, 0.0) + prob
    return triples


def wide_probabilities(trifecta_probs: dict[int, float]) -> dict[tuple[int, int], float]:
    """`P(both lanes of the pair finish in the top 3, any order)` -- 拡連複.

    Keyed the same low-high way as `quinella_probabilities`. Three of the
    15 unordered pairs win on every race (the three pairs drawn from the
    actual top 3), so unlike 単勝/複勝 this is not a probability
    distribution over its own outcomes and the 15 values sum to 3.0, not
    1.0 -- the same reason `top2_probabilities` sums to 2.0.
    """
    _validate_trifecta(trifecta_probs)
    pairs: dict[tuple[int, int], float] = {}
    for code, prob in trifecta_probs.items():
        first, second, third = decode_trifecta(code)
        for a, b in ((first, second), (first, third), (second, third)):
            key = (min(a, b), max(a, b))
            pairs[key] = pairs.get(key, 0.0) + prob
    return pairs
