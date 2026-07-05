"""FEEDFACE server — FastAPI + x402. Reads last-good snapshot only; stateless.

Serves two layers on one host (docs/02, docs/07):
  - machine layer: /health, /v1/meta (free), /v1/insider/* (x402-paid)
  - human layer: The Junkyard static pages (landing + provenance/processing/pricing)
"""
