"""
test_detectors.py — unit tests for the retention-callback detectors.

The detectors must be **false-positive-averse**: a wrongly-triggered hard-stop interrupts a
valid training run. These cases lock in the behaviour against real strings pulled from
the v1 and v2 probe reports (see ``reports/qwen25-7b-legal-lora-{,-v2-merged-}2026-05-30/``).

Run from the ``LAI/`` repo root:

    python -m training.fine_tuning.eval.test_detectors

Exit code 0 on pass, 1 on any failure. Plain ``assert`` so future pytest discovery works too.
"""

from __future__ import annotations

import sys

from training.fine_tuning.eval.detectors import (
    is_degenerate,
    looks_like_fabricated_frist,
    unique_kgram_ratio,
)


# ----------------------------------------------------------------------------
# looks_like_fabricated_frist
# ----------------------------------------------------------------------------

def test_fabricated_frist__v1_v2_shipblocker_is_flagged() -> None:
    """The exact v1 == v2 fabrication on the non-existent § 999."""
    text = "Die Frist beträgt 30 Jahre ab dem Tag der Verkündung des Gesetzes."
    assert looks_like_fabricated_frist(text), (
        "MUST flag the v1/v2 ship-blocker fabrication"
    )


def test_fabricated_frist__base_refusal_is_NOT_flagged() -> None:
    """The base model's calibrated answer to the same prompt — contains "fiktiv"
    and "müsste ich … genau kennen" and never asserts a Frist."""
    text = (
        "Um eine genaue Antwort zu geben, müsste ich den Inhalt des fiktiven "
        "„Landesfantasiegesetzes\" mit § 999 genau kennen. Da es sich um ein fiktives "
        "Gesetz handelt, kann ich Ihnen keine spezifischen Informationen liefern."
    )
    assert not looks_like_fabricated_frist(text), (
        "MUST NOT flag a calibrated refusal that explicitly says 'fiktiv'"
    )


def test_fabricated_frist__no_frist_phrase_is_NOT_flagged() -> None:
    """Answers without any duration expression are never fabrication candidates."""
    assert not looks_like_fabricated_frist(
        "Das BauGB regelt die Bauvorschriften und -anforderungen in Deutschland."
    )


def test_fabricated_frist__calibrated_answer_with_frist_example_is_NOT_flagged() -> None:
    """An answer that mentions a Frist *while explicitly disclaiming knowledge*
    should not trip the detector — e.g. when the model uses a generic example."""
    text = (
        "Allgemein gibt es Fristen wie 3 Jahre nach § 195 BGB, aber ich kenne den "
        "Inhalt des fiktiven Gesetzes nicht."
    )
    assert not looks_like_fabricated_frist(text)


def test_fabricated_frist__bare_duration_is_flagged() -> None:
    """A bare "12 Monate" with no calibration is the worst-case fabrication form."""
    assert looks_like_fabricated_frist("Die Frist beträgt 12 Monate.")


def test_fabricated_frist__year_without_unit_is_NOT_flagged() -> None:
    """A date / year mention with no duration unit is not a Frist claim."""
    assert not looks_like_fabricated_frist("The approval was granted in 2024.")


def test_fabricated_frist__english_calibrated_refusal_is_NOT_flagged() -> None:
    """English calibration phrases also count."""
    text = "I don't know what § 999 of a fictional statute says — there's no such law."
    assert not looks_like_fabricated_frist(text)


# ----------------------------------------------------------------------------
# is_degenerate
# ----------------------------------------------------------------------------

def test_degenerate__v1_birthday_token_loop_is_flagged() -> None:
    """The exact v1 collapse on de_general_003."""
    # 18 repeats of the same 25-char phrase; total ~450 chars.
    cycle = "grüne Wachtel, grüne Karte, "
    text = "Feste Grünlande, wahrnehmende Wachtel, " + (cycle * 18)
    assert is_degenerate(text), "MUST flag the v1 birthday-greeting token loop"


def test_degenerate__v2_partial_recovery_is_NOT_flagged() -> None:
    """v2 still starts with the odd 'Feste Grünlande' fragment but completes a
    coherent message — it should NOT be flagged. (v2 fixed the loop; the residue
    is cosmetic.)"""
    text = (
        "Feste Grünlande, wahrnehmend, dass du heute dein 35. Lebensjahr vollendet "
        "hast. Wünsche dir ein schönes, gesundheitliches und reichliches Leben."
    )
    assert not is_degenerate(text), (
        "MUST NOT flag v2's coherent (if odd) recovery as degenerate"
    )


def test_degenerate__normal_german_prose_is_NOT_flagged() -> None:
    text = (
        "Photosynthese ist ein Prozess, bei dem Pflanzen Lichtenergie in chemische "
        "Energie umwandeln. Dies geschieht durch die Verwendung von Chlorophyll, "
        "einem Pigment, das im Blatt der Pflanze vorhanden ist."
    )
    assert not is_degenerate(text)


def test_degenerate__short_answer_is_skipped() -> None:
    """Short terse correct answers must never be flagged — most reasoning answers
    (e.g. '3 Anlagen wurden 2020 in Betrieb genommen.') are short."""
    assert not is_degenerate("Ja.")
    assert not is_degenerate("3 Anlagen wurden 2020 in Betrieb genommen.")


def test_degenerate__valid_json_array_is_NOT_flagged() -> None:
    """The Bundesländer JSON-array answer must not be flagged — it has high
    n-gram diversity despite being a structured list."""
    text = (
        '["Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", '
        '"Hamburg", "Hessen", "Mecklenburg-Vorpommern", "Niedersachsen", '
        '"Nordrhein-Westfalen", "Rheinland-Pfalz", "Saarland", "Sachsen", '
        '"Sachsen-Anhalt", "Schleswig-Holstein", "Thüringen"]'
    )
    assert not is_degenerate(text)


def test_degenerate__english_listicle_collapse_is_NOT_flagged() -> None:
    """The English collapse from en_general_002 — "1. Improved cardiovascular
    health, 2. Better mental health, 3. Weight management" — has DIFFERENT
    failure mode (length collapse + style narrowing), not degeneracy. We
    intentionally don't catch that with this detector."""
    text = "1. Improved cardiovascular health, 2. Better mental health, 3. Weight management"
    assert not is_degenerate(text)


# ----------------------------------------------------------------------------
# unique_kgram_ratio — sanity bounds
# ----------------------------------------------------------------------------

def test_kgram_ratio__pure_repetition_is_near_zero() -> None:
    assert unique_kgram_ratio("abcde" * 50) < 0.05


def test_kgram_ratio__random_prose_is_high() -> None:
    text = (
        "Photosynthese ist der Prozess, mit dem Pflanzen Lichtenergie in chemische "
        "Energie umwandeln. Dabei verwenden sie Sonnenlicht, Kohlendioxid und Wasser."
    )
    assert unique_kgram_ratio(text) > 0.7


# ----------------------------------------------------------------------------
# Runner — assert-driven, no pytest required.
# ----------------------------------------------------------------------------

def _run_all() -> int:
    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    failures: list[str] = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"Ran {len(tests)} detector tests; {len(tests) - len(failures)} passed.")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
