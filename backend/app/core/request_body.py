"""Bounded raw-body reading for public endpoints.

``await request.body()`` buffers the entire request before anyone can measure
it. On an authenticated route that is tolerable — the caller is known and the
server can be sized for them. On an *unauthenticated* route it means anyone who
learns the URL can make the process allocate as much memory as they care to
send, and the size check runs only after the damage is done.

This reader pulls ``request.stream()`` incrementally and stops the moment the
accumulated length would exceed the ceiling. "Stops" is the operative word: it
stops *asking* for more, so the sender is left holding the rest rather than the
server holding it in memory.

Three rules that are easy to get subtly wrong, so they are stated here:

* **``Content-Length`` is a hint, never the protection.** An honest oversized
  declaration is refused before a single byte is read, which is a cheap win. A
  dishonest small one is worthless, so the streaming bound runs regardless.
* **The bytes are returned exactly as received** — no decoding, no
  normalisation, no re-serialisation. A signature is over bytes, and anything
  that rewrites them breaks verification for reasons that are very hard to see.
* **A client disconnect is the client's problem.** Starlette raises
  ``ClientDisconnect`` from the stream; letting that escape turns a dropped
  connection into a 500 and a page of traceback.
"""

from __future__ import annotations

from typing import Final

from starlette.requests import ClientDisconnect, Request

from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestBodyTooLargeError(AppError):
    """The body exceeded the caller-supplied ceiling.

    413 rather than 400: the request was well-formed, it was simply too big,
    and a correctly-behaving sender can act on that.
    """

    code = "request_body_too_large"
    status_code = 413
    message = "The request body exceeded the permitted size."


class RequestDisconnectedError(AppError):
    """The client went away mid-body.

    A 4xx rather than a 5xx: nothing on the server went wrong, and reporting it
    as a server error would put every dropped mobile connection in the error
    budget.
    """

    code = "request_disconnected"
    status_code = 400
    message = "The request body was not fully received."


#: Read granularity. Small enough that the overshoot past the limit is bounded
#: by one chunk, large enough that a normal body costs a couple of iterations.
_CHUNK_HINT: Final = 8 * 1024


async def read_bounded_body(
    request: Request,
    *,
    max_bytes: int,
    reject_declared_oversize: bool = True,
) -> bytes:
    """Read at most ``max_bytes``, refusing anything larger.

    Returns the exact bytes received. Raises ``RequestBodyTooLargeError`` as
    soon as the limit is passed — before the rest of the body is requested —
    and ``RequestDisconnectedError`` if the peer vanishes part-way.

    ``reject_declared_oversize`` short-circuits on an honest ``Content-Length``.
    It is on by default and is strictly an optimisation: turning it off changes
    how quickly an oversized request is refused, never whether it is.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")

    if reject_declared_oversize:
        declared = _declared_length(request)
        if declared is not None and declared > max_bytes:
            # The only case where the header is worth acting on: the caller has
            # told us it is too big, so there is no reason to read anything.
            logger.warning(
                "request_body_rejected",
                reason="declared_content_length",
                declared=declared,
                limit=max_bytes,
            )
            raise RequestBodyTooLargeError(details={"limit": max_bytes})

    chunks: list[bytes] = []
    received = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > max_bytes:
                # Return without consuming the remainder. The generator is
                # abandoned here, which is what makes this a bound on memory
                # rather than a bound on the response.
                logger.warning(
                    "request_body_rejected",
                    reason="streamed_over_limit",
                    limit=max_bytes,
                )
                raise RequestBodyTooLargeError(details={"limit": max_bytes})
            chunks.append(chunk)
    except ClientDisconnect as exc:
        logger.info("request_body_disconnected", received=received)
        raise RequestDisconnectedError() from exc

    return b"".join(chunks)


def _declared_length(request: Request) -> int | None:
    """Parse ``Content-Length``, treating anything odd as absent.

    A malformed or negative value is not an error worth its own status — the
    streaming bound covers it either way, and rejecting here would give a
    different answer to a broken client than to a hostile one for no gain.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


__all__ = [
    "RequestBodyTooLargeError",
    "RequestDisconnectedError",
    "read_bounded_body",
]
