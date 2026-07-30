"""Tests for the role-based authorization dependencies.

Exercised directly rather than through HTTP: the dependency is the security
boundary, so testing it in isolation proves the decision logic independently of
whether an endpoint remembered to depend on it.
"""

from __future__ import annotations

import uuid

import pytest

from app.api.deps import require_minimum_role, require_roles
from app.core.context import AuthenticatedUser
from app.core.exceptions import PermissionDeniedError
from app.models.role import RoleName

pytestmark = pytest.mark.unit


def principal_with(*roles: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="user@example.com",
        roles=frozenset(roles),
    )


class TestRequireRoles:
    def test_admits_an_exact_match(self) -> None:
        check = require_roles(RoleName.ADMIN)
        principal = principal_with("admin")
        assert check(principal) is principal

    def test_admits_when_one_of_several_roles_matches(self) -> None:
        check = require_roles(RoleName.ADMIN, RoleName.OWNER)
        assert check(principal_with("owner")) is not None

    def test_denies_when_no_role_matches(self) -> None:
        check = require_roles(RoleName.ADMIN)
        with pytest.raises(PermissionDeniedError):
            check(principal_with("member"))

    def test_denies_a_principal_with_no_roles(self) -> None:
        check = require_roles(RoleName.VIEWER)
        with pytest.raises(PermissionDeniedError):
            check(principal_with())

    def test_does_not_admit_a_higher_role_implicitly(self) -> None:
        """`require_roles` is an exact set test, not a hierarchy test.

        Use `require_minimum_role` when an owner should satisfy an admin
        requirement. Conflating the two is how an endpoint ends up accidentally
        excluding the account owner.
        """
        check = require_roles(RoleName.ADMIN)
        with pytest.raises(PermissionDeniedError):
            check(principal_with("owner"))


class TestRequireMinimumRole:
    @pytest.mark.parametrize("held", ["admin", "owner"])
    def test_admits_the_threshold_and_above(self, held: str) -> None:
        check = require_minimum_role(RoleName.ADMIN)
        assert check(principal_with(held)) is not None

    @pytest.mark.parametrize("held", ["member", "viewer"])
    def test_denies_below_the_threshold(self, held: str) -> None:
        check = require_minimum_role(RoleName.ADMIN)
        with pytest.raises(PermissionDeniedError):
            check(principal_with(held))

    def test_uses_the_highest_role_held(self) -> None:
        check = require_minimum_role(RoleName.ADMIN)
        assert check(principal_with("viewer", "owner")) is not None

    def test_ignores_an_unrecognised_role(self) -> None:
        """A renamed or removed role must degrade to denial, not a 500."""
        check = require_minimum_role(RoleName.MEMBER)
        with pytest.raises(PermissionDeniedError):
            check(principal_with("some-role-that-no-longer-exists"))

    def test_unrecognised_role_does_not_mask_a_valid_one(self) -> None:
        check = require_minimum_role(RoleName.MEMBER)
        assert check(principal_with("bogus", "admin")) is not None


class TestRoleHierarchy:
    def test_ranks_are_strictly_ordered(self) -> None:
        assert (
            RoleName.VIEWER.rank < RoleName.MEMBER.rank < RoleName.ADMIN.rank < RoleName.OWNER.rank
        )

    def test_every_role_has_a_rank(self) -> None:
        """A role added without a rank would raise at authorization time."""
        for role in RoleName:
            assert isinstance(role.rank, int)


class TestPrincipal:
    def test_repr_omits_the_email_address(self) -> None:
        """This repr reaches logs and exception output."""
        principal = principal_with("admin")
        assert "user@example.com" not in repr(principal)

    def test_has_role_and_has_any_role(self) -> None:
        principal = principal_with("admin", "viewer")
        assert principal.has_role("admin")
        assert not principal.has_role("owner")
        assert principal.has_any_role("owner", "viewer")
        assert not principal.has_any_role("owner", "member")

    def test_is_immutable(self) -> None:
        """Identity must not be reassignable mid-request."""
        principal = principal_with("member")
        with pytest.raises((AttributeError, TypeError)):
            principal.tenant_id = uuid.uuid4()  # type: ignore[misc]
