"""BACKUP-B1-R1 — protected production database names, compared as policy.

The accepted BACKUP-B1 review left one informational finding: the protected
name set was matched with exact, case-sensitive equality, so a database called
`DropPilot` walked past every guard that `droppilot` was stopped by. That is a
protection you can step over by holding shift.

**Two comparisons, and these tests exist mostly to keep them apart.**

*Policy* — "is this one of the protected names?" — is case-insensitive and
tolerates surrounding whitespace, because its job is to be impossible to slip
past by capitalisation.

*Identity* — "is the server I am connected to the one I was told to expect?" —
stays exact, because two PostgreSQL databases whose names differ only in case
are two different databases, and a tool that picked one on the operator's
behalf would write to a database nobody named.

Loosening the second in the course of loosening the first would be the
plausible mistake here, so it is asserted directly rather than assumed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.core import database_identity
from app.core.backup_crypto import BackupKey, load_backup_key
from app.core.database_identity import (
    MAX_DATABASE_NAME_BYTES,
    DatabaseIdentityError,
    DatabaseNameError,
    canonical_database_name,
    is_protected_database,
    require_database_name,
    safe_label,
    validate_database_name,
)
from app.services import database_backup
from app.services.database_backup import (
    ConfigurationError,
    ExitCode,
    PostgresTarget,
    create_backup,
    restore_backup,
)

pytestmark = pytest.mark.unit

SECRET_PASSWORD = "correct-horse-battery-staple-7f3a9c"
FAKE_DUMP = b"PGDMP" + bytes(range(256)) * 40

#: Confusable spellings, written as escapes. A test file about homoglyphs that
#: contained literal homoglyphs would be unreviewable — nobody can tell a
#: Cyrillic "o" from a Latin one by looking, which is the entire problem.
CYRILLIC_O = chr(0x043E)  # CYRILLIC SMALL LETTER O
DOTLESS_I = chr(0x0131)  # LATIN SMALL LETTER DOTLESS I
SHARP_S = chr(0x1E9E)  # LATIN CAPITAL LETTER SHARP S
DOTTED_I = chr(0x0130)  # LATIN CAPITAL LETTER I WITH DOT ABOVE
RTL_OVERRIDE = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
ZERO_WIDTH_SPACE = chr(0x200B)  # ZERO WIDTH SPACE
#: Fullwidth d r o p p i l o t, U+FF44 onwards.
FULLWIDTH_DROPPILOT = "".join(
    chr(code) for code in (0xFF44, 0xFF52, 0xFF4F, 0xFF50, 0xFF50, 0xFF49, 0xFF4C, 0xFF4F, 0xFF54)
)

#: Every spelling of the production name that must be refused. The point of the
#: list is that not one of them appears in `PRODUCTION_DATABASE_NAMES`.
PROTECTED_VARIANTS: tuple[str, ...] = (
    "droppilot",
    "DROPPILOT",
    "DropPilot",
    "dropPILOT",
    "DrOpPiLoT",
)

#: Names that are *not* production and must stay usable. A guard that refused
#: these would be a guard that gets disabled.
INNOCENT_NAMES: tuple[str, ...] = (
    "droppilot_staging",
    "droppilot_test",
    "droppilot_b1_source",
    "DropPilot_Staging",
    "pilot",
    "drop",
)


def a_key(seed: bytes = b"\x01") -> BackupKey:
    material = hashlib.sha256(b"backup-b1-test-key" + seed).digest()
    return load_backup_key(base64.urlsafe_b64encode(material).decode())


def a_target(dbname: str) -> PostgresTarget:
    return PostgresTarget(
        host="db.internal",
        port=5432,
        user="droppilot_backup",
        password=SECRET_PASSWORD,
        dbname=dbname,
    )


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


class TestCanonicalisationIsPolicyOnly:
    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_capitalisation_of_production_is_protected(self, name: str) -> None:
        assert is_protected_database(name) is True

    @pytest.mark.parametrize("name", ["  droppilot", "droppilot  ", "\tdroppilot\n", " DropPilot "])
    def test_whitespace_variants_are_protected_by_policy(self, name: str) -> None:
        """Policy trims, so a stray space cannot smuggle the name past the guard.

        Note this is the *policy* side only. The same strings are refused
        outright as operator input — see the validation tests below. Both hold
        at once: the name is rejected as unusable, and if it somehow reached
        the policy check anyway it would still be recognised as production.
        """
        assert is_protected_database(name) is True

    @pytest.mark.parametrize("name", INNOCENT_NAMES)
    def test_an_innocent_database_is_not_protected(self, name: str) -> None:
        assert is_protected_database(name) is False

    def test_canonicalisation_never_returns_something_to_connect_with(self) -> None:
        """The canonical form is a comparison key, not a name."""
        assert canonical_database_name(" DropPilot ") == "droppilot"
        # And the caller still holds the original, unchanged.
        original = " DropPilot "
        canonical_database_name(original)
        assert original == " DropPilot "

    def test_casefold_not_lower(self) -> None:
        """`lower` is not a case-folding operation; the difference is testable.

        German sharp s is the standard demonstration: `'ẞ'.lower()` is `'ß'`,
        which still compares unequal to `'ss'`, while `casefold` maps both to
        `'ss'`. Nothing in `droppilot` needs it — the point is that the
        authority uses the operation that is correct in general, so adding a
        protected name with such a character later does not silently fail.
        """
        sharp_s = SHARP_S
        assert canonical_database_name(sharp_s) == "ss"
        assert sharp_s.lower() != "ss"

    def test_a_non_string_is_refused_rather_than_coerced(self) -> None:
        for value in (None, 5, b"droppilot", ["droppilot"]):
            with pytest.raises(DatabaseNameError, match="must be text"):
                canonical_database_name(value)

    def test_an_empty_or_whitespace_only_name_is_refused(self) -> None:
        for value in ("", "   ", "\t\n"):
            with pytest.raises(DatabaseNameError, match="must not be empty"):
                canonical_database_name(value)

    def test_is_protected_raises_rather_than_answering_false_for_a_non_name(self) -> None:
        """ "Not protected" and "not a name" are different answers.

        Collapsing them is how a guard comes to wave through the input it could
        not read.
        """
        with pytest.raises(DatabaseNameError):
            is_protected_database(None)
        with pytest.raises(DatabaseNameError):
            is_protected_database("")


class TestUnicodeIsHandledDeterministicallyAndWithoutSurprises:
    def test_a_cyrillic_homoglyph_is_not_mapped_onto_production(self) -> None:
        """A name using CYRILLIC SMALL LETTER O is a different database, and stays one.

        Mapping it onto production would be the *dangerous* behaviour, not the
        safe one: the tool would refuse an operation on an innocent database
        while telling the operator it was production. The exact identity check
        is what governs such a name, and it always will.
        """
        homoglyph = f"dr{CYRILLIC_O}ppilot"
        assert homoglyph != "droppilot"
        assert is_protected_database(homoglyph) is False

    def test_a_fullwidth_name_is_not_normalised_onto_production(self) -> None:
        """Deliberately no NFKC. A fullwidth spelling is another database."""
        assert FULLWIDTH_DROPPILOT != "droppilot"
        assert is_protected_database(FULLWIDTH_DROPPILOT) is False

    def test_the_turkish_dotless_i_does_not_collide_with_production(self) -> None:
        assert is_protected_database(f"dropp{DOTLESS_I}lot") is False

    def test_canonicalisation_is_idempotent(self) -> None:
        for name in (*PROTECTED_VARIANTS, *INNOCENT_NAMES, SHARP_S, DOTTED_I + "stanbul"):
            once = canonical_database_name(name)
            assert canonical_database_name(once) == once


# ---------------------------------------------------------------------------
# Validation of operator-supplied names
# ---------------------------------------------------------------------------


class TestOperatorSuppliedNamesAreGatedNotRepaired:
    def test_a_valid_name_is_returned_byte_for_byte(self) -> None:
        for name in INNOCENT_NAMES:
            assert validate_database_name(name, field="--target") == name

    @pytest.mark.parametrize(
        "name", [" droppilot_staging", "droppilot_staging ", "\tdroppilot_staging", "x\n"]
    )
    def test_surrounding_whitespace_is_refused_rather_than_trimmed(self, name: str) -> None:
        """Trimming would pick a database on the operator's behalf.

        ` droppilot_staging` and `droppilot_staging` are different PostgreSQL
        identifiers. A tool that silently chose one would be doing exactly what
        this module exists to prevent.
        """
        with pytest.raises(DatabaseNameError, match="whitespace"):
            validate_database_name(name, field="--target")

    @pytest.mark.parametrize(
        ("name", "label"),
        [
            ("drop\x00pilot", "NUL"),
            ("drop\npilot", "newline"),
            ("drop\rpilot", "carriage return"),
            ("drop\tpilot", "tab"),
            ("drop\x1bpilot", "escape"),
            ("drop\x07pilot", "bell"),
            ("drop\x7fpilot", "delete"),
            (f"drop{RTL_OVERRIDE}pilot", "right-to-left override"),
            (f"drop{ZERO_WIDTH_SPACE}pilot", "zero-width space"),
        ],
    )
    def test_control_and_format_characters_are_refused(self, name: str, label: str) -> None:
        with pytest.raises(DatabaseNameError, match="control or format character"):
            validate_database_name(name, field="--target")

    def test_a_refusal_never_echoes_the_rejected_value(self) -> None:
        """The value is precisely what must not reach a log."""
        hostile = "drop\x1b[2Jpilot"
        with pytest.raises(DatabaseNameError) as caught:
            validate_database_name(hostile, field="--target")
        message = str(caught.value)
        assert hostile not in message
        assert "\x1b" not in message
        assert "--target" in message

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(DatabaseNameError, match="is empty"):
            validate_database_name("", field="--target")

    def test_a_non_string_is_refused(self) -> None:
        with pytest.raises(DatabaseNameError, match="must be text"):
            validate_database_name(None, field="--target")

    def test_a_name_postgresql_would_truncate_is_refused(self) -> None:
        """A truncated name is not the name that was checked."""
        with pytest.raises(DatabaseNameError, match="truncates"):
            validate_database_name("d" * (MAX_DATABASE_NAME_BYTES + 1), field="--target")

    def test_the_byte_limit_is_bytes_not_characters(self) -> None:
        """Multi-byte characters count against PostgreSQL's limit as bytes."""
        name = "é" * 32  # 64 bytes in UTF-8
        assert len(name) < MAX_DATABASE_NAME_BYTES
        with pytest.raises(DatabaseNameError, match="64 bytes"):
            validate_database_name(name, field="--target")

    def test_a_name_at_exactly_the_limit_is_accepted(self) -> None:
        name = "d" * MAX_DATABASE_NAME_BYTES
        assert validate_database_name(name, field="--target") == name


