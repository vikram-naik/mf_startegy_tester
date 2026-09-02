from mf_strategy_tester.services.distribution_quality import classify_distribution_label


def test_scheme_name_growth_word_is_not_an_option_conflict() -> None:
    assessment = classify_distribution_label(
        scheme_name="ICICI Prudential Growth Fund - Series X",
        nav_name="ICICI Prudential Growth Fund - Series X - IDCW",
    )

    assert assessment.classification == "distribution"
    assert assessment.option_descriptor == "IDCW"


def test_scheme_name_dividend_word_is_not_option_semantics() -> None:
    assessment = classify_distribution_label(
        scheme_name="Example Dividend Yield Fund",
        nav_name="Example Dividend Yield Fund - Growth Option",
    )

    assert assessment.classification == "growth_or_cumulative"


def test_bonus_and_dividend_source_label_is_ambiguous() -> None:
    assessment = classify_distribution_label(
        scheme_name="Tata Money Market Fund Regular",
        nav_name="Tata Money Market Fund Regular (Bonus / Dividend)",
    )

    assert assessment.classification == "bonus_or_distribution"


def test_bonus_and_dividend_in_identical_source_names_is_ambiguous() -> None:
    assessment = classify_distribution_label(
        scheme_name="Tata Money Market Fund Regular (Bonus / Dividend)",
        nav_name="Tata Money Market Fund Regular (Bonus / Dividend)",
    )

    assert assessment.classification == "bonus_or_distribution"


def test_historical_div_abbreviation_is_explicit_distribution_marker() -> None:
    assessment = classify_distribution_label(
        scheme_name="Tata Capital Builder Fund - Div",
        nav_name="Tata Capital Builder Fund - Div",
    )

    assert assessment.classification == "distribution"


def test_frequency_only_label_remains_unknown() -> None:
    assessment = classify_distribution_label(
        scheme_name="Tata Liquid Retail Investment Plan - Daily",
        nav_name="Tata Liquid Retail Investment Plan - Daily",
    )

    assert assessment.classification == "unknown"


def test_full_idcw_phrase_is_explicit_distribution_marker() -> None:
    assessment = classify_distribution_label(
        scheme_name="Taurus Flexi Cap Fund",
        nav_name=(
            "Taurus Flexi Cap Fund - Regular Plan - Payout of Income Distribution cum "
            "Capital Withdrawal option"
        ),
    )

    assert assessment.classification == "distribution"


def test_dividend_added_to_a_renamed_scheme_is_explicit_distribution_marker() -> None:
    assessment = classify_distribution_label(
        scheme_name="Nippon India Liquid Fund",
        nav_name="Reliance Liquid Fund-Cash Plan-Monthly Dividend Plan",
    )

    assert assessment.classification == "distribution"


def test_dividend_plan_in_both_source_names_is_explicit_distribution_marker() -> None:
    assessment = classify_distribution_label(
        scheme_name="Tata Income Fund - Direct Plan - Quarterly Dividend Plan",
        nav_name="Tata Long Term Debt Fund - Direct Plan - Quarterly Dividend Plan",
    )

    assert assessment.classification == "distribution"


def test_cumulative_option_is_not_a_cash_distribution_candidate() -> None:
    assessment = classify_distribution_label(
        scheme_name="Example Fixed Maturity Plan",
        nav_name="Example Fixed Maturity Plan - Direct Plan - Cumulative Option",
    )

    assert assessment.classification == "growth_or_cumulative"
