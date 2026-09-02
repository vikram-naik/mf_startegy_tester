from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from mf_strategy_tester.ingestion.errors import SourceParseError


@dataclass(frozen=True)
class KfintechFund:
    code: str
    name: str


@dataclass(frozen=True)
class KfintechScheme:
    code: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class KfintechDistribution:
    record_date: str
    individual_amount: str
    non_individual_amount: str | None
    ex_nav: str | None
    cum_nav: str | None


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif not self._stack:
            return
        elif tag == "tr":
            self._stack[-1]["row"] = []
        elif tag in {"td", "th"} and self._stack[-1]["row"] is not None:
            self._stack[-1]["cell"] = []
        elif tag == "br":
            cell = self._stack[-1]["cell"]
            if isinstance(cell, list):
                cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._stack:
            cell = self._stack[-1]["cell"]
            if isinstance(cell, list):
                cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        context = self._stack[-1]
        cell = context["cell"]
        row = context["row"]
        rows = context["rows"]
        if tag in {"td", "th"} and isinstance(cell, list) and isinstance(row, list):
            row.append(_space("".join(str(part) for part in cell)))
            context["cell"] = None
        elif tag == "tr" and isinstance(row, list) and isinstance(rows, list):
            if any(row):
                rows.append(row)
            context["row"] = None
        elif tag == "table":
            completed = self._stack.pop()["rows"]
            if isinstance(completed, list):
                self.tables.append(completed)


class _SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_scheme_select = False
        self.current_value: str | None = None
        self.current_text: list[str] = []
        self.options: list[KfintechScheme] = []
        self.hidden_fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and (values.get("type") or "").casefold() == "hidden":
            name = values.get("name")
            if name:
                self.hidden_fields[name] = values.get("value") or ""
        elif tag == "select" and values.get("name") == "ctl00$ContentPlaceHolder1$SelScheme":
            self.in_scheme_select = True
        elif tag == "option" and self.in_scheme_select:
            self.current_value = values.get("value")
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_value is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self.current_value is not None:
            code = self.current_value.strip()
            name = _space("".join(self.current_text))
            if code and code != "0" and name:
                self.options.append(KfintechScheme(code=code, name=name))
            self.current_value = None
            self.current_text = []
        elif tag == "select" and self.in_scheme_select:
            self.in_scheme_select = False


class _FundParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, object]] = []
        self.funds: list[KfintechFund] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "tr":
            self.rows.append({"code": None, "name_parts": [], "in_name": False})
        elif not self.rows:
            return
        elif tag == "td" and "comptitle" in (values.get("class") or "").split():
            self.rows[-1]["in_name"] = True
        elif tag == "a":
            href = values.get("href") or ""
            match = re.search(r"Dividend\.aspx\?Fund=([A-Za-z0-9_-]+)", href, flags=re.IGNORECASE)
            if match is not None:
                self.rows[-1]["code"] = match.group(1)

    def handle_data(self, data: str) -> None:
        if self.rows and self.rows[-1]["in_name"]:
            parts = self.rows[-1]["name_parts"]
            if isinstance(parts, list):
                parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.rows:
            return
        if tag == "td" and self.rows[-1]["in_name"]:
            self.rows[-1]["in_name"] = False
        elif tag == "tr":
            row = self.rows.pop()
            code = row["code"]
            parts = row["name_parts"]
            if isinstance(code, str) and isinstance(parts, list):
                name = _space("".join(str(part) for part in parts))
                if name:
                    self.funds.append(KfintechFund(code=code, name=name))


def parse_funds(payload: str) -> tuple[KfintechFund, ...]:
    """Read KFintech's serviced-fund table without relying on link control IDs."""
    parser = _FundParser()
    parser.feed(payload)
    seen: set[str] = set()
    for fund in parser.funds:
        if fund.code in seen:
            raise SourceParseError(f"duplicate KFintech fund code {fund.code}")
        seen.add(fund.code)
    if not parser.funds:
        raise SourceParseError("KFintech landing page contains no dividend fund links")
    return tuple(parser.funds)


def parse_scheme_form(payload: str) -> tuple[tuple[KfintechScheme, ...], dict[str, str]]:
    parser = _SelectParser()
    parser.feed(payload)
    if not parser.options:
        raise SourceParseError("KFintech dividend page contains no scheme options")
    return _group_scheme_aliases(parser.options), parser.hidden_fields


def _group_scheme_aliases(
    options: list[KfintechScheme],
) -> tuple[KfintechScheme, ...]:
    """Collapse labels sharing one provider request code without guessing across plans."""
    names_by_code: dict[str, list[str]] = {}
    code_order: list[str] = []
    for option in options:
        if option.code not in names_by_code:
            names_by_code[option.code] = []
            code_order.append(option.code)
        if option.name not in names_by_code[option.code]:
            names_by_code[option.code].append(option.name)

    grouped: list[KfintechScheme] = []
    for code in code_order:
        names = names_by_code[code]
        plans = {classify_plan(name) for name in names}
        known_plans = plans - {"unknown"}
        if len(known_plans) > 1:
            raise SourceParseError(
                "KFintech duplicate scheme code has conflicting plan labels: "
                f"code={code}, names={names!r}"
            )
        primary = next(
            (name for name in names if classify_plan(name) in known_plans),
            names[0],
        )
        grouped.append(
            KfintechScheme(
                code=code,
                name=primary,
                aliases=tuple(name for name in names if name != primary),
            )
        )
    return tuple(grouped)


