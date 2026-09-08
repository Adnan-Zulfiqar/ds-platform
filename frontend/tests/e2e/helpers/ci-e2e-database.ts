/**
 * Documents the GitHub Actions Playwright job database contract.
 *
 * Kept in one module so seed-harness tests and workflow verification steps
 * cannot drift from each other silently.
 */

export const CI_E2E_POSTGRES_DB = "droppilot_e2e";

export const CI_E2E_DATABASE_URL =
  "postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/droppilot_e2e";

export const CI_E2E_PYTHON = "python";

export const CI_ALEMBIC_HEAD_REVISION = "0032";
