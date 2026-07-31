#!/usr/bin/env python
"""Live AliExpress integration verification.

Walks the OAuth flow and makes one read-only API call, so the parts that cannot
be proven with mocks can be proven against the real gateway.

**Credentials are read from the environment and never printed.** Nothing here
accepts a secret as a command-line argument either, because arguments appear in
shell history and in the process list of every other user on the machine.

    ALIEXPRESS_APP_KEY=...       required
    ALIEXPRESS_APP_SECRET=...    required
    ALIEXPRESS_REDIRECT_URI=...  must match the developer console exactly

Usage
-----

    # 1. Check configuration and print the authorization URL.
    python scripts/verify_aliexpress.py authorize

    # 2. Visit the URL, approve, and copy the `code` from the redirect.
    #    Then exchange it (the code is single-use and expires quickly):
    ALIEXPRESS_AUTH_CODE=... python scripts/verify_aliexpress.py exchange

    # 3. With the access token from step 2, make a read-only call:
    ALIEXPRESS_ACCESS_TOKEN=... python scripts/verify_aliexpress.py call

    # Or check configuration without contacting AliExpress at all:
    python scripts/verify_aliexpress.py preflight

Nothing this script does touches the database. It exercises the client and
signing code directly, so a failure points at the integration rather than at
application state.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.integrations.aliexpress.auth import (
    build_authorization_url,
    resolve_token_expiry,
    signing_path_for,
)
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import AliExpressError

VERIFICATION_TENANT = "00000000-0000-4000-8000-000000000000"


def mask(value: str | None) -> str:
    """Render a credential so its presence is confirmable but its value is not."""
    if not value:
        return "(not set)"
    if len(value) <= 4:
        return "•" * 4
    return f"{'•' * 8}{value[-4:]}"


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"  ✗ {name} is not set.")
        sys.exit(2)
    return value


def platform_credentials() -> tuple[str, str]:
    """Read the application credentials through the settings object.

    Deliberately not ``os.environ``: the point of the audit is that credentials
    reach the code through one validated path. A script that read the
    environment directly could pass while the application itself was
    misconfigured.
    """
    key = settings.aliexpress.app_key.strip()
    secret = settings.aliexpress.app_secret
    return key, (secret.get_secret_value() if secret else "")


def preflight() -> tuple[str, str]:
    """Report configuration without contacting AliExpress."""
    print("Configuration  (loaded via app.core.config, not os.environ)")
    print("-" * 60)

    app_key, app_secret = platform_credentials()

    print(f"  app key           : {mask(app_key)}")
    print(f"  app secret        : {mask(app_secret)}")
    print(f"  authorize url     : {settings.aliexpress.authorize_url}")
    print(f"  token url         : {settings.aliexpress.token_url}")
    print(f"  refresh url       : {settings.aliexpress.refresh_url}")
    print(f"  api base url      : {settings.aliexpress.api_base_url}")
    print(f"  callback url      : {settings.aliexpress.callback_url}")
    print(f"  app environment   : {settings.aliexpress.environment}")
    print()
    print("Derived signing paths")
    print("-" * 60)
    # The rule corrected in Phase 3.5: REST-style endpoints prefix the path,
    # minus the /rest routing segment; the TOP gateway prefixes nothing.
    for label, url in (
        ("token", settings.aliexpress.token_url),
        ("refresh", settings.aliexpress.refresh_url),
        ("api", settings.aliexpress.api_base_url),
    ):
        derived = signing_path_for(url) or "(none — TOP style)"
        print(f"  {label:<17} : {derived}")
    print()

    if not app_key or not app_secret:
        print("  ✗ Credentials missing. Set ALIEXPRESS_APP_KEY and")
        print("    ALIEXPRESS_APP_SECRET, then re-run.")
        sys.exit(2)

    print("  ✓ Configuration looks complete.")
    return app_key, app_secret


def authorize() -> None:
    app_key, _ = preflight()

    # A fixed, obviously-inert state. This script is not the application: it has
    # no session to protect, and using a random value would only mean the user
    # cannot see what was sent.
    url = build_authorization_url(app_key=app_key, state="verification-run")

    print()
    print("Open this URL, approve access, then copy the `code` parameter from")
    print("the address bar of the page you land on:")
    print()
    print(f"  {url}")
    print()
    print("Then run:  ALIEXPRESS_AUTH_CODE=<code> python scripts/verify_aliexpress.py exchange")
    print()
    print("Note: the redirect target must be reachable, or the browser will")
    print("show an error *after* AliExpress has already issued the code. The")
    print("code is still in the URL and still usable.")


async def _exchange() -> None:
    app_key, app_secret = platform_credentials()
    if not app_key or not app_secret:
        print("  ✗ ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET are not configured.")
        sys.exit(2)
    code = require("ALIEXPRESS_AUTH_CODE")

    client = AliExpressClient(app_key=app_key, app_secret=app_secret, tenant_id=VERIFICATION_TENANT)

    print("Exchanging the authorization code...")
    print(f"  endpoint     : {settings.aliexpress.token_url}")
    print(f"  signing path : {signing_path_for(settings.aliexpress.token_url) or '(none)'}")
    print()

    payload: dict[str, Any] = await client.exchange_token(
        settings.aliexpress.token_url,
        {
            "code": code,
            "grant_type": "authorization_code",
            "need_refresh_token": "true",
            "redirect_uri": settings.aliexpress.callback_url,
        },
    )

    # Report the *shape* of the response, not its contents. Field names are what
    # matter for verification; the token itself must not reach a terminal, a
    # scrollback buffer, or a screen recording.
    print("  ✓ Token exchange succeeded.")
    print(f"  response keys : {sorted(payload.keys())}")

    access = payload.get("access_token") or payload.get("data", {}).get("access_token")
    refresh = payload.get("refresh_token") or payload.get("data", {}).get("refresh_token")
    expires_in = payload.get("expires_in") or payload.get("data", {}).get("expires_in")
    expire_time = payload.get("expire_time") or payload.get("data", {}).get("expire_time")

    print(f"  access token  : {mask(access)}")
    print(f"  refresh token : {mask(refresh)}")
    print(f"  expires_in    : {expires_in}")
    print(f"  expire_time   : {expire_time}")
    print(
        f"  resolved expiry: {resolve_token_expiry(expires_in=expires_in, expire_time=expire_time)}"
    )
    print()
    print("Next:  ALIEXPRESS_ACCESS_TOKEN=<token> python scripts/verify_aliexpress.py call")


async def _call() -> None:
    app_key, app_secret = platform_credentials()
    if not app_key or not app_secret:
        print("  ✗ ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET are not configured.")
        sys.exit(2)
    access_token = require("ALIEXPRESS_ACCESS_TOKEN")

    # A read-only method from the dropship permission group. Overridable, since
    # which methods an application may call depends on its approved groups.
    method = os.environ.get("ALIEXPRESS_TEST_METHOD", "aliexpress.ds.category.get")

    client = AliExpressClient(
        app_key=app_key,
        app_secret=app_secret,
        tenant_id=VERIFICATION_TENANT,
        access_token=access_token,
    )

    print(f"Calling {method} (read-only)...")
    payload = await client.call(method, {})

    print("  ✓ Call succeeded.")
    print(f"  response keys : {sorted(payload.keys())}")
    # Truncated: a category list can be enormous, and the point is to prove the
    # call round-trips, not to dump the catalogue.
    rendered = str(payload)
    print(f"  payload (first 600 chars):\n{rendered[:600]}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "preflight"

    try:
        if command == "preflight":
            preflight()
        elif command == "authorize":
            authorize()
        elif command == "exchange":
            asyncio.run(_exchange())
        elif command == "call":
            asyncio.run(_call())
        else:
            print(f"Unknown command: {command}")
            print("Use one of: preflight, authorize, exchange, call")
            sys.exit(1)
    except AliExpressError as exc:
        # The typed error carries the upstream code, which is the useful part.
        print()
        print(f"  ✗ {exc.code}: {exc.message}")
        if exc.details:
            print(f"    details: {exc.details}")
        sys.exit(1)


if __name__ == "__main__":
    main()
