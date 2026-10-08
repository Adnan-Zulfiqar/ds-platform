"""Create a platform operator account (Track E5, decision D-015).

The only way a platform admin comes into existence. Run it on the server,
inside the backend container:

    docker exec -it -e PYTHONPATH=/app droppilot-backend-1 \
        python scripts/create_platform_admin.py --email you@example.com

``PYTHONPATH=/app`` is required: run as a file, the script's own folder is on
the path, not the application, and ``import app`` fails.

The password is read from the terminal (twice) and never echoed. The TOTP
secret is printed **once**, as an ``otpauth://`` URI to add to an
authenticator app. It is stored encrypted and cannot be shown again. Lose it
and the account must be recreated.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.exceptions import AppError
from app.database.session import transaction
from app.services.platform_admin import PlatformAdminService


async def _create(email: str, password: str) -> str:
    async with transaction() as session:
        created = await PlatformAdminService(session).create_admin(email=email, password=password)
        return created.provisioning_uri


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        uri = asyncio.run(_create(args.email, password))
    except AppError as exc:
        print(f"Not created: {exc.message}", file=sys.stderr)
        return 1
    print("Platform admin created.")
    print("Add this to your authenticator app now. It will not be shown again:")
    print(uri)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
