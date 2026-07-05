"""CLI: print the volume/revenue summary from volume_store.

Run on the VPS where volume.db actually accumulates data:
    cd /opt/feedface && .venv/bin/python -m server.volume_report

Deliberately a local script, not an HTTP endpoint — the summary includes payer addresses and
exact revenue, which don't belong on a public route.
"""
from __future__ import annotations

import json
import sys

from . import volume_store


def main() -> None:
    report = volume_store.summary()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
        return

    print(f"Window: {report['window']['first_call'] or '(no calls logged yet)'} "
          f"-> {report['window']['last_call'] or '-'}")
    print(f"Total priced calls: {report['total_calls']}  "
          f"(settled: {report['settled_calls']}, unpaid 402: {report['unpaid_402_calls']})")
    print(f"Conversion rate: {report['conversion_rate']:.1%}")
    print(f"Revenue: ${report['revenue_usd']:.4f} USDC")
    print()
    print("By endpoint:")
    for row in report["by_endpoint"]:
        print(f"  {row['endpoint']:<16} settled={row['settled_n'] or 0:<4} "
              f"402s={row['unpaid_402_n'] or 0:<4} revenue=${(row['revenue_usd'] or 0):.4f}")
    print()
    print("By network/outcome:")
    for row in report["by_network_outcome"]:
        print(f"  {row['network']:<14} {row['outcome']:<8} n={row['n']}")
    if report["recent_sales"]:
        print()
        print("Most recent settled sales:")
        for row in report["recent_sales"]:
            print(f"  {row['ts']}  {row['endpoint']:<14} ${row['price_usd']:.4f}  "
                  f"payer={row['payer']}  tx={row['tx']}")


if __name__ == "__main__":
    main()
