from __future__ import annotations

import argparse
import json
import secrets
import string
import subprocess
import sys
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
ALEMBIC = BACKEND / ".venv" / "Scripts" / "alembic.exe"


def random_token(length: int = 16) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_alembic(db_name: str) -> None:
    import os

    os.environ["POSTGRES_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PORT"] = "5432"
    os.environ["POSTGRES_USER"] = "droppilot"
    os.environ["POSTGRES_PASSWORD"] = "droppilot"
    os.environ["POSTGRES_DB"] = db_name
    subprocess.run([str(ALEMBIC), "upgrade", "head"], cwd=BACKEND, check=True)


def grant_reviewer_scoped_write(cur: psycopg.Cursor, role_name: str) -> None:
    cur.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{role_name}"')
    cur.execute(f'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO "{role_name}"')
    cur.execute(
        f'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO "{role_name}"'
    )
    cur.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "{role_name}"'
    )
    cur.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "{role_name}"'
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--mode", choices=["reviewer", "cursor"], required=True)
    parser.add_argument("--secrets-dir", required=True)
    args = parser.parse_args()

    if args.mode == "reviewer":
        db_name = f"droppilot_uxl2b_r7_review_{args.timestamp}"
        role_name = f"uxl2b_r7_reviewer_{random_token(8)}"
        redis_cache, redis_session, redis_rate = 7, 8, 10
        env_filename = "ux-l2b-r7-review.env"
        backend_port, frontend_port = 8128, 3128
    else:
        db_name = f"droppilot_uxl2b_r7_{args.timestamp}"
        role_name = None
        redis_cache, redis_session, redis_rate = 13, 14, 15
        env_filename = None
        backend_port, frontend_port = 8127, 3127

    role_password = secrets.token_urlsafe(24)

    admin = psycopg.connect("postgresql://droppilot:droppilot@127.0.0.1:5432/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{db_name}"')

        if role_name:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
            if not cur.fetchone():
                escaped_password = role_password.replace("'", "''")
                cur.execute(
                    f'CREATE ROLE "{role_name}" LOGIN PASSWORD \'{escaped_password}\' '
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                )
            cur.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{role_name}"')
    admin.close()

    run_alembic(db_name)

    with psycopg.connect(
        f"postgresql://droppilot:droppilot@127.0.0.1:5432/{db_name}"
    ) as scoped:
        scoped.autocommit = True
        with scoped.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            version = cur.fetchone()[0]
            if version != "0044":
                raise SystemExit(f"expected alembic 0034, got {version}")
            if role_name:
                grant_reviewer_scoped_write(cur, role_name)

    if role_name:
        database_url = (
            f"postgresql+psycopg://{role_name}:{role_password}@127.0.0.1:5432/{db_name}"
        )
    else:
        database_url = f"postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/{db_name}"

    metadata = {
        "db_name": db_name,
        "role_name": role_name,
        "redis_cache_db": redis_cache,
        "redis_session_db": redis_session,
        "redis_rate_limit_db": redis_rate,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "alembic_version": "0044",
    }

    secrets_dir = Path(args.secrets_dir)
    secrets_dir.mkdir(parents=True, exist_ok=True)

    if env_filename:
        app_secret = secrets.token_urlsafe(32)
        # Generated fresh per run rather than a fixed published key: this stack
        # is torn down at the end of the review, so nothing needs to decrypt
        # data written by a previous run.
        encryption_key = Fernet.generate_key().decode()
        google_id = f"synthetic-r7-review-{random_token(6)}.apps.googleusercontent.com"
        lines = [
            "ENVIRONMENT=local",
            "POSTGRES_HOST=127.0.0.1",
            "POSTGRES_PORT=5432",
            f"POSTGRES_USER={role_name}",
            f"POSTGRES_PASSWORD={role_password}",
            f"POSTGRES_DB={db_name}",
            "REDIS_HOST=127.0.0.1",
            "REDIS_PORT=6379",
            f"REDIS_CACHE_DB={redis_cache}",
            f"REDIS_SESSION_DB={redis_session}",
            f"REDIS_RATE_LIMIT_DB={redis_rate}",
            f"SECURITY_SECRET_KEY={app_secret}",
            "SECURITY_COOKIE_SECURE=false",
            "SECURITY_RATE_LIMIT_ENABLED=false",
            f"SECURITY_ENCRYPTION_KEYS={encryption_key}",
            f"CORS_ORIGINS=http://127.0.0.1:{frontend_port}",
            "SHOPIFY_API_KEY=synthetic-r7-review-shopify-client-id",
            "SHOPIFY_API_SECRET=synthetic-r7-review-shopify-client-secret-not-real",
            f"SHOPIFY_CALLBACK_URL=http://127.0.0.1:{backend_port}/api/v1/integrations/shopify/callback",
            f"SHOPIFY_FRONTEND_RETURN_URL=http://127.0.0.1:{frontend_port}/settings/integrations",
            f"GOOGLE_OAUTH_CLIENT_ID={google_id}",
            "EBAY_CLIENT_ID=DropPilo-CiOnlyNo-PRD-000000000-00000000",
            "EBAY_CLIENT_SECRET=PRD-ci-only-not-a-real-ebay-certificate-id",
            "EBAY_REDIRECT_URI_NAME=DropPilot-CiOnly-RuName",
            "LOG_JSON_OUTPUT=true",
            f"E2E_DATABASE_URL={database_url}",
            f"E2E_BASE_URL=http://127.0.0.1:{frontend_port}",
            f"NEXT_PUBLIC_API_URL=http://127.0.0.1:{backend_port}",
            "NEXT_PUBLIC_ENVIRONMENT=local",
            f"NEXT_PUBLIC_GOOGLE_CLIENT_ID={google_id}",
            f"E2E_PYTHON={PYTHON}",
        ]
        env_path = secrets_dir / env_filename
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        metadata["env_path"] = str(env_path)

    private_meta = secrets_dir / f"ux-l2b-r7-{args.mode}-private.json"
    private_meta.write_text(
        json.dumps(
            {
                **metadata,
                "database_url": database_url,
            }
        ),
        encoding="utf-8",
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
