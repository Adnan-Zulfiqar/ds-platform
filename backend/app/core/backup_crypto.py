"""The encrypted container a database backup is written into.

A backup is the single most dangerous file this company will ever produce: one
portable object containing every tenant's workspace, every customer record the
merchants hold, and the ciphertext of every marketplace credential. The live
database is behind a network, a password and an application; the backup is a
file that fits on a USB stick. It therefore gets stronger handling than the
database it came from, not weaker.

**Why an envelope rather than encrypting with the configured key directly.**
Each backup gets a fresh random 256-bit data key, and only that key is wrapped
by the long-lived key from configuration. Two consequences matter. Rotating the
long-lived key re-wraps a few hundred bytes per backup instead of re-encrypting
hundreds of gigabytes, so rotation is something an operator will actually do.
And no single key ever encrypts more than one file, which removes the
nonce-reuse cliff that GCM falls off when one key covers many multi-gigabyte
streams.

**Why STREAM framing rather than one-shot AEAD.** A one-shot `AESGCM.encrypt`
needs the whole dump in memory twice, which a production dump will not permit.
Worse, it gives no way to notice that a file stops early: you learn a 40 GB
backup was truncated after decrypting 40 GB, and only if you finish. So the
plaintext is split into fixed chunks, each independently authenticated, with the
nonce carrying the chunk counter and a final-chunk flag — the STREAM
construction of Hoang, Reyhanitabar, Rogaway and Vizár. Chunks cannot be
reordered, duplicated, dropped or moved between files, and a stream that ends
without its final-flagged chunk is refused as truncated rather than accepted as
short.

**Why the header is the associated data.** Every chunk is authenticated against
the exact header bytes, so the wrapped key, the key identifier and the chunk
size cannot be edited or swapped in from another backup. The header is not
encrypted — it holds no secret — but it cannot be changed.

Nothing here logs, prints or returns key material. The one value that leaves is
the key *identifier*, which is an HKDF output over a 256-bit key: it names which
key a file needs, and reveals nothing usable about it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

__all__ = [
    "CHUNK_BYTES",
    "CONTAINER_VERSION",
    "MAGIC",
    "PUBLISHED_BACKUP_KEYS",
    "BackupCryptoError",
    "BackupHeader",
    "BackupKey",
    "CorruptBackupError",
    "InvalidBackupKeyError",
    "TruncatedBackupError",
    "WrongBackupKeyError",
    "decrypt_stream",
    "encrypt_stream",
    "is_safe_backup_id",
    "load_backup_key",
    "manifest_mac",
    "read_header",
]

#: File magic. Present so a wrong file is rejected by its first eight bytes
#: rather than by a confusing authentication failure megabytes later.
MAGIC: Final[bytes] = b"DPBKUP\x00\x01"

#: Container format version. Bumped only for a breaking change to the framing;
#: a reader that does not recognise the version refuses rather than guessing.
CONTAINER_VERSION: Final[int] = 1

#: Plaintext bytes per authenticated chunk. Four megabytes keeps peak memory
#: flat and bounds a GCM key to far below its safe message count even for a
#: dump in the terabytes.
CHUNK_BYTES: Final[int] = 4 * 1024 * 1024

#: Largest plaintext chunk that may be written, and the ciphertext ceiling that
#: follows from it. The reading ceiling exists so a tampered length prefix
#: cannot ask for an arbitrary allocation; the writing one exists so the two
#: can never disagree and produce a backup this code refuses to read.
_MAX_CHUNK_PLAINTEXT: Final[int] = 64 * 1024 * 1024
_MAX_CHUNK_CIPHERTEXT: Final[int] = _MAX_CHUNK_PLAINTEXT + 64

_KEY_BYTES: Final[int] = 32
_NONCE_BYTES: Final[int] = 12
_STREAM_PREFIX_BYTES: Final[int] = 7
_TAG_BYTES: Final[int] = 16
_MAX_HEADER_BYTES: Final[int] = 8192

_INFO_KEY_ID: Final[bytes] = b"droppilot-backup-key-id-v1"
_INFO_MANIFEST_MAC: Final[bytes] = b"droppilot-backup-manifest-mac-v1"

#: Backup keys that appear in this repository and are therefore public. A key
#: anyone can `git clone` is not a key. The test key is listed by value on
#: purpose — the check has to recognise it, and publishing an already-published
#: value costs nothing.
PUBLISHED_BACKUP_KEYS: Final[frozenset[str]] = frozenset(
    {
        # tests/conftest.py and tests/unit/test_backup_b1.py
        "YmFja3VwLXRlc3Qta2V5LW5vdC1mb3ItcHJvZHVjdGlvbiE=",
        # .env.example, as an obviously-invalid placeholder
        "CHANGE-ME-generate-with-secrets-token-urlsafe-32",
    }
)

#: Keys whose bytes are a single repeated value. Not an entropy estimator —
#: a real one on 32 bytes has no statistical power. This catches the specific
#: mistake of `base64.b64encode(b"0" * 32)`, which looks plausible in a diff.
_MIN_DISTINCT_BYTES: Final[int] = 8


class BackupCryptoError(RuntimeError):
    """Base class for every failure in this module."""


class InvalidBackupKeyError(BackupCryptoError):
    """The configured backup key is missing, malformed, public or reused."""


class WrongBackupKeyError(BackupCryptoError):
    """The key offered cannot open this backup."""


class CorruptBackupError(BackupCryptoError):
    """The container failed authentication or is not a backup at all."""


class TruncatedBackupError(CorruptBackupError):
    """The stream ended before the chunk marked final.

    Separate from `CorruptBackupError` because the operator action differs: a
    truncated file usually means the disk filled or the job was killed, and the
    fix is to take another backup, not to hunt for tampering.
    """


@dataclass(frozen=True, slots=True)
class BackupKey:
    """A validated 256-bit key-encryption key, with an identifier for it.

    The raw bytes are held, so this object must never be logged. `key_id` is
    the only part safe to write down: it is 64 bits of HKDF output over a
    256-bit secret, which names the key without narrowing it.
    """

    material: bytes
    key_id: str

    def __repr__(self) -> str:
        """Never render the key, including in a traceback's locals summary."""
        return f"BackupKey(key_id={self.key_id!r})"

    __str__ = __repr__


