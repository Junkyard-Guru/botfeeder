"""The maker's mark — our operating ethos as a compact glyph plus its plain-language decode.

Single source of truth so the seal and its principles never drift across surfaces (storefront
footer, /v1/meta, /llms.txt, outbound webhooks). The glyph is a formal statement of positive-sum,
generous reciprocity; the decode makes it machine-actionable, so a buying agent can read our
principles rather than just see a sigil it can't act on.

Provenance: authored by the operator as The Junkyard's founding ethic (the "Principles Glyph").
"""
from __future__ import annotations

# The full seal, as authored. Compact renderable unicode — rides into every surface as text.
ETHOS_GLYPH = (
    "∂Vᵢ/∂Vⱼ > 0\n"
    "Vᵢ > V̂ᵢ ,   Vⱼ ≠ V̂ⱼ\n"
    "aᵢ(0) > 0\n"
    "aⱼ ≥ 0  ↦  aᵢ = aⱼ + β\n"
    "aⱼ < 0  ↦  aᵢ : P̂ⱼ(aⱼ′ < 0 | aᵢ) ≤ τ\n"
    "P(aᵢ > 0) ≥ φ\n"
    "P̂ⱼ = P̂( · | rⱼ ) ,   rⱼ ⟵ aⱼ"
)

# The HEADLINE line, for compact spots (webhook footer): our value rises only with yours.
# (The glyph's keystone is line 4, aᵢ = aⱼ + β — the Reciprocity Plus Beta line.)
ETHOS_GLYPH_INLINE = "∂Vᵢ/∂Vⱼ > 0"

# Machine-actionable decode: each line of the seal in plain language, so an agent can weigh our
# principles as part of its trust decision, not just render a sigil.
PRINCIPLES = [
    {"expr": "∂Vᵢ/∂Vⱼ > 0",
     "principle": "Positive-sum: our value rises only when yours does."},
    {"expr": "Vᵢ > V̂ᵢ , Vⱼ ≠ V̂ⱼ",
     "principle": "Over-deliver against the estimate — realized value beats the quote."},
    {"expr": "aᵢ(0) > 0",
     "principle": "Open in good faith — we act first, positively (the free sample)."},
    {"expr": "aⱼ ≥ 0 ↦ aᵢ = aⱼ + β",
     "principle": "Meet cooperation and add a surplus on top."},
    {"expr": "aⱼ < 0 ↦ aᵢ : P̂ⱼ(aⱼ′<0 | aᵢ) ≤ τ",
     "principle": "Answer bad behavior with correction, not revenge."},
    {"expr": "P(aᵢ > 0) ≥ φ",
     "principle": "Be reliably good, not occasionally."},
    {"expr": "P̂ⱼ = P̂(·|rⱼ), rⱼ ⟵ aⱼ",
     "principle": "Judged by deeds — reputation is earned from the record, and ours is auditable."},
]

# What the seal is, in one sentence — for surfaces that want a label next to the glyph.
ETHOS_SUMMARY = ("The Junkyard's maker's mark: a formal ethic of positive-sum, generous "
                 "reciprocity — we win only when you do.")
