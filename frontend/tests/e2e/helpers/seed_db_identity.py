"""Read database identity from stdin JSON — credentials never touch argv."""

from __future__ import annotations

import json
import sys

import sqlalchemy as sa


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"invalid stdin json: {exc}", file=sys.stderr)
        sys.exit(2)

    url = payload.get("databaseUrl")
    expected = payload.get("expectedDatabase")
    if not isinstance(url, str) or not url.strip():
        print("missing databaseUrl", file=sys.stderr)
        sys.exit(2)

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        current = connection.execute(sa.text("select current_database()")).scalar()

    if expected is not None and current != expected:
        print(
            f"database identity mismatch: connected {current!r}, expected {expected!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(current)


if __name__ == "__main__":
    main()
