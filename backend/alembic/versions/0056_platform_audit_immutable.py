"""Make the operator audit trail immutable in the database (Admin Control
Center phase 9, D-019).

Until now ``platform_admin_audit`` was append-only by convention: the
application has no update or delete path. This adds the guarantee where it
cannot be bypassed by a future code path or a hand-run statement through
the application's role: a trigger refuses ``UPDATE``, ``DELETE`` and
``TRUNCATE``.

One update is allowed, because the schema itself asks for it: the foreign
keys ``admin_id`` and ``target_tenant_id`` are ``ON DELETE SET NULL``, so
deleting an operator or a workspace row clears them. The trigger accepts an
update only when every other column is unchanged and those two either stay
the same or become NULL.

What this does not stop: a database superuser can disable the trigger.
That is recorded as a known limitation, not hidden.

``downgrade()`` drops the triggers and the function.

Revision ID: 0056
Revises: 0055
"""

from __future__ import annotations

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION platform_admin_audit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- NEW and OLD exist only for row-level UPDATE: check TG_OP first,
            -- in its own IF, since SQL does not promise to short-circuit AND.
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(NEW) - 'admin_id' - 'target_tenant_id')
                       = (to_jsonb(OLD) - 'admin_id' - 'target_tenant_id')
                   AND (NEW.admin_id IS NULL
                        OR NEW.admin_id IS NOT DISTINCT FROM OLD.admin_id)
                   AND (NEW.target_tenant_id IS NULL
                        OR NEW.target_tenant_id IS NOT DISTINCT FROM OLD.target_tenant_id)
                THEN
                    RETURN NEW;  -- ON DELETE SET NULL from a removed operator or workspace
                END IF;
            END IF;
            RAISE EXCEPTION 'platform_admin_audit is append-only (%)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER platform_admin_audit_no_change
        BEFORE UPDATE OR DELETE ON platform_admin_audit
        FOR EACH ROW EXECUTE FUNCTION platform_admin_audit_immutable();
        """
    )
    op.execute(
        """
        CREATE TRIGGER platform_admin_audit_no_truncate
        BEFORE TRUNCATE ON platform_admin_audit
        FOR EACH STATEMENT EXECUTE FUNCTION platform_admin_audit_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS platform_admin_audit_no_truncate ON platform_admin_audit")
    op.execute("DROP TRIGGER IF EXISTS platform_admin_audit_no_change ON platform_admin_audit")
    op.execute("DROP FUNCTION IF EXISTS platform_admin_audit_immutable()")
