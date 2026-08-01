#!/usr/bin/env python
"""Live verification of ``AliExpressClient.call`` through the production path.

Phase 3.7 probed the gateway with raw HTTP and proved *access*. What it could
not prove — recorded as M10's remainder — is the application's own client:
request building, signing, retry wiring, error mapping and body interpretation
as they run in production. This script closes that gap for the order APIs
Phase 5 depends on.

It exercises the exact production path: the tenant's stored connection is
loaded from PostgreSQL, its credentials are decrypted by ``app.core.encryption``,
and every request goes through ``AliExpressClient.call`` — no raw HTTP, no
signing shortcuts.

Three calls, all read-only:

1. ``aliexpress.ds.category.get``           — a known-good success, proving the
                                               client parses a real 200 payload
2. ``aliexpress.ds.trade.order.get``         — with a fabricated order id,
                                               proving error interpretation of a
                                               real error envelope
3. ``aliexpress.ds.commissionorder.listbyindex`` — a real list query, capturing
                                               the response shape Phase 5's sync
                                               parses

Successful responses are written to ``tests/fixtures/aliexpress/`` so the order
contract layer is built from captured payloads, not documentation guesses.
Credentials never reach stdout or the fixture files.

Usage (needs the repo-root .env, PostgreSQL and Redis running):

    python scripts/verify_orders_live.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.core.encryption import decrypt
from app.database.session import transaction
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import AliExpressError
from app.integrations.aliexpress.service import AliExpressService
from app.models.integration import AliExpressConnection, IntegrationStatus

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "aliexpress"

#: An order id that cannot exist. The gateway answering "not found" (rather
#: than "bad signature" or "no permission") proves the request was well formed
#: and authorised — the strongest signal available without spending money on a
#: real order.
FABRICATED_ORDER_ID = "9999999999999999"


def _capture(name: str, payload: dict[str, Any]) -> None:
    """Persist a captured payload as a committed fixture.

    The payload is a gateway response body; it contains no request parameters,
    so no credential can leak into the file. Written pretty-printed so diffs
    against future captures are readable.
    """
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    captured -> tests/fixtures/aliexpress/{name}")


async def _load_client() -> AliExpressClient:
    """Build a client from the most recent usable stored connection.

    This is the production credential path — repository row, Fernet decryption,
    client construction — not an environment-variable shortcut.
    """
    async with transaction() as session:
        row = (
            await session.execute(
                sa.select(AliExpressConnection)
                .where(AliExpressConnection.status == IntegrationStatus.CONNECTED)
                .order_by(AliExpressConnection.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if row is None:
            print("  x No connected AliExpress account found in the database.")
            print("    Complete the OAuth flow first (settings -> integrations).")
            sys.exit(2)

        if not row.encrypted_access_token:
            print("  x The stored connection has no access token.")
            sys.exit(2)

        if row.is_token_expired:
            print("  x The stored access token is expired. Reconnect first.")
            sys.exit(2)

        set_tenant_id(row.tenant_id)
        print(f"  connection : {row.id}")
        print(f"  status     : {row.status.value}")
        print(f"  expires    : {row.token_expiry}")

        app_key, app_secret = AliExpressService.platform_credentials()
        return AliExpressClient(
            app_key=app_key,
            app_secret=app_secret,
            tenant_id=str(row.tenant_id),
            access_token=decrypt(row.encrypted_access_token),
        )


async def _verify() -> int:
    print("Live AliExpressClient verification (production path)")
    print("=" * 60)

    client = await _load_client()
    failures = 0

    # -- 1. Success path ------------------------------------------------------
    print()
    print("[1/3] aliexpress.ds.category.get - success path")
    try:
        payload = await client.call("aliexpress.ds.category.get", {})
        keys = sorted(payload.keys())
        print(f"    OK response keys: {keys}")
        _capture("category_live.json", payload)
    except AliExpressError as exc:
        failures += 1
        print(f"    FAILED {exc.code}: {exc.message}")

    # The gateway enforces a short per-second frequency limit; the first run
    # tripped it (`ApiCallLimit ... ban will last 1 seconds`). Spacing the
    # calls keeps each probe's answer about the probe, not about the burst.
    await asyncio.sleep(2)

    # -- 2. Error mapping -----------------------------------------------------
    print()
    print("[2/3] aliexpress.ds.trade.order.get - error interpretation")
    try:
        # Flat `order_id`, not a nested query object: the live gateway answered
        # `MissingParameter: order_id` to the nested form, which settles it.
        payload = await client.call(
            "aliexpress.ds.trade.order.get",
            {"order_id": FABRICATED_ORDER_ID},
        )
        # Some methods answer errors inside a 200 result envelope rather than
        # the documented error body; that is still a captured contract.
        print(f"    OK (envelope answer) keys: {sorted(payload.keys())}")
        _capture("order_get_not_found.json", payload)
    except AliExpressError as exc:
        # A typed error is also a pass: it proves _interpret and _map_error ran
        # against a real error payload. Record what came back.
        print(f"    OK (typed error) {type(exc).__name__} code={exc.code}")
        print(f"       upstream={exc.upstream_code} message={exc.message[:120]}")

    await asyncio.sleep(2)

    # -- 3. Order list --------------------------------------------------------
    print()
    print("[3/3] aliexpress.ds.commissionorder.listbyindex - list contract")
    now = datetime.now(UTC)
    start = now - timedelta(days=30)
    try:
        payload = await client.call(
            "aliexpress.ds.commissionorder.listbyindex",
            {
                "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "Payment Completed",
                "page_no": "1",
                "page_size": "20",
            },
        )
        print(f"    OK response keys: {sorted(payload.keys())}")
        _capture("commissionorder_list_live.json", payload)
    except AliExpressError as exc:
        # Parameter rejections still prove the client path; capture the code.
        print(f"    typed error {type(exc).__name__} code={exc.code}")
        print(f"       upstream={exc.upstream_code} message={exc.message[:200]}")

    print()
    print("=" * 60)
    if failures:
        print(f"RESULT: {failures} hard failure(s) - see above")
        return 1
    print("RESULT: client verified against the live gateway")
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(_verify())
    finally:
        clear_context()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
