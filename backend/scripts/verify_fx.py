#!/usr/bin/env python
"""Live FX verification (Open Exchange Rates).

Reads ``FX_*`` from the environment. Never prints the API key.

    FX_PROVIDER=openexchangerates
    FX_API_KEY=...

Usage:

    python scripts/verify_fx.py
    python scripts/verify_fx.py --pairs GBP/USD,USD/GBP,CNY/GBP

Skips cleanly when production FX config is absent.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


async def _main(pairs: list[tuple[str, str]]) -> int:
    from app.core.config import get_settings
    from app.services.fx.service import get_fx_service

    settings = get_settings()
    provider = (settings.fx.provider or "").strip().lower()
    key = settings.fx.api_key.get_secret_value() if settings.fx.api_key else ""

    if provider not in {"openexchangerates", "oer"} or not key:
        print("SKIP: production FX config absent (FX_PROVIDER=openexchangerates + FX_API_KEY).")
        return 0

    # Clear lru_cache so this process picks up env.
    get_fx_service.cache_clear()
    fx = get_fx_service()
    print(f"provider={fx.provider_name}")
    print(f"freshness_seconds={settings.fx.cache_ttl_seconds}")
    print(f"max_staleness_seconds={settings.fx.max_staleness_seconds}")
    print("---")

    exit_code = 0
    for base, quote in pairs:
        try:
            row = await fx.get_rate(base, quote)
        except Exception as exc:
            print(f"{base}/{quote}: ERROR {type(exc).__name__}: {exc}")
            exit_code = 1
            continue
        if row is None:
            print(f"{base}/{quote}: UNAVAILABLE")
            exit_code = 1
            continue
        print(
            f"{base}/{quote}: rate={row.rate} "
            f"provider_timestamp={row.provider_timestamp} "
            f"fetched_at={row.fetched_at} "
            f"status={row.status.value} "
            f"derivation={row.derivation}"
        )
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        default="GBP/USD,USD/GBP,CNY/GBP",
        help="Comma-separated BASE/QUOTE pairs",
    )
    args = parser.parse_args()
    pairs: list[tuple[str, str]] = []
    for item in args.pairs.split(","):
        item = item.strip().upper()
        if not item:
            continue
        if "/" not in item:
            print(f"Invalid pair: {item}", file=sys.stderr)
            sys.exit(2)
        base, quote = item.split("/", 1)
        pairs.append((base.strip(), quote.strip()))

    # Prefer backend/.env via pydantic settings when cwd is repo root.
    os.chdir(BACKEND_ROOT)
    raise SystemExit(asyncio.run(_main(pairs)))


if __name__ == "__main__":
    main()
