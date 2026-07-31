"""Classify a race's position in its 節 (series) from its class label.

The B-file race header carries a class label (`bfile_parser.
ParsedRaceCard.race_class`, stored as `races.race_class`) that names the
race: 予選, 準優勝戦, 優勝戦, 選抜戦, and so on. That label is the only
leakage-safe signal of series phase available from the parsed sources —
"last race of the last day" would settle it far more reliably, but a
series' end date is future knowledge for any race inside it, which is
exactly why `db.models.RaceMeeting.meeting_end_date` is NULL by design.

The phase matters because a lane number does not mean the same thing in
every phase. In 予選 the lane is essentially arbitrary; in 準優勝戦 and
優勝戦 it is assigned by standing (点率 order), so boat 1 in a final is a
systematically stronger boat than boat 1 in a heat. Mixing the phases
therefore mis-calibrates any lane-conditioned probability.

Label matching is not as simple as `== "優勝戦"`. A full sweep of the
2005-2026 K-file archive (7,863 files, 97,079 venue-days, 1,675 distinct
labels) found the traps this module encodes:

- Finals are often named after the event rather than called 優勝戦:
  王将位決定戦, 王座決定戦, 海の王者決定戦, ファイナル, 決勝戦.
- The label field is fixed-width and truncates, so a sponsored final
  arrives as `サントリー優`, `スピード王優`, `住信ＳＢＩ優` — a bare
  trailing 優.
- `順位決定戦` (475 races) is *not* a final, so a bare "contains 決定"
  rule would be wrong for 83% of the races it matches. 順位 and 代表
  are therefore excluded from it. What remains is right for 85 of 96
  archive races; the known residual is 牛若丸決定戦 (4), 児島王決定戦
  (1) and 男子/女子代表決定 (6), which do not sit in the final slot.
- `準優勝戦` contains 優勝, and `準優` ends in 優, so the semifinal test
  must run before the final test or every semifinal reads as a final.
- `準優進出戦` / `準優勝進出戦` / `準々決勝戦` qualify *into* the
  semifinal and are not semifinals themselves.

Ambiguous labels return `RACE_PHASE_UNKNOWN` rather than a guess: a
wrong phase is worse than a missing one, because it moves a race into a
group whose lane semantics do not apply to it.
"""

from __future__ import annotations

import re

RACE_PHASE_TRIAL = "trial"
"""予選 — the heats that build the standing."""

RACE_PHASE_QUALIFIER = "qualifier"
"""準優進出戦 / 準々決勝戦 — races that qualify into the semifinal."""

RACE_PHASE_SEMIFINAL = "semifinal"
"""準優勝戦 — lanes assigned by standing; top finishers reach the final."""

RACE_PHASE_FINAL = "final"
"""優勝戦 — the series decider, lanes assigned by standing."""

RACE_PHASE_SELECTION = "selection"
"""選抜戦 / 特選 / 特賞 — graded races outside the qualifying ladder."""

RACE_PHASE_GENERAL = "general"
"""一般戦 — an ordinary race carrying no series role."""

RACE_PHASE_UNKNOWN = "unknown"
"""Not classifiable from the label alone; never a guess."""

_WHITESPACE = re.compile(r"[\s　]+")
# Appended to the class, not part of it ("予選　進入固定").
_MODIFIERS = ("進入固定",)


def normalize_race_class(race_class: str | None) -> str:
    """Strip layout padding and the 進入固定 modifier from a class label.

    `bfile_parser.ParsedRaceCard.race_class` already removes whitespace;
    this repeats it so the function also accepts a raw label or a value
    read back from the database.
    """
    if not race_class:
        return ""
    text = _WHITESPACE.sub("", race_class)
    for modifier in _MODIFIERS:
        text = text.replace(modifier, "")
    return text


def classify_race_phase(race_class: str | None) -> str:
    """Map a race class label to one of the `RACE_PHASE_*` constants.

    Order matters: 準優勝戦 contains 優勝 and 準優 ends in 優, so the
    semifinal tests must precede the final tests. See the module
    docstring for the evidence behind each rule.
    """
    text = normalize_race_class(race_class)
    if not text:
        return RACE_PHASE_UNKNOWN

    if "進出" in text or "準々" in text:
        return RACE_PHASE_QUALIFIER
    if "準優" in text or "準決勝" in text:
        return RACE_PHASE_SEMIFINAL

    if "優勝" in text or "決勝" in text or text.endswith(("ファイナル", "優")):
        return RACE_PHASE_FINAL
    if "決定" in text and "順位" not in text and "代表" not in text:
        return RACE_PHASE_FINAL

    if "予選" in text:
        return RACE_PHASE_TRIAL
    if "選抜" in text or "特選" in text or "特賞" in text:
        return RACE_PHASE_SELECTION
    if "一般" in text:
        return RACE_PHASE_GENERAL
    return RACE_PHASE_UNKNOWN


def is_final(race_class: str | None) -> bool:
    """True for the series decider (優勝戦 and its event-named forms)."""
    return classify_race_phase(race_class) == RACE_PHASE_FINAL


def is_semifinal(race_class: str | None) -> bool:
    """True for 準優勝戦, excluding the races that qualify into it."""
    return classify_race_phase(race_class) == RACE_PHASE_SEMIFINAL


def is_standing_seeded(race_class: str | None) -> bool:
    """True when lanes are assigned by standing rather than arbitrarily.

    The single most useful derived flag: it marks the races where lane
    number encodes strength, so a lane-conditioned probability fitted on
    heats does not transfer to them.
    """
    return classify_race_phase(race_class) in (RACE_PHASE_SEMIFINAL, RACE_PHASE_FINAL)