def _hkdf(key: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(key)


def _decode_key(raw: str) -> bytes:
    """Decode a base64 key, accepting both alphabets, rejecting anything else."""
    candidate = raw.strip()
    if not candidate:
        raise InvalidBackupKeyError(
            "The backup encryption key is empty. Generate one with "
            '`python -c "import base64, os; '
            'print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` and store '
            "it in the operator's secret store — never in this repository."
        )
    if len(candidate) > 512:
        raise InvalidBackupKeyError(
            "The backup encryption key is implausibly long; it should be a "
            "base64-encoded 32-byte value."
        )
    try:
        material = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))
    except (binascii.Error, ValueError):
        try:
            material = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidBackupKeyError("The backup encryption key is not valid base64.") from exc
    if len(material) != _KEY_BYTES:
        raise InvalidBackupKeyError(
            f"The backup encryption key decodes to {len(material)} bytes; "
            f"exactly {_KEY_BYTES} are required for AES-256-GCM."
        )
    return material


def load_backup_key(raw: str | None, *, forbidden: dict[str, str] | None = None) -> BackupKey:
    """Validate a configured backup key, or refuse with a reason.

    `forbidden` maps a setting name to its value; any match is rejected. It
    exists because the tempting shortcut — reusing `SECURITY_SECRET_KEY` —
    silently couples two things with opposite rotation stories. The signing key
    can be rotated at any moment: every session ends and users log in again. The
    backup key cannot: rotating it without re-wrapping makes every existing
    backup unopenable, which is indistinguishable from having no backups at the
    moment you need one.
    """
    if raw is None:
        raise InvalidBackupKeyError(
            "No backup encryption key is configured. Set BACKUP_ENCRYPTION_KEY in "
            "the operator's secret store; the tooling refuses to write an "
            "unencrypted dump."
        )

    candidate = raw.strip()
    if candidate in PUBLISHED_BACKUP_KEYS:
        raise InvalidBackupKeyError(
            "The backup encryption key is a value published in this repository. "
            "Anyone who can read the source could decrypt every backup taken "
            "with it."
        )

    material = _decode_key(candidate)

    if len(set(material)) < _MIN_DISTINCT_BYTES:
        raise InvalidBackupKeyError(
            "The backup encryption key decodes to a repeating pattern rather than random bytes."
        )

    for setting, other in (forbidden or {}).items():
        if not other:
            continue
        if hmac.compare_digest(candidate, other.strip()):
            raise InvalidBackupKeyError(
                f"The backup encryption key is the same value as {setting}. "
                "One leak would compromise both, and the two have incompatible "
                "rotation stories."
            )
        try:
            if hmac.compare_digest(material, _decode_key(other)):
                raise InvalidBackupKeyError(
                    f"The backup encryption key decodes to the same bytes as "
                    f"{setting}, differently encoded."
                )
        except InvalidBackupKeyError as exc:
            # The other setting is not a 32-byte base64 key at all, so it
            # cannot collide. Its own validity is not this function's business.
            if "same bytes" in str(exc):
                raise

    return BackupKey(material=material, key_id=_hkdf(material, _INFO_KEY_ID, 8).hex())


