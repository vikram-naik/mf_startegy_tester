import pytest

from mf_strategy_tester.ingestion.errors import SourceParseError
from mf_strategy_tester.ingestion.kfintech import (
    classify_option_variant,
    classify_plan,
    classify_scheme_option_variant,
    parse_distributions,
    parse_funds,
    parse_scheme_form,
)


def test_kfintech_landing_and_scheme_forms_preserve_proprietary_codes() -> None:
    landing = """
    <table><tr><td><table><tr><td>x</td></tr></table></td>
      <td class="comptitle">Axis Asset Management Company Limited</td>
      <td><a href="javascript:x('../../NAVDividend/Dividend.aspx?Fund=128')">Dividend</a></td>
    </tr></table>
    """
    form = """
    <input type="hidden" name="__VIEWSTATE" value="opaque" />
    <select name="ctl00$ContentPlaceHolder1$SelScheme">
      <option value="0">Select</option>
      <option value="DE#D1">Axis Balanced Advantage Fund - Direct IDCW</option>
      <option value="DE#DP">Axis Balanced Advantage Fund - Regular IDCW</option>
    </select>
    """

    assert parse_funds(landing)[0].code == "128"
    schemes, hidden = parse_scheme_form(form)
    assert [item.code for item in schemes] == ["DE#D1", "DE#DP"]
    assert hidden == {"__VIEWSTATE": "opaque"}
    assert classify_plan(schemes[0].name) == "direct"
    assert classify_option_variant(schemes[0].name) == "payout"


def test_kfintech_scheme_form_retains_compatible_duplicate_code_aliases() -> None:
    form = """
    <input type="hidden" name="__VIEWSTATE" value="opaque" />
    <select name="ctl00$ContentPlaceHolder1$SelScheme">
      <option value="0">Select</option>
      <option value="DB#RM">Quantum Dynamic Bond Fund - Regular Plan Monthly IDCW Payout</option>
      <option value="DB#RM">
        Quantum Dynamic Bond Fund Regular Plan Monthly IDCW Reinvestment
      </option>
    </select>
    """

    schemes, _ = parse_scheme_form(form)

    assert len(schemes) == 1
    assert schemes[0].code == "DB#RM"
    assert schemes[0].aliases == (
        "Quantum Dynamic Bond Fund Regular Plan Monthly IDCW Reinvestment",
    )
    assert classify_scheme_option_variant(schemes[0]) == "unknown"


def test_kfintech_scheme_form_rejects_duplicate_code_identity_conflict() -> None:
    form = """
    <select name="ctl00$ContentPlaceHolder1$SelScheme">
      <option value="XX#01">Alpha Fund - Direct Plan IDCW</option>
      <option value="XX#01">Beta Fund - Regular Plan IDCW</option>
    </select>
    """

    with pytest.raises(SourceParseError, match="conflicting plan labels"):
        parse_scheme_form(form)


def test_kfintech_scheme_form_accepts_growth_and_idcw_alias_for_same_code() -> None:
    form = """
    <select name="ctl00$ContentPlaceHolder1$SelScheme">
      <option value="MP#ZM">Principal Corporate Bond Fund - Direct Plan AEP Monthly Growth</option>
      <option value="MP#ZM">Principal Corporate Bond Fund - Direct Plan IDCW Monthly</option>
    </select>
    """

    schemes, _ = parse_scheme_form(form)

    assert len(schemes) == 1
    assert classify_scheme_option_variant(schemes[0]) == "payout"


def test_kfintech_distribution_table_is_strict_and_decimal_safe() -> None:
    payload = """
    <table><tr><td>layout
    <table>
      <tr><th>Recorded Date</th><th>Dividend Rate - Individual</th>
          <th>Dividend Rate - Non Individual</th>
          <th>Ex NAV</th><th>Cum NAV</th></tr>
      <tr><td>15/Jul/2026</td><td>0.2500</td><td>0.2000</td>
          <td>12.3456</td><td>12.5956</td></tr>
    </table></td></tr></table>
    """

    rows = parse_distributions(payload)

    assert len(rows) == 1
    assert rows[0].record_date == "2026-07-15"
    assert rows[0].individual_amount == "0.2500"
    assert rows[0].non_individual_amount == "0.2000"
    assert rows[0].ex_nav == "12.3456"


def test_kfintech_distribution_parser_rejects_structural_drift() -> None:
    with pytest.raises(SourceParseError, match="neither a dividend table"):
        parse_distributions("<html><body>unexpected response</body></html>")


def test_kfintech_distribution_parser_accepts_live_empty_result_wording() -> None:
    assert (
        parse_distributions(
            "<html><body>There are no dividend records found for this scheme.</body></html>"
        )
        == ()
    )


def test_kfintech_distribution_parser_preserves_zero_non_individual_amount() -> None:
    payload = """
    <table>
      <tr><th>Recorded Date</th><th>Dividend Rate - Individual</th>
          <th>Dividend Rate - Non Individual</th><th>Ex. NAV</th><th>Cum. NAV</th></tr>
      <tr><td>20/Jan/2026</td><td>0.1500</td><td>0</td><td>10.25</td><td>10.40</td></tr>
    </table>
    """

    row = parse_distributions(payload)[0]

    assert row.record_date == "2026-01-20"
    assert row.non_individual_amount == "0"


def test_kfintech_distribution_parser_preserves_distinct_same_date_source_rows() -> None:
    payload = """
    <table>
      <tr><th>Recorded Date</th><th>Dividend Rate - Individual</th>
          <th>Dividend Rate - Non Individual</th><th>Ex. NAV</th><th>Cum. NAV</th></tr>
      <tr><td>08/Sep/2025</td><td>0.06</td><td>0.06</td><td>10.8260</td><td>10.8260</td></tr>
      <tr><td>08/Sep/2025</td><td>0.06</td><td>0.06</td><td>10.8197</td><td>10.8797</td></tr>
      <tr><td>08/Sep/2025</td><td>0.06</td><td>0.06</td><td>10.8197</td><td>10.8797</td></tr>
    </table>
    """

    rows = parse_distributions(payload)

    assert len(rows) == 2
    assert {row.record_date for row in rows} == {"2025-09-08"}
    assert {row.ex_nav for row in rows} == {"10.8260", "10.8197"}