def parse_distributions(payload: str) -> tuple[KfintechDistribution, ...]:
    parser = _TableParser()
    parser.feed(payload)
    expected = {
        "record date": "record_date",
        "recorded date": "record_date",
        "individual": "individual_amount",
        "dividend rate individual": "individual_amount",
        "non individual": "non_individual_amount",
        "dividend rate non individual": "non_individual_amount",
        "ex nav": "ex_nav",
        "cum nav": "cum_nav",
    }
    for table in parser.tables:
        for header_index, raw_header in enumerate(table):
            normalized = [_header(cell) for cell in raw_header]
            mapped = [expected.get(cell) for cell in normalized]
            required = {"record_date", "individual_amount"}
            if not required.issubset(item for item in mapped if item is not None):
                continue
            rows: list[KfintechDistribution] = []
            seen: set[KfintechDistribution] = set()
            for source_row in table[header_index + 1 :]:
                if len(source_row) != len(raw_header):
                    raise SourceParseError(
                        "KFintech dividend table row has an unexpected column count"
                    )
                values = {
                    field: source_row[index]
                    for index, field in enumerate(mapped)
                    if field is not None
                }
                record_date = _source_date(values["record_date"])
                individual = _positive_decimal_text(
                    values["individual_amount"], "individual amount"
                )
                row = KfintechDistribution(
                    record_date=record_date,
                    individual_amount=individual,
                    non_individual_amount=_optional_nonnegative_decimal_text(
                        values.get("non_individual_amount"), "non-individual amount"
                    ),
                    ex_nav=_optional_nonnegative_decimal_text(values.get("ex_nav"), "ex NAV"),
                    cum_nav=_optional_nonnegative_decimal_text(values.get("cum_nav"), "cum NAV"),
                )
                if row not in seen:
                    rows.append(row)
                    seen.add(row)
            return tuple(rows)
    empty_markers = (
        "no record found",
        "no data found",
        "no records found",
        "no dividend records found",
    )
    if any(marker in payload.casefold() for marker in empty_markers):
        return ()
    raise SourceParseError("KFintech response has neither a dividend table nor an empty result")


def classify_plan(name: str) -> str:
    normalized = name.casefold()
    if "direct" in normalized:
        return "direct"
    if "regular" in normalized or "retail" in normalized or "institutional" in normalized:
        return "regular"
    return "unknown"


def classify_option_variant(name: str) -> str:
    normalized = name.casefold()
    if "reinvest" in normalized:
        return "reinvestment"
    if "payout" in normalized or "idcw" in normalized or "dividend" in normalized:
        return "payout"
    return "unknown"


def classify_scheme_option_variant(scheme: KfintechScheme) -> str:
    """Return a variant only when every alias agrees on its payout semantics."""
    variants = {
        classify_option_variant(name)
        for name in (scheme.name, *scheme.aliases)
        if classify_option_variant(name) != "unknown"
    }
    return next(iter(variants)) if len(variants) == 1 else "unknown"


def _source_date(value: str) -> str:
    normalized = _space(value)
    for pattern in ("%d/%m/%Y", "%d/%b/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(normalized, pattern).date().isoformat()
        except ValueError:
            continue
    raise SourceParseError(f"invalid KFintech record date {value!r}")


def _positive_decimal_text(value: str, label: str) -> str:
    parsed = _decimal(value, label)
    if parsed <= 0:
        raise SourceParseError(f"KFintech {label} must be positive")
    return str(parsed)


def _optional_positive_decimal_text(value: str | None, label: str) -> str | None:
    if value is None or not value.strip() or value.strip() in {"-", "--", "NA", "N/A"}:
        return None
    return _positive_decimal_text(value, label)


def _optional_nonnegative_decimal_text(value: str | None, label: str) -> str | None:
    if value is None or not value.strip() or value.strip() in {"-", "--", "NA", "N/A"}:
        return None
    parsed = _decimal(value, label)
    if parsed < 0:
        raise SourceParseError(f"KFintech {label} cannot be negative")
    return str(parsed)


def _decimal(value: str, label: str) -> Decimal:
    normalized = value.replace(",", "").strip()
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise SourceParseError(f"invalid KFintech {label}: {value!r}") from error
    if not parsed.is_finite():
        raise SourceParseError(f"invalid KFintech {label}: {value!r}")
    return parsed


def _header(value: str) -> str:
    return _space(re.sub(r"[^a-z0-9]+", " ", value.casefold()))


def _space(value: str) -> str:
    return " ".join(value.split())
