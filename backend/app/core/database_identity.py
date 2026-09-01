"""Confirm which database a destructive operation is about to touch.

An erasure script reads its connection from whichever `.env` happened to load.
That is fine until someone runs it from the wrong directory, or a deployment
leaves a production `.env` beside a staging checkout, and a rehearsal becomes a
live deletion. Configuration is not identity.

So the operator states the database they *expect*, and this asks the server
which one it is actually connected to. If the two disagree, nothing runs.

`current_database()` is deliberately a query rather than a parse of the URL: the
URL is the same string that was already wrong, and re-reading it proves nothing.
The answer comes from the server that would receive the DELETE.

**Two comparisons live here, and conflating them would be a bug.**

*Identity* — "is the server I am connected to the one I was told to expect?" —
is **exact**. If the operator says `droppilot_staging` and the server says
`droppilot_Staging`, those are two different databases in PostgreSQL and the
run must stop. Loosening this would let a tool write to a database nobody
named.

*Policy* — "is this name one of the protected production names?" — is
**case-insensitive and whitespace-tolerant**. Its job is to catch a human
typing `DropPilot` and to be impossible to slip past by capitalisation. A
protection that can be bypassed by holding shift is decoration.

Neither comparison ever changes the string used to connect. PostgreSQL folds
unquoted identifiers to lower case but preserves quoted ones exactly, so
`"DropPilot"` and `droppilot` can both exist on one server. Canonicalisation
here is a **policy comparison, not identifier rewriting** — the name handed to
libpq is always the one the operator supplied, byte for byte.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "MAX_DATABASE_NAME_BYTES",
    "PRODUCTION_DATABASE_NAMES",
    "DatabaseIdentityError",
    "DatabaseNameError",
    "canonical_database_name",
    "current_database",
    "is_protected_database",
    "require_database",
    "require_database_name",
    "safe_label",
    "validate_database_name",
]

#: Names that must never be touched without the explicit production flag. This
#: is a backstop, not the primary control — the expected-name check is.
#:
#: Compared through `is_protected_database`, which canonicalises both sides, so
#: every capitalisation of every entry here is covered without listing them.
#: Read at call time rather than pre-computed, so a test that substitutes this
#: set still governs every guard.
PRODUCTION_DATABASE_NAMES: Final[frozenset[str]] = frozenset({"droppilot"})

#: PostgreSQL truncates identifiers at `NAMEDATALEN - 1`, 63 bytes by default.
#: A longer name is silently shortened by the server, which means the name the
#: operator checked and the name the server used would differ — so it is
#: refused rather than truncated.
MAX_DATABASE_NAME_BYTES: Final[int] = 63

#: Unicode general categories that must never appear in a database name.
#: `Cc` is the C0/C1 controls including NUL, newline and tab; `Cf` is format
#: characters such as the bidirectional overrides, which can make a name render
#: as something other than what it is; `Cs`, `Co` and `Cn` are surrogates,
#: private use and unassigned.
_FORBIDDEN_CATEGORIES: Final[frozenset[str]] = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


class DatabaseIdentityError(RuntimeError):
    """The connected database is not the one the operator expected."""


class DatabaseNameError(DatabaseIdentityError):
    """A supplied database name is not usable as one.

    A subclass so that every existing caller catching `DatabaseIdentityError`
    keeps working: a name that cannot be validated is a reason to refuse for
    the same reason a mismatched name is, and no call site should have to learn
    a second exception to stay safe.
    """


def safe_label(name: object) -> str:
    """Render a database name for a message, and never render anything else.

    Names reach messages from two places that are not fully trusted: an
    operator's command line, and the server's own answer. Neither should be
    able to put a terminal escape, a bidirectional override or a newline into a
    log line that somebody later reads to decide what happened. Anything
    unprintable becomes `?`, and the result is capped — a message is for
    orientation, not for reproducing an arbitrary string.
    """
    if not isinstance(name, str) or not name:
        return "<unnamed>"
    cleaned = "".join(character if character.isprintable() else "?" for character in name)
    return cleaned[:MAX_DATABASE_NAME_BYTES] or "<unprintable>"


def validate_database_name(raw: object, *, field: str) -> str:
    """Return an operator-supplied name unchanged, or refuse to use it at all.

    **Returns the original string.** Validation is a gate, not a transform: a
    function that quietly repaired a name would connect to a database the
    operator never typed, which is the failure this whole module exists to
    prevent.

    Surrounding whitespace is refused rather than trimmed, for the same reason.
    ` droppilot` and `droppilot` are different PostgreSQL identifiers, and a
    tool that silently picked one of them would be choosing a database on the
    operator's behalf. The policy comparison in `canonical_database_name` does
    trim — there, tolerance is protective; here, it would be presumptuous.

    `field` names the flag or setting, so a refusal says which input was wrong
    without echoing the input itself. The value may be exactly what should not
    reach a log.
    """
    if not isinstance(raw, str):
        raise DatabaseNameError(f"{field} must be text.")
    if not raw:
        raise DatabaseNameError(f"{field} is empty.")
    if raw != raw.strip():
        raise DatabaseNameError(
            f"{field} has leading or trailing whitespace. That is a different "
            "PostgreSQL identifier from the trimmed name, and guessing which "
            "one was meant is not this tool's decision. Supply it without the "
            "surrounding space."
        )

    for position, character in enumerate(raw):
        if unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
            raise DatabaseNameError(
                f"{field} contains a control or format character at position "
                f"{position}. The character itself is not shown, because a name "
                "carrying one is exactly what must not reach a log."
            )

    encoded = len(raw.encode("utf-8"))
    if encoded > MAX_DATABASE_NAME_BYTES:
        raise DatabaseNameError(
            f"{field} is {encoded} bytes; PostgreSQL truncates database names "
            f"at {MAX_DATABASE_NAME_BYTES}. A truncated name would not be the "
            "name that was checked."
        )
    return raw


def canonical_database_name(raw: object) -> str:
    """The form used **only** for protected-name policy comparison.

    Never used to connect, never returned to a caller as a database name, and
    never written anywhere. `casefold` rather than `lower` because `lower` is
    not a case-folding operation — it leaves `ẞ` and `İ` in forms that still
    compare unequal to their lowercase counterparts.

    Deliberately **not** NFKC-normalised. Normalising would map a fullwidth
    spelling of the name (U+FF44 onwards) onto `droppilot`, and those are
    genuinely different databases; treating them as one would refuse an
    operation on an innocent database while telling the operator it was
    production. Homoglyphs — CYRILLIC SMALL LETTER O standing in for the Latin
    one — are likewise left alone: such a name is not the production database,
    and the exact identity check is what governs it.

    Confusable characters are named here rather than shown. A module about
    homoglyphs that contained homoglyphs would be unreviewable: nobody can tell
    the two apart by looking, which is the whole problem.
    """
    if not isinstance(raw, str):
        raise DatabaseNameError("A database name must be text.")
    stripped = raw.strip()
    if not stripped:
        raise DatabaseNameError("A database name must not be empty.")
    return stripped.casefold()


def is_protected_database(raw: object) -> bool:
    """Whether this name is one of the protected production names, under policy.

    Both sides are canonicalised, so `DROPPILOT`, `DropPilot` and ` droppilot `
    are all protected without any of them being listed. Raises rather than
    returning `False` for an unusable name: "not protected" and "not a name"
    are different answers, and collapsing them is how a guard comes to wave
    through the input it could not read.
    """
    candidate = canonical_database_name(raw)
    return any(candidate == canonical_database_name(name) for name in PRODUCTION_DATABASE_NAMES)


async def current_database(session: AsyncSession) -> str:
    """Ask the server for its own name."""
    return str((await session.execute(text("SELECT current_database()"))).scalar_one())


def require_database_name(actual: str, *, expected: str, allow_production: bool = False) -> str:
    """The decision itself, with no opinion about how the name was obtained.

    Split out from `require_database` for the backup tooling, which reads
    `current_database()` over a synchronous psycopg connection because it is
    about to hand the same connection details to `pg_dump`. Two copies of this
    reasoning would be two places for the production-name backstop to be
    forgotten, and the one that gets forgotten is always the newer one.

    The equality below is **exact** and must stay that way — see the module
    docstring. Only the production backstop is case-insensitive.
    """
    if actual != expected:
        raise DatabaseIdentityError(
            f"Connected to {safe_label(actual)!r} but {safe_label(expected)!r} was "
            "expected. Nothing was changed. Check which environment file is loaded."
        )

    if is_protected_database(actual) and not allow_production:
        raise DatabaseIdentityError(
            f"{safe_label(actual)!r} is a production database. Re-run with the "
            "explicit production flag if that is genuinely intended."
        )

    return actual


async def require_database(
    session: AsyncSession, *, expected: str, allow_production: bool = False
) -> str:
    """Refuse unless the connected database is exactly ``expected``.

    Returns the confirmed name so a caller can show it in a confirmation
    prompt — the operator should see the database they are about to change,
    named by the server rather than by their own shell.
    """
    return require_database_name(
        await current_database(session), expected=expected, allow_production=allow_production
    )
