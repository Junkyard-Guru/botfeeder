"""FEEDFACE producer — polls EDGAR, parses Form 4, writes snapshot + archive.

Decoupled from serving (docs/02-architecture.md): this side PRODUCES; the server
only reads last-good. "Never charge for a bad response" starts here — on any
failure, keep the prior snapshot and log; the server keeps serving slightly-stale
data rather than erroring on a paid call.
"""
