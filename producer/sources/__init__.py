"""New-source producer modules (the source-collection validation notes).

Each module implements the contract documented in producer/runner.py: SOURCE_ID, LABEL,
client(), fetch_new(state, c). producer/main.py's REGISTRY list wires them into the poll loop.
"""
