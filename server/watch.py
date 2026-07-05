"""Watch / retainer pricing. Spec: docs/09.

A retainer is a prepaid window: monthly = base (fixed per-sub overhead) + per-entity (variable
watching cost, scales with value). Longer prepay terms carry progressive discounts (upfront cash +
lock-in). Storage lives in watch_store.py; the x402 settlement of `term_price` happens in the
subscribe route (server/payments.py does the actual crypto).
"""
from __future__ import annotations

import os

WATCH_BASE_USD = float(os.environ.get("FEEDFACE_WATCH_BASE_USD", "2.00"))      # per 30d, fixed overhead
WATCH_ENTITY_USD = float(os.environ.get("FEEDFACE_WATCH_ENTITY_USD", "0.40"))  # per watched CIK / 30d

# Prepay term (months) -> discount fraction. Longer commitment, deeper discount.
TERM_DISCOUNTS = {1: 0.00, 3: 0.05, 6: 0.15, 12: 0.35}


def monthly_price(n_entities: int) -> float:
    if n_entities < 1:
        raise ValueError("a watchlist needs at least one entity")
    return WATCH_BASE_USD + n_entities * WATCH_ENTITY_USD


def term_price(n_entities: int, months: int) -> float:
    """Total prepaid price for n entities over `months`, after the term discount. USDC has 6 decimals."""
    if months not in TERM_DISCOUNTS:
        raise ValueError(f"unsupported term {months}; allowed: {sorted(TERM_DISCOUNTS)}")
    gross = monthly_price(n_entities) * months
    return round(gross * (1.0 - TERM_DISCOUNTS[months]), 6)


def quote(n_entities: int, months: int) -> dict:
    """A buyer-facing price quote for a watchlist size + term."""
    return {
        "entities": n_entities,
        "months": months,
        "monthly_usd": round(monthly_price(n_entities), 6),
        "discount": TERM_DISCOUNTS[months],
        "price_usd": term_price(n_entities, months),
        "currency": "USDC",
    }


def term_table(n_entities: int = 5) -> list[dict]:
    """The published term ladder (for /v1/meta), illustrated at a sample watchlist size."""
    return [quote(n_entities, m) for m in sorted(TERM_DISCOUNTS)]
