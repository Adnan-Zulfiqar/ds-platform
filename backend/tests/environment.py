"""Constants the test harness binds into the environment before app import.

A separate module rather than a constant in `conftest.py` for one mechanical
reason: `app.core.config` builds its settings singleton at import time, so the
environment has to be set before the first application import — and the only
statements ruff permits ahead of imports are `os.environ` mutations, not a
module-level assignment. Putting the value here keeps it importable by both the
harness and the tests that assert against it, with one definition.
"""

from __future__ import annotations

from typing import Final

#: The OTP key the whole suite runs under, and the one a *deployed*-environment
#: fixture must carry.
#:
#: Fixed here rather than left to the shell. Without it the suite inherited
#: whatever `SECURITY_OTP_HMAC_KEY` a developer happened to have exported, and
#: the production-configuration tests passed or failed by accident: on a clean
#: machine four of them failed, because an unset key falls back to the
#: development default that a deployed environment refuses. A test whose result
#: depends on the shell is not a test.
#:
#: Deliberately neither the development default nor `SECURITY_SECRET_KEY` — the
#: guard rejects both, and a fixture that tripped over its own value would mask
#: whatever the test was actually about. The literal says what it is, so a grep
#: of a configuration file cannot mistake it for a real secret.
TEST_OTP_HMAC_KEY: Final[str] = "test-only-otp-hmac-key-NEVER-USE-IN-PROD-!!!"