def manifest_mac(key: BackupKey, payload: bytes) -> str:
    """Authenticate a manifest with a subkey derived from the backup key.

    A separate subkey rather than the key itself: the manifest MAC is verified
    by tooling that may never decrypt anything, and a key used for two purposes
    is a key that can be used for the wrong one.
    """
    subkey = _hkdf(key.material, _INFO_MANIFEST_MAC, 32)
    return hmac.new(subkey, payload, hashlib.sha256).hexdigest()


def _nonce(prefix: bytes, counter: int, *, final: bool) -> bytes:
    return prefix + struct.pack(">I", counter) + (b"\x01" if final else b"\x00")


def _canonical_header(header: dict[str, object]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class BackupHeader:
    raw: bytes
    fields: dict[str, object]

    @property
    def backup_id(self) -> str:
        return str(self.fields["backup_id"])

    @property
    def key_id(self) -> str:
        return str(self.fields["key_id"])


def encrypt_stream(
    plaintext: IO[bytes],
    ciphertext: IO[bytes],
    *,
    key: BackupKey,
    backup_id: str,
    chunk_bytes: int = CHUNK_BYTES,
) -> tuple[int, str, int, str]:
    """Encrypt `plaintext` into `ciphertext`, returning what the manifest needs.

    Returns `(plaintext_bytes, plaintext_sha256, ciphertext_bytes,
    ciphertext_sha256)`. Both digests are computed in the same pass that writes,
    because reading a multi-gigabyte file twice to hash it is a cost with no
    benefit — and a second pass would hash a file that could have changed.
    """
    # A chunk size the reader would refuse produces a backup that cannot be
    # read back — the one failure mode this whole module exists to prevent, and
    # reachable from a CLI flag. Refused before a byte is written rather than
    # discovered on the day of the restore.
    if not 0 < chunk_bytes <= _MAX_CHUNK_PLAINTEXT:
        raise BackupCryptoError(
            f"A chunk size of {chunk_bytes} bytes is outside the range this "
            f"format can read back (1 to {_MAX_CHUNK_PLAINTEXT} bytes). No "
            "backup was written."
        )

    dek = os.urandom(_KEY_BYTES)
    wrap_nonce = os.urandom(_NONCE_BYTES)
    stream_prefix = os.urandom(_STREAM_PREFIX_BYTES)
    wrapped = AESGCM(key.material).encrypt(wrap_nonce, dek, MAGIC)

    header = _canonical_header(
        {
            "alg": "AES-256-GCM/STREAM",
            "backup_id": backup_id,
            "chunk_bytes": chunk_bytes,
            "key_id": key.key_id,
            "stream_prefix": base64.b64encode(stream_prefix).decode(),
            "v": CONTAINER_VERSION,
            "wrap_nonce": base64.b64encode(wrap_nonce).decode(),
            "wrapped_dek": base64.b64encode(wrapped).decode(),
        }
    )
    if len(header) > _MAX_HEADER_BYTES:
        raise BackupCryptoError("Backup header exceeds the readable maximum.")

    out_digest = hashlib.sha256()
    in_digest = hashlib.sha256()
    written = 0

    def emit(block: bytes) -> None:
        nonlocal written
        out_digest.update(block)
        written += len(block)
        ciphertext.write(block)

    emit(MAGIC)
    emit(struct.pack(">I", len(header)))
    emit(header)

    aead = AESGCM(dek)
    counter = 0
    plaintext_bytes = 0
    pending = plaintext.read(chunk_bytes)
    while True:
        block = pending
        pending = plaintext.read(chunk_bytes)
        final = not pending
        in_digest.update(block)
        plaintext_bytes += len(block)
        sealed = aead.encrypt(_nonce(stream_prefix, counter, final=final), block, header)
        emit(struct.pack(">I", len(sealed)))
        emit(sealed)
        counter += 1
        if final:
            break

    ciphertext.flush()
    # `written` and `out_digest` cover exactly the bytes emitted, so a caller
    # comparing them against the published file proves the file on disk is the
    # one this function produced — not merely that a file of some size exists.
    return plaintext_bytes, in_digest.hexdigest(), written, out_digest.hexdigest()


def read_header(stream: IO[bytes]) -> BackupHeader:
    """Read and validate the container header, leaving the stream at chunk one."""
    magic = stream.read(len(MAGIC))
    if magic != MAGIC:
        raise CorruptBackupError(
            "This file does not begin with the DropPilot backup magic; it is "
            "not a backup produced by this tooling."
        )
    raw_len = stream.read(4)
    if len(raw_len) != 4:
        raise TruncatedBackupError("The file ends inside its header length.")
    (length,) = struct.unpack(">I", raw_len)
    if not 0 < length <= _MAX_HEADER_BYTES:
        raise CorruptBackupError("The backup header length is implausible.")
    raw = stream.read(length)
    if len(raw) != length:
        raise TruncatedBackupError("The file ends inside its header.")
    try:
        fields = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptBackupError("The backup header is not readable JSON.") from exc
    if not isinstance(fields, dict):
        raise CorruptBackupError("The backup header is not an object.")
    required = {"alg", "backup_id", "chunk_bytes", "key_id", "stream_prefix", "v", "wrap_nonce"}
    missing = sorted(required - fields.keys())
    if missing:
        raise CorruptBackupError(f"The backup header is missing {', '.join(missing)}.")
    if fields.get("v") != CONTAINER_VERSION:
        raise CorruptBackupError(
            f"This backup is container version {fields.get('v')!r}; this tool "
            f"reads version {CONTAINER_VERSION}."
        )
    return BackupHeader(raw=raw, fields=fields)


def decrypt_stream(stream: IO[bytes], *, key: BackupKey) -> Iterator[bytes]:
    """Yield authenticated plaintext chunks, or raise before yielding a bad one.

    Nothing is yielded until the chunk containing it has been authenticated, so
    a caller writing the output to disk never writes a byte an attacker chose.
    """
    header = read_header(stream)

    if header.key_id != key.key_id:
        raise WrongBackupKeyError(
            f"This backup was written for key {header.key_id}, and the key "
            f"offered is {key.key_id}. No decryption was attempted."
        )

    try:
        wrapped = base64.b64decode(str(header.fields["wrapped_dek"]))
        wrap_nonce = base64.b64decode(str(header.fields["wrap_nonce"]))
        stream_prefix = base64.b64decode(str(header.fields["stream_prefix"]))
    except (binascii.Error, ValueError) as exc:
        raise CorruptBackupError("The backup header contains malformed base64.") from exc
    if len(wrap_nonce) != _NONCE_BYTES or len(stream_prefix) != _STREAM_PREFIX_BYTES:
        raise CorruptBackupError("The backup header's nonce fields are the wrong length.")

    try:
        dek = AESGCM(key.material).decrypt(wrap_nonce, wrapped, MAGIC)
    except InvalidTag as exc:
        # The key identifier matched, so this is tampering rather than the
        # wrong key: someone edited the wrapped key or the magic.
        raise CorruptBackupError(
            "The backup's wrapped data key failed authentication. The header has been altered."
        ) from exc

    aead = AESGCM(dek)
    counter = 0
    while True:
        raw_len = stream.read(4)
        if not raw_len:
            raise TruncatedBackupError(
                "The backup ends without a final chunk. It was truncated — the "
                "writer was interrupted, or the file was copied incompletely."
            )
        if len(raw_len) != 4:
            raise TruncatedBackupError("The backup ends inside a chunk length.")
        (length,) = struct.unpack(">I", raw_len)
        if not _TAG_BYTES <= length <= _MAX_CHUNK_CIPHERTEXT:
            raise CorruptBackupError("A backup chunk declares an implausible length.")
        blob = stream.read(length)
        if len(blob) != length:
            raise TruncatedBackupError("The backup ends inside a chunk.")

        # Try the chunk as non-final, then as final. The flag is in the nonce,
        # so exactly one of the two authenticates — and which one it was tells
        # the reader whether the stream is allowed to end here.
        for final in (False, True):
            try:
                block = aead.decrypt(_nonce(stream_prefix, counter, final=final), blob, header.raw)
            except InvalidTag:
                continue
            counter += 1
            if block:
                yield block
            if final:
                trailing = stream.read(1)
                if trailing:
                    raise CorruptBackupError(
                        "The backup continues past its final chunk; bytes have been appended."
                    )
                return
            break
        else:
            raise CorruptBackupError(
                f"Chunk {counter} of the backup failed authentication. The file "
                "is corrupt, or chunks have been reordered or substituted."
            )


_SAFE_ID = re.compile(r"\A[0-9a-f]{16,64}\Z")


def is_safe_backup_id(value: str) -> bool:
    """Whether a backup identifier is the hex this tooling produces.

    Checked before an identifier reaches a filename, because the identifier is
    the one header field that flows outward into a path.
    """
    return bool(_SAFE_ID.match(value))