class TestSafeLabel:
    def test_a_plain_name_renders_unchanged(self) -> None:
        assert safe_label("droppilot_staging") == "droppilot_staging"

    def test_unprintable_characters_never_reach_a_message(self) -> None:
        rendered = safe_label("drop\x1b[2J\npilot")
        assert "\x1b" not in rendered
        assert "\n" not in rendered
        assert rendered.startswith("drop")

    def test_a_missing_or_non_string_name_renders_as_a_placeholder(self) -> None:
        assert safe_label(None) == "<unnamed>"
        assert safe_label("") == "<unnamed>"

    def test_rendering_is_capped(self) -> None:
        assert len(safe_label("d" * 500)) <= MAX_DATABASE_NAME_BYTES


# ---------------------------------------------------------------------------
# Identity stays exact
# ---------------------------------------------------------------------------


class TestIdentityComparisonIsStillExact:
    def test_a_case_difference_between_expected_and_actual_is_still_a_mismatch(self) -> None:
        """The plausible mistake this whole change could have introduced."""
        with pytest.raises(DatabaseIdentityError, match="was expected"):
            require_database_name("droppilot_Staging", expected="droppilot_staging")

    def test_a_whitespace_difference_is_still_a_mismatch(self) -> None:
        with pytest.raises(DatabaseIdentityError, match="was expected"):
            require_database_name("droppilot_staging ", expected="droppilot_staging")

    def test_an_exact_match_on_an_innocent_database_passes(self) -> None:
        assert (
            require_database_name("droppilot_staging", expected="droppilot_staging")
            == "droppilot_staging"
        )

    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_capitalisation_the_server_reports_hits_the_backstop(self, name: str) -> None:
        """Even when the operator's expectation matches exactly."""
        with pytest.raises(DatabaseIdentityError, match="production database"):
            require_database_name(name, expected=name)

    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_the_explicit_flag_still_permits_it(self, name: str) -> None:
        assert require_database_name(name, expected=name, allow_production=True) == name

    def test_the_returned_name_is_the_servers_own_string(self) -> None:
        """Never a canonicalised one — that is a comparison key, not a name."""
        assert (
            require_database_name("DropPilot", expected="DropPilot", allow_production=True)
            == "DropPilot"
        )

    def test_a_substituted_protected_set_still_governs_every_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The set is read at call time, so a test can narrow or widen it.

        `tests/integration/test_data_subject_erasure.py` relies on this, and a
        pre-computed canonical set would have silently stopped honouring it.
        """
        monkeypatch.setattr(
            database_identity, "PRODUCTION_DATABASE_NAMES", frozenset({"Some_Other_DB"})
        )
        assert is_protected_database("some_other_db") is True
        assert is_protected_database("droppilot") is False


# ---------------------------------------------------------------------------
# The guards, through the service
# ---------------------------------------------------------------------------


class FakePopen:
    calls: list[dict[str, Any]] = []  # noqa: RUF012 - reset per test by the fixture

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        type(self).calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        self.stdout = io.BytesIO(FAKE_DUMP)
        self.stderr = io.BytesIO(b"")

    def kill(self) -> None:
        return None

    def wait(self) -> int:
        return 0


class FakeCursor:
    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        lowered = " ".join(sql.lower().split())
        if "current_database" in lowered:
            self._row = (self._facts["database"], self._facts["version"])
        elif "to_regclass" in lowered:
            self._row = (True,)
        elif "alembic_version" in lowered:
            self._row = (self._facts.get("revision", "0033"),)
        elif "information_schema.tables" in lowered:
            self._row = (self._facts.get("tables", 0),)
        else:
            self._row = (0,)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeConnection:
    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._facts)

    def rollback(self) -> None:
        return None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


REAL_POPEN = subprocess.Popen
REAL_RUN = subprocess.run


@pytest.fixture
def fake_postgres(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "database": "droppilot_staging",
        "version": "PostgreSQL 17.10 on x86_64-windows",
        "revision": "0033",
        "tables": 0,
    }
    FakePopen.calls = []

    def dispatch_popen(argv: list[str], **kwargs: Any) -> Any:
        if argv and "pg_dump" in str(argv[0]):
            return FakePopen(argv, **kwargs)
        return REAL_POPEN(argv, **kwargs)

    def dispatch_run(argv: list[str], **kwargs: Any) -> Any:
        if argv and "pg_restore" in str(argv[0]):
            return subprocess.CompletedProcess(argv, 0, stdout="1; TABLE tenants\n", stderr="")
        return REAL_RUN(argv, **kwargs)

    monkeypatch.setattr(database_backup, "_connect", lambda target: FakeConnection(facts))
    monkeypatch.setattr(database_backup, "_tool", lambda name, *, pg_bin_dir: f"/fake/{name}")
    monkeypatch.setattr(database_backup.subprocess, "Popen", dispatch_popen)
    monkeypatch.setattr(database_backup.subprocess, "run", dispatch_run)
    return facts


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "droppilot" / "backups"
    directory.mkdir(parents=True)
    return directory


class TestNoVariantOfProductionCanBeBackedUp:
    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_variant_is_refused_before_connecting(
        self,
        name: str,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def refuse_to_connect(target: Any) -> Any:
            raise AssertionError("a connection was opened before the name was judged")

        monkeypatch.setattr(database_backup, "_connect", refuse_to_connect)
        with pytest.raises(ConfigurationError, match="protected production database name"):
            create_backup(
                target=a_target(name),
                expected_database=name,
                key=a_key(),
                directory=backup_dir,
            )
        assert FakePopen.calls == []
        assert list(backup_dir.iterdir()) == []

    def test_a_mixed_case_name_the_server_reports_still_hits_the_backstop(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """The operator claimed something innocent; the server said otherwise."""
        fake_postgres["database"] = "DropPilot"
        with pytest.raises(ConfigurationError, match="was expected"):
            create_backup(
                target=a_target("droppilot_staging"),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert FakePopen.calls == []

    def test_a_control_character_in_the_expected_name_is_refused(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            database_backup,
            "_connect",
            lambda t: (_ for _ in ()).throw(AssertionError("connected")),
        )
        with pytest.raises(ConfigurationError, match="control or format character"):
            create_backup(
                target=a_target("droppilot_staging"),
                expected_database="droppilot\x00_staging",
                key=a_key(),
                directory=backup_dir,
            )

    def test_an_innocent_database_is_still_backed_up_normally(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """The happy path must be untouched, or the guard gets removed."""
        outcome = create_backup(
            target=a_target("droppilot_staging"),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
        )
        assert outcome.manifest.database == "droppilot_staging"
        assert outcome.ciphertext_path.is_file()

    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_the_explicit_flag_permits_every_variant(
        self, name: str, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """Protection, not prohibition — the flag still means what it meant."""
        fake_postgres["database"] = name
        outcome = create_backup(
            target=a_target(name),
            expected_database=name,
            key=a_key(),
            directory=backup_dir,
            allow_production=True,
        )
        # The *exact* name is recorded, not a canonicalised one.
        assert outcome.manifest.database == name


@pytest.fixture
def a_backup(fake_postgres: dict[str, Any], backup_dir: Path) -> Any:
    return create_backup(
        target=a_target("droppilot_staging"),
        expected_database="droppilot_staging",
        key=a_key(),
        directory=backup_dir,
        chunk_bytes=1024,
    )


class TestNoVariantOfProductionCanBeRestoredOver:
    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_variant_is_refused_as_a_target(self, name: str, a_backup: Any) -> None:
        with pytest.raises(ConfigurationError, match="protected production database name"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target(name),
                expected_source="droppilot_staging",
                expected_target=name,
                key=a_key(),
            )

    def test_the_flag_alone_is_not_enough_without_a_confirmation(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        """`--production-restore` opens the door; it does not walk through it."""
        fake_postgres["database"] = "DropPilot"
        with pytest.raises(ConfigurationError, match="no typed confirmation was possible"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("DropPilot"),
                expected_source="droppilot_staging",
                expected_target="DropPilot",
                key=a_key(),
                apply=True,
                allow_production_target=True,
                allow_non_empty=True,
                confirm_production=None,
            )

    def test_a_refused_confirmation_stops_the_restore(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "DROPPILOT"
        with pytest.raises(ConfigurationError, match="was not confirmed"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("DROPPILOT"),
                expected_source="droppilot_staging",
                expected_target="DROPPILOT",
                key=a_key(),
                apply=True,
                allow_production_target=True,
                allow_non_empty=True,
                confirm_production=lambda observed: False,
            )

    def test_the_confirmation_is_asked_with_the_name_the_server_reported(
        self, a_backup: Any, fake_postgres: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not the one on the command line — that is the string under suspicion."""
        fake_postgres["database"] = "DropPilot"
        asked: list[str] = []

        def capture(observed: str) -> bool:
            asked.append(observed)
            return True

        restore_backup(
            a_backup.ciphertext_path,
            target=a_target("DropPilot"),
            expected_source="droppilot_staging",
            expected_target="DropPilot",
            key=a_key(),
            apply=True,
            allow_production_target=True,
            allow_non_empty=True,
            confirm_production=capture,
        )
        assert asked == ["DropPilot"]

    def test_a_rehearsal_never_prompts(self, a_backup: Any, fake_postgres: dict[str, Any]) -> None:
        """Nothing is written, so there is nothing to confirm."""
        fake_postgres["database"] = "DropPilot"

        def must_not_be_called(observed: str) -> bool:
            raise AssertionError("a rehearsal asked for a production confirmation")

        outcome = restore_backup(
            a_backup.ciphertext_path,
            target=a_target("DropPilot"),
            expected_source="droppilot_staging",
            expected_target="DropPilot",
            key=a_key(),
            allow_production_target=True,
            allow_non_empty=True,
            confirm_production=must_not_be_called,
        )
        assert outcome.dry_run is True

    def test_an_innocent_target_is_never_prompted_for(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"

        def must_not_be_called(observed: str) -> bool:
            raise AssertionError("an isolated restore asked for a production confirmation")

        outcome = restore_backup(
            a_backup.ciphertext_path,
            target=a_target("droppilot_restore_check"),
            expected_source="droppilot_staging",
            expected_target="droppilot_restore_check",
            key=a_key(),
            apply=True,
            confirm_production=must_not_be_called,
        )
        assert outcome.dry_run is False
        assert outcome.target_database == "droppilot_restore_check"

    def test_an_identity_mismatch_is_still_refused_before_any_confirmation(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_somewhere_else"

        def must_not_be_called(observed: str) -> bool:
            raise AssertionError("confirmation was reached despite an identity mismatch")

        with pytest.raises(ConfigurationError, match="was expected"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_staging",
                expected_target="droppilot_restore_check",
                key=a_key(),
                apply=True,
                confirm_production=must_not_be_called,
            )

    def test_a_control_character_in_the_target_is_refused(self, a_backup: Any) -> None:
        with pytest.raises(ConfigurationError, match="control or format character"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_staging",
                expected_target="droppilot\nrestore",
                key=a_key(),
            )

    def test_no_refusal_message_carries_a_password_or_a_dsn(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        messages: list[str] = []
        for target, source in (
            ("DropPilot", "droppilot_staging"),
            ("droppilot_restore_check", "some_other_source"),
        ):
            with pytest.raises(ConfigurationError) as caught:
                restore_backup(
                    a_backup.ciphertext_path,
                    target=a_target(target),
                    expected_source=source,
                    expected_target=target,
                    key=a_key(),
                )
            messages.append(str(caught.value))
        joined = " ".join(messages)
        assert SECRET_PASSWORD not in joined
        assert "://" not in joined
        assert "db.internal" not in joined


class TestTheRestoreCliGuard:
    def _cli(self) -> Any:
        import scripts.restore_database_backup as module

        return module

    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_variant_is_refused_at_the_command_line(
        self, name: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = self._cli().main(
            ["--file", "x.dpbk", "--expect-source", "droppilot_staging", "--target", name]
        )
        assert code == int(ExitCode.CONFIGURATION)
        assert "typed confirmation" in capsys.readouterr().err

    def test_a_malformed_target_is_refused_without_a_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = self._cli().main(
            [
                "--file",
                "x.dpbk",
                "--expect-source",
                "droppilot_staging",
                "--target",
                "drop\x00pilot",
            ]
        )
        assert code == int(ExitCode.CONFIGURATION)
        assert "control or format character" in capsys.readouterr().err

    def test_a_non_terminal_session_cannot_confirm(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False, raising=False)
        assert module._confirm_production("DropPilot") is False
        assert "no terminal" in capsys.readouterr().err

    def test_the_prompt_shows_the_observed_name_and_requires_it_exactly(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)

        monkeypatch.setattr("builtins.input", lambda *a: "droppilot")
        assert module._confirm_production("DropPilot") is False, (
            "a different capitalisation was accepted as confirmation"
        )
        assert "did not match" in capsys.readouterr().err

        monkeypatch.setattr("builtins.input", lambda *a: "DropPilot")
        assert module._confirm_production("DropPilot") is True
        assert "DropPilot" in capsys.readouterr().out

    def test_an_eof_at_the_prompt_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)

        def eof(*args: Any) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        assert module._confirm_production("DropPilot") is False

    def test_the_prompt_never_renders_an_unprintable_name(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda *a: "x")
        module._confirm_production("drop\x1b[2Jpilot")
        assert "\x1b" not in capsys.readouterr().out


class TestTheCreateCliGuard:
    def _cli(self) -> Any:
        import scripts.create_database_backup as cli

        return cli

    class _Settings:
        def __init__(self, directory: Path, key: str) -> None:
            self.backup = TestTheCreateCliGuard._Backup(directory, key)
            self.security = TestTheCreateCliGuard._Security()
            self.database = TestTheCreateCliGuard._Database()

    class _Backup:
        def __init__(self, directory: Path, key: str) -> None:
            self.directory = str(directory)
            self.encryption_key = SecretStr(key)
            self.pg_bin_dir = None

    class _Security:
        def __init__(self) -> None:
            self.secret_key = SecretStr("a-production-signing-key-that-is-long-enough")
            self.otp_hmac_key = SecretStr("a-distinct-production-otp-key-also-long")
            self.encryption_keys: list[SecretStr] = []

    class _Database:
        def __init__(self) -> None:
            self.host = "db.internal"
            self.port = 5432
            self.user = "droppilot_backup"
            self.password = SecretStr(SECRET_PASSWORD)
            self.db = "droppilot_staging"

    @pytest.mark.parametrize("name", PROTECTED_VARIANTS)
    def test_every_variant_needs_the_flag(
        self,
        name: str,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cli = self._cli()
        key = a_key()
        monkeypatch.setattr(
            cli,
            "settings",
            self._Settings(backup_dir, base64.urlsafe_b64encode(key.material).decode()),
        )
        monkeypatch.setattr(
            database_backup,
            "_connect",
            lambda t: (_ for _ in ()).throw(AssertionError("connected")),
        )
        assert cli.main(["--expect-database", name]) == int(ExitCode.CONFIGURATION)
        assert "protected production database name" in capsys.readouterr().err
        assert list(backup_dir.iterdir()) == []

    def test_a_malformed_name_is_refused_with_a_configuration_code(
        self,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cli = self._cli()
        key = a_key()
        monkeypatch.setattr(
            cli,
            "settings",
            self._Settings(backup_dir, base64.urlsafe_b64encode(key.material).decode()),
        )
        assert cli.main(["--expect-database", " droppilot_staging"]) == int(ExitCode.CONFIGURATION)
        assert "whitespace" in capsys.readouterr().err


class TestNothingDestructiveIsReachableFromAnyProductionVariant:
    """A sweep, rather than trusting that every call site was found.

    For each spelling of the production name, every entry point is driven with
    the safest plausible arguments and required to refuse. `pg_dump`,
    `pg_restore` and the connection are all replaced with things that fail the
    test if they are reached.
    """

    @pytest.mark.parametrize("name", [*PROTECTED_VARIANTS, " droppilot", "droppilot "])
    def test_no_entry_point_touches_a_production_variant(
        self,
        name: str,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"a destructive path was reached for {name!r}")

        monkeypatch.setattr(database_backup, "_connect", explode)
        monkeypatch.setattr(database_backup.subprocess, "Popen", explode)
        monkeypatch.setattr(database_backup.subprocess, "run", explode)

        with pytest.raises(ConfigurationError):
            create_backup(
                target=a_target(name),
                expected_database=name,
                key=a_key(),
                directory=backup_dir,
            )
        with pytest.raises(ConfigurationError):
            restore_backup(
                backup_dir / "absent.dpbk",
                target=a_target(name),
                expected_source="droppilot_staging",
                expected_target=name,
                key=a_key(),
                apply=True,
            )
        assert list(backup_dir.iterdir()) == []
