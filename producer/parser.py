"""Form 4 parser — THE MOAT. Correct, complete parsing IS the product.

Raw reformatted XML is a commodity anyone can ship. Complete, correct parsing is not.
This parser is built against REAL filings (tests/fixtures/) and handles, per docs/03:

  1. Amendments (Form 4/A) — flagged via documentType ("4/A").
  2. Non-derivative (Table I) vs. derivative (Table II) — both, labeled.
  3. Transaction codes mapped to MEANING + discretionary-vs-mechanical (the value-add).
  4. Rule 10b5-1 — the structured doc flag <aff10b5One> (schema X0508+) OR, for older
     filings, footnote text. We check both.
  5. Multiple reporting owners -> one record per owner (with filing_id to dedupe by filing).
  6. Direct (D) vs. indirect (I) ownership.
  7. Footnotes preserved + linked to the transactions that reference them.
  8. Price (range/weighted-avg lives in footnotes; price_low/high reserved for later enrich).
  9. Post-transaction holdings.
 10. Issuer identity: CIK, name, ticker.

Form 4 XML has no namespaces, so stdlib ElementTree suffices.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

# SEC transaction code -> (human meaning, is_discretionary).
# Discretionary P/S are the high-signal events; A/M/F/G are mechanical by comparison.
TXN_CODES: dict[str, tuple[str, bool]] = {
    "P": ("open_market_purchase", True),
    "S": ("open_market_sale", True),
    "V": ("voluntary_early_report", True),
    "A": ("grant_or_award", False),
    "D": ("disposition_to_issuer", False),
    "F": ("shares_withheld_for_tax", False),
    "I": ("discretionary_transaction", True),
    "M": ("option_exercise", False),
    "C": ("conversion_of_derivative", False),
    "E": ("expiration_short_derivative", False),
    "H": ("expiration_long_derivative", False),
    "O": ("exercise_out_of_money_derivative", False),
    "X": ("exercise_in_money_derivative", False),
    "G": ("gift", False),
    "L": ("small_acquisition", False),
    "W": ("acquisition_disposition_by_will", False),
    "Z": ("deposit_withdrawal_voting_trust", False),
    "J": ("other", None),
    "K": ("equity_swap", None),
    "U": ("tender_of_shares", None),
}


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _child(el: ET.Element, tag: str) -> str | None:
    """Direct child text, e.g. <transactionCode>S</transactionCode>."""
    return _text(el.find(tag))


def _value(el: ET.Element, tag: str) -> str | None:
    """Value-wrapped child, e.g. <transactionShares><value>50</value></transactionShares>."""
    node = el.find(tag)
    return _text(node.find("value")) if node is not None else None


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _footnote_ids(el: ET.Element) -> list[str]:
    """Footnote ids referenced anywhere within this transaction subtree."""
    return [fid.get("id") for fid in el.iter("footnoteId") if fid.get("id")]


def _clean_ticker(t: str | None) -> str | None:
    """Filers put 'NONE'/'N/A' in the ticker field for non-traded issuers — null those."""
    if t and t.strip().upper() not in {"NONE", "N/A", "NA", "-"}:
        return t.strip()
    return None


def _classify(code: str | None) -> tuple[str | None, bool | None]:
    if not code:
        return None, None
    return TXN_CODES.get(code, (f"code_{code}", None))


def _owners(root: ET.Element) -> list[dict]:
    owners = []
    for ro in root.findall("reportingOwner"):
        rid = ro.find("reportingOwnerId")
        rel = ro.find("reportingOwnerRelationship")
        rel = rel if rel is not None else ET.Element("x")
        roles = []
        is_dir = _child(rel, "isDirector") == "1"
        is_off = _child(rel, "isOfficer") == "1"
        is_ten = _child(rel, "isTenPercentOwner") == "1"
        is_oth = _child(rel, "isOther") == "1"
        title = _child(rel, "officerTitle")
        if is_dir:
            roles.append("director")
        if is_off:
            roles.append(f"officer:{title}" if title else "officer")
        if is_ten:
            roles.append("ten_percent_owner")
        if is_oth:
            roles.append("other")
        owners.append({
            "cik": _child(rid, "rptOwnerCik") if rid is not None else None,
            "name": _child(rid, "rptOwnerName") if rid is not None else None,
            "roles": roles,
            "is_director": is_dir,
            "is_officer": is_off,
            "is_ten_percent_owner": is_ten,
            "officer_title": title,
        })
    return owners


def _transaction(tx: ET.Element, table: str, footnotes: dict, doc_10b5_1: bool) -> dict:
    code = _child(tx.find("transactionCoding"), "transactionCode") if tx.find("transactionCoding") is not None else None
    meaning, discretionary = _classify(code)
    fids = _footnote_ids(tx)
    linked = [{"id": fid, "text": footnotes.get(fid)} for fid in fids]
    # 10b5-1: the document flag, OR a referenced footnote that mentions the rule.
    ftext = " ".join(f["text"] or "" for f in linked).lower().replace("–", "-")
    rule_10b5_1 = doc_10b5_1 or ("10b5-1" in ftext)

    rec = {
        "table": table,
        "security_title": _value(tx, "securityTitle"),
        "transaction_date": _value(tx, "transactionDate"),
        "code": code,
        "code_meaning": meaning,
        "discretionary": discretionary,
        "shares": _num(_value(tx.find("transactionAmounts"), "transactionShares")) if tx.find("transactionAmounts") is not None else None,
        "price": _num(_value(tx.find("transactionAmounts"), "transactionPricePerShare")) if tx.find("transactionAmounts") is not None else None,
        "price_low": None,   # reserved: weighted-avg/range lives in footnotes (later enrich)
        "price_high": None,
        "acquired_disposed": _value(tx.find("transactionAmounts"), "transactionAcquiredDisposedCode") if tx.find("transactionAmounts") is not None else None,
        "ownership": _value(tx.find("ownershipNature"), "directOrIndirectOwnership") if tx.find("ownershipNature") is not None else None,
        "shares_owned_after": _num(_value(tx.find("postTransactionAmounts"), "sharesOwnedFollowingTransaction")) if tx.find("postTransactionAmounts") is not None else None,
        "rule_10b5_1": rule_10b5_1,
        "footnotes": linked,
    }
    if table == "derivative":
        rec["conversion_or_exercise_price"] = _num(_value(tx, "conversionOrExercisePrice"))
        rec["exercise_date"] = _value(tx, "exerciseDate")
        rec["expiration_date"] = _value(tx, "expirationDate")
        und = tx.find("underlyingSecurity")
        rec["underlying_security_title"] = _value(und, "underlyingSecurityTitle") if und is not None else None
        rec["underlying_shares"] = _num(_value(und, "underlyingSecurityShares")) if und is not None else None
    return rec


def parse_form4(
    xml: bytes,
    *,
    source_url: str | None = None,
    filing_id: str | None = None,
    filed_at: str | None = None,
    fetched_at: str | None = None,
) -> list[dict]:
    """Parse one Form 4 XML into normalized per-(owner, transaction) records.

    Output matches the schema in docs/03. Metadata (source_url/filing_id/filed_at/
    fetched_at) is supplied by the producer so every record is independently verifiable
    against SEC — provenance built in, not asserted.
    """
    root = ET.fromstring(xml)

    document_type = _child(root, "documentType")  # "4" or "4/A"
    is_amendment = bool(document_type and document_type.endswith("/A"))
    doc_10b5_1 = _child(root, "aff10b5One") == "1"

    issuer_el = root.find("issuer")
    issuer = {
        "cik": _child(issuer_el, "issuerCik") if issuer_el is not None else None,
        "name": _child(issuer_el, "issuerName") if issuer_el is not None else None,
        "ticker": _clean_ticker(_child(issuer_el, "issuerTradingSymbol")) if issuer_el is not None else None,
    }

    footnotes = {
        f.get("id"): (f.text or "").strip()
        for f in root.iter("footnote")
        if f.get("id")
    }

    owners = _owners(root)

    transactions: list[dict] = []
    nd = root.find("nonDerivativeTable")
    if nd is not None:
        for tx in nd.findall("nonDerivativeTransaction"):
            transactions.append(_transaction(tx, "non_derivative", footnotes, doc_10b5_1))
    dv = root.find("derivativeTable")
    if dv is not None:
        for tx in dv.findall("derivativeTransaction"):
            transactions.append(_transaction(tx, "derivative", footnotes, doc_10b5_1))

    base = {
        "filing_id": filing_id,
        "filed_at": filed_at,
        "fetched_at": fetched_at,
        "source_url": source_url,
        "document_type": document_type,
        "is_amendment": is_amendment,
        "period_of_report": _child(root, "periodOfReport"),
        "issuer": issuer,
    }

    # One record per (owner, transaction). Single-owner is the common case.
    records: list[dict] = []
    for owner in owners or [None]:
        for tx in transactions:
            rec = dict(base)
            rec["insider"] = owner
            rec["transaction"] = tx
            records.append(rec)
    return records
