"""PROD-H1: the production-readiness rules, and the CLI that reports them.

Two things are being guarded here, and they are different.

**That each rule fires.** Straightforward, and the reason each test breaks
exactly one setting: a failure names the control that stopped working.

**That the rules and the application cannot disagree.** The operator CLI exists
to answer "would this configuration boot?" without booting it. That answer is
worthless the moment the two drift, so `TestTheCliAndStartupCannotDrift` walks
the shared table and, for every rule marked as enforced at startup, breaks that
one setting and asserts *both* that the evaluator reports FAIL and that
constructing `Settings` raises. Not "they look similar" — the same
configurations, judged by both.

Nothing here connects to Postgres, Redis or any network service.
"""

from __future__ import annotations

import base64
import codecs
import io
import os
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.core.config import SecuritySettings, Settings
from app.core.production_readiness import (
    RULES,
    Classification,
    Finding,
    Status,
    evaluate,
    startup_failure,
)
from tests.environment import TEST_OTP_HMAC_KEY

pytestmark = pytest.mark.unit

VALID_FERNET = base64.urlsafe_b64encode(b"prod-h1-key-32-bytes-exactly!!!!").decode()
VALID_SIGNING_KEY = "prod-h1-signing-key-not-a-real-secret-value"
VALID_OTP_KEY = "prod-h1-otp-key-distinct-from-the-signing-key"

#: A deployed configuration with nothing wrong with it. Every test below breaks
#: exactly one setting, so a failure names the control rather than the fixture.
#: None of these values is a real secret; each is obviously synthetic.
READY: dict[str, str] = {
    "ENVIRONMENT": "production",
    "ALLOWED_HOSTS": "api.example.invalid",
    "CORS_ORIGINS": "https://app.example.invalid",
    "POSTGRES_DB": "droppilot",
    "SECURITY_SECRET_KEY": VALID_SIGNING_KEY,
    "SECURITY_ENCRYPTION_KEYS": VALID_FERNET,
    "SECURITY_OTP_HMAC_KEY": VALID_OTP_KEY,
    "SECURITY_COOKIE_SECURE": "true",
    "SECURITY_TRUSTED_PROXIES": "10.0.0.0/8",
    "LOG_INCLUDE_REQUEST_BODY": "false",
    "NEXT_PUBLIC_API_URL": "https://api.example.invalid",
    "SHOPIFY_FRONTEND_RETURN_URL": "https://app.example.invalid/settings/integrations",
    "ALIEXPRESS_FRONTEND_RETURN_URL": "https://app.example.invalid/settings/integrations",
    "EBAY_FRONTEND_RETURN_URL": "https://app.example.invalid/settings/integrations",
}

#: Every application variable the harness must clear so a developer's shell
#: cannot decide the outcome of a test about configuration.
_PREFIXES = (
    "ENVIRONMENT",
    "ALLOWED_HOSTS",
    "CORS_ORIGINS",
    "POSTGRES_",
    "REDIS_",
    "SECURITY_",
    "EMAIL_",
    "RESEND_",
    "GOOGLE_",
    "EBAY_",
    "SHOPIFY_",
    "ALIEXPRESS_",
    "LOG_",
    "NEXT_PUBLIC_",
    "AI_",
)


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Bind a valid deployed environment, isolated from the developer's own."""
    for name in list(os.environ):
        if name.startswith(_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    for name, value in READY.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def settings_with(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> Settings:
    """A `Settings` built from `READY` plus overrides, constructed as local.

    Constructed as `local` so the object exists even when the configuration is
    one a deployed environment would refuse — the rules are what judge it, and
    they cannot judge an object that could not be built. The environment is then
    restored to what the test declared.
    """
    from app.core.config import Environment

    declared = overrides.pop("ENVIRONMENT", READY["ENVIRONMENT"])
    for name, value in overrides.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENVIRONMENT", "local")
    settings = Settings()
    object.__setattr__(settings, "environment", Environment(declared or "local"))
    return settings


def finding_for(report: Any, setting: str) -> Finding:
    matches = [f for f in report.findings if f.setting == setting]
    assert matches, f"no finding for {setting}"
    return matches[0]


# ---------------------------------------------------------------------------
# The happy path, first — otherwise every test below could pass against a
# validator that simply rejects everything.
# ---------------------------------------------------------------------------


class TestAValidDeployedConfiguration:
    def test_every_required_now_rule_passes(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed))

        failed = [
            f.setting
            for f in report.findings
            if f.classification is Classification.REQUIRED_NOW and f.status is Status.FAIL
        ]
        assert not failed, failed

    def test_it_would_start(self, deployed: Any) -> None:
        """The other half: the rules and the application agree it is fit."""
        deployed.setenv("ENVIRONMENT", "production")
        Settings()

    def test_the_only_remaining_findings_are_activation_ones(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed))

        for finding in report.findings:
            if finding.status in {Status.BLOCKED, Status.MISSING, Status.SKIPPED}:
                assert finding.classification is Classification.REQUIRED_AT_ACTIVATION, finding


class TestEnvironmentMustBeDeployed:
    @pytest.mark.parametrize("value", ["local", "test"])
    def test_a_development_environment_is_rejected(self, deployed: Any, value: str) -> None:
        """This is the defect PROD-H1 was opened for.

        A production host running as `local` has every one of its guards
        switched off, silently, while looking entirely healthy.
        """
        report = evaluate(settings_with(deployed, ENVIRONMENT=value))

        finding = finding_for(report, "ENVIRONMENT")
        assert finding.status is Status.FAIL
        assert "not a deployed environment" in finding.reason

    @pytest.mark.parametrize("value", ["production", "staging"])
    def test_a_deployed_environment_is_accepted(self, deployed: Any, value: str) -> None:
        report = evaluate(settings_with(deployed, ENVIRONMENT=value))
        assert finding_for(report, "ENVIRONMENT").status is Status.PASS


class TestSecretRules:
    def test_the_published_signing_placeholder_is_rejected(self, deployed: Any) -> None:
        report = evaluate(
            settings_with(deployed, SECURITY_SECRET_KEY=SecuritySettings.LOCAL_PLACEHOLDER_KEY)
        )
        assert finding_for(report, "SECURITY_SECRET_KEY").status is Status.FAIL

    def test_a_missing_otp_key_is_rejected(self, deployed: Any) -> None:
        """Unset is not neutral: the field falls back to the published default."""
        report = evaluate(settings_with(deployed, SECURITY_OTP_HMAC_KEY=None))
        finding = finding_for(report, "SECURITY_OTP_HMAC_KEY")
        assert finding.status is Status.FAIL
        assert "development default" in finding.reason

    def test_an_empty_otp_key_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_OTP_HMAC_KEY=""))
        assert finding_for(report, "SECURITY_OTP_HMAC_KEY").status is Status.FAIL

    def test_the_default_otp_key_is_rejected(self, deployed: Any) -> None:
        report = evaluate(
            settings_with(deployed, SECURITY_OTP_HMAC_KEY=SecuritySettings.INSECURE_OTP_KEY_DEFAULT)
        )
        assert finding_for(report, "SECURITY_OTP_HMAC_KEY").status is Status.FAIL

    def test_an_otp_key_equal_to_the_signing_key_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_OTP_HMAC_KEY=VALID_SIGNING_KEY))
        finding = finding_for(report, "SECURITY_OTP_HMAC_KEY")
        assert finding.status is Status.FAIL
        assert "same value as SECURITY_SECRET_KEY" in finding.reason

    def test_missing_encryption_keys_are_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_ENCRYPTION_KEYS=""))
        assert finding_for(report, "SECURITY_ENCRYPTION_KEYS").status is Status.FAIL

    def test_an_invalid_encryption_key_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_ENCRYPTION_KEYS="not-a-fernet-key"))
        assert finding_for(report, "SECURITY_ENCRYPTION_KEYS").status is Status.FAIL

    @pytest.mark.parametrize("published", sorted(SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS))
    def test_a_published_encryption_key_is_rejected(self, deployed: Any, published: str) -> None:
        report = evaluate(settings_with(deployed, SECURITY_ENCRYPTION_KEYS=published))
        finding = finding_for(report, "SECURITY_ENCRYPTION_KEYS")
        assert finding.status is Status.FAIL
        assert "published in this repository" in finding.reason


class TestBrowserFacingRules:
    def test_an_insecure_refresh_cookie_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_COOKIE_SECURE="false"))
        assert finding_for(report, "SECURITY_COOKIE_SECURE").status is Status.FAIL

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:3000",
            "http://127.0.0.1:8000",
            "https://localhost:3000",
            "http://api.example.invalid",
        ],
    )
    def test_a_loopback_or_plaintext_api_url_is_rejected(self, deployed: Any, url: str) -> None:
        report = evaluate(settings_with(deployed, NEXT_PUBLIC_API_URL=url))
        assert finding_for(report, "NEXT_PUBLIC_API_URL").status is Status.FAIL

    def test_a_missing_cors_origin_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, CORS_ORIGINS=""))
        assert finding_for(report, "CORS_ORIGINS").status is Status.FAIL

    def test_a_loopback_cors_origin_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, CORS_ORIGINS="http://localhost:3000"))
        finding = finding_for(report, "CORS_ORIGINS")
        assert finding.status is Status.FAIL
        assert "loopback" in finding.reason

    def test_a_wildcard_allowed_host_is_rejected(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, ALLOWED_HOSTS="*"))
        assert finding_for(report, "ALLOWED_HOSTS").status is Status.FAIL

    def test_a_loopback_oauth_return_url_is_rejected(self, deployed: Any) -> None:
        report = evaluate(
            settings_with(deployed, SHOPIFY_FRONTEND_RETURN_URL="http://localhost:3000/x")
        )
        assert finding_for(report, "SHOPIFY_FRONTEND_RETURN_URL").status is Status.FAIL


class TestActivationPhasesAreNotConfused:
    """An absent optional integration is not a defect. Saying it is trains an
    operator to skim past the report, which is how a real defect gets missed."""

    def test_google_disabled_is_skipped_not_failed(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, GOOGLE_OAUTH_CLIENT_ID=None))
        finding = finding_for(report, "GOOGLE_OAUTH_CLIENT_ID")
        assert finding.status is Status.SKIPPED
        assert "not a defect" in finding.reason

    def test_google_partially_configured_is_a_failure(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, GOOGLE_OAUTH_CLIENT_ID="short"))
        assert finding_for(report, "GOOGLE_OAUTH_CLIENT_ID").status is Status.FAIL

    def test_email_disabled_is_skipped(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, EMAIL_PROVIDER="stub"))
        assert finding_for(report, "EMAIL_PROVIDER").status is Status.SKIPPED

    def test_email_enabled_without_a_key_is_missing_not_skipped(self, deployed: Any) -> None:
        """The dangerous middle state: the first reset fails, for somebody who
        is already locked out of their account."""
        report = evaluate(
            settings_with(
                deployed,
                EMAIL_PROVIDER="resend",
                RESEND_API_KEY=None,
                EMAIL_FROM="DropPilot <security@auth.whiteto.com>",
            )
        )
        assert finding_for(report, "EMAIL_PROVIDER").status is Status.PASS
        assert finding_for(report, "RESEND_API_KEY").status is Status.MISSING

    def test_email_from_an_unverified_domain_is_rejected(self, deployed: Any) -> None:
        report = evaluate(
            settings_with(
                deployed,
                EMAIL_PROVIDER="resend",
                RESEND_API_KEY="not-a-real-key-for-tests-only",
                EMAIL_FROM="DropPilot <hello@example.invalid>",
            )
        )
        finding = finding_for(report, "EMAIL_FROM")
        assert finding.status is Status.FAIL
        assert "auth.whiteto.com" in finding.reason

    def test_unpublished_terms_are_a_publication_block_not_a_config_error(
        self, deployed: Any
    ) -> None:
        report = evaluate(settings_with(deployed))
        finding = finding_for(report, "TERMS_PUBLISHED")

        assert finding.status is Status.BLOCKED
        assert finding.status is not Status.FAIL
        assert "must not be resolved by editing" in finding.reason

    def test_backups_are_a_publication_block(self, deployed: Any) -> None:
        assert finding_for(evaluate(settings_with(deployed)), "BACKUPS").status is Status.BLOCKED


class TestExitCodes:
    def test_a_ready_configuration_is_not_zero_while_terms_are_unpublished(
        self, deployed: Any
    ) -> None:
        """Honest, and the reason BLOCKED is not PASS.

        Everything in the file is correct and the service still must not open
        for business, so the exit code says `publication blocked` rather than
        `ready`.
        """
        assert evaluate(settings_with(deployed)).exit_code == 3

    def test_an_invalid_configuration_outranks_a_publication_block(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, SECURITY_COOKIE_SECURE="false"))
        assert report.exit_code == 2

    def test_a_missing_dependency_outranks_nothing_but_ready(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed, EBAY_FRONTEND_RETURN_URL=None))
        assert Status.MISSING in {f.status for f in report.findings}
        # Terms are still unpublished, so the more severe verdict wins.
        assert report.exit_code == 3


class TestNoValueEverEscapes:
    """A configuration validator that printed what it validated would be a new
    way to leak the secrets it exists to protect."""

    SENSITIVE = (
        VALID_SIGNING_KEY,
        VALID_OTP_KEY,
        VALID_FERNET,
        SecuritySettings.LOCAL_PLACEHOLDER_KEY,
        SecuritySettings.INSECURE_OTP_KEY_DEFAULT,
        TEST_OTP_HMAC_KEY,
    )

    def test_no_finding_reason_contains_a_secret(self, deployed: Any) -> None:
        report = evaluate(settings_with(deployed))
        blob = " ".join(f"{f.setting} {f.reason}" for f in report.findings)

        for secret in self.SENSITIVE:
            assert secret not in blob, "a secret reached a finding"

    def test_a_broken_configuration_still_leaks_nothing(self, deployed: Any) -> None:
        report = evaluate(
            settings_with(
                deployed,
                SECURITY_SECRET_KEY=SecuritySettings.LOCAL_PLACEHOLDER_KEY,
                SECURITY_OTP_HMAC_KEY=SecuritySettings.INSECURE_OTP_KEY_DEFAULT,
            )
        )
        blob = " ".join(f.reason for f in report.findings)

        for secret in self.SENSITIVE:
            assert secret not in blob

    def test_a_finding_that_interpolates_a_value_cannot_be_constructed(self) -> None:
        """The structural guard, not a convention somebody must remember."""
        with pytest.raises(ValueError, match="interpolated"):
            Finding("SECURITY_SECRET_KEY", Status.FAIL, "SECURITY_SECRET_KEY=hunter2")


class TestTheCliAndStartupCannotDrift:
    """The property the whole milestone rests on.

    For every rule the table marks as enforced at startup, break that one
    setting and assert both readers agree: the evaluator reports FAIL, and
    constructing `Settings` refuses. Not "the code looks shared" — the same
    configurations, judged by both.
    """

    #: One way to break each startup-enforced rule.
    BREAKAGES: ClassVar[dict[str, dict[str, str | None]]] = {
        "SECURITY_SECRET_KEY": {"SECURITY_SECRET_KEY": SecuritySettings.LOCAL_PLACEHOLDER_KEY},
        "ALLOWED_HOSTS": {"ALLOWED_HOSTS": "*"},
        "LOG_INCLUDE_REQUEST_BODY": {"LOG_INCLUDE_REQUEST_BODY": "true"},
        "SECURITY_ENCRYPTION_KEYS": {"SECURITY_ENCRYPTION_KEYS": ""},
        "SECURITY_OTP_HMAC_KEY": {
            "SECURITY_OTP_HMAC_KEY": SecuritySettings.INSECURE_OTP_KEY_DEFAULT
        },
        "SECURITY_COOKIE_SECURE": {"SECURITY_COOKIE_SECURE": "false"},
        "GOOGLE_OAUTH_CLIENT_ID": {"GOOGLE_OAUTH_CLIENT_ID": "short"},
        "EBAY_MARKETPLACE_DELETION_ENDPOINT": {
            "EBAY_MARKETPLACE_DELETION_ENDPOINT": "http://localhost/hook"
        },
    }

    def test_every_startup_enforced_rule_has_a_breakage_case(self) -> None:
        """So a rule added later cannot quietly escape this class."""
        enforced = {r.setting for r in RULES if r.enforced_at_startup}
        assert enforced == set(self.BREAKAGES), enforced ^ set(self.BREAKAGES)

    @pytest.mark.parametrize("setting", sorted(BREAKAGES))
    def test_the_evaluator_and_the_application_agree(self, deployed: Any, setting: str) -> None:
        overrides = self.BREAKAGES[setting]

        report = evaluate(settings_with(deployed, **overrides))
        assert finding_for(report, setting).status is Status.FAIL, (
            f"the evaluator accepted a configuration the application refuses ({setting})"
        )

        for name, value in overrides.items():
            deployed.setenv(name, value or "")
        deployed.setenv("ENVIRONMENT", "production")
        with pytest.raises(ValueError):
            Settings()

    def test_startup_failure_reports_nothing_for_a_valid_configuration(self, deployed: Any) -> None:
        assert startup_failure(settings_with(deployed)) is None

    def test_startup_failure_never_returns_a_secret(self, deployed: Any) -> None:
        message = startup_failure(
            settings_with(deployed, SECURITY_SECRET_KEY=SecuritySettings.LOCAL_PLACEHOLDER_KEY)
        )
        assert message is not None
        assert SecuritySettings.LOCAL_PLACEHOLDER_KEY not in message


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def write_env(tmp_path: Path, values: dict[str, str], *, extra: str = "") -> Path:
    path = tmp_path / ".env"
    body = "\n".join(f"{k}={v}" for k, v in values.items())
    path.write_text(f"{body}\n{extra}", encoding="utf-8")
    return path


def run_cli(
    path: Path, *, environment: str = "production", database: str = "droppilot"
) -> tuple[int, str, str]:
    from scripts.verify_production_config import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(
            [
                "--env-file",
                str(path),
                "--expect-environment",
                environment,
                "--expect-database",
                database,
            ]
        )
    return code, out.getvalue(), err.getvalue()


class TestTheOperatorCli:
    def test_a_ready_file_is_publication_blocked_not_ready(
        self, tmp_path: Path, deployed: Any
    ) -> None:
        code, out, _ = run_cli(write_env(tmp_path, READY))

        assert code == 3, out
        assert "PUBLICATION BLOCKED" in out

    def test_a_local_environment_is_refused_for_production(
        self, tmp_path: Path, deployed: Any
    ) -> None:
        code, out, _ = run_cli(write_env(tmp_path, {**READY, "ENVIRONMENT": "local"}))

        assert code == 2
        assert "CONFIGURATION INVALID" in out
        assert "development value" in out

    def test_the_wrong_database_is_refused(self, tmp_path: Path, deployed: Any) -> None:
        code, out, _ = run_cli(write_env(tmp_path, READY), database="droppilot_staging")

        assert code == 2
        assert "names a different database" in out

    def test_a_malformed_file_fails_closed(self, tmp_path: Path, deployed: Any) -> None:
        path = write_env(tmp_path, READY, extra="this line has no equals sign\n")
        code, _, err = run_cli(path)

        assert code == 1
        assert "malformed" in err

    def test_a_malformed_line_is_reported_by_number_not_by_text(
        self, tmp_path: Path, deployed: Any
    ) -> None:
        """Its text is exactly as likely to hold a secret as any other line."""
        path = write_env(tmp_path, READY, extra="a-secret-looking-line-with-no-equals\n")
        _, _, err = run_cli(path)

        assert "a-secret-looking-line" not in err

    def test_a_missing_file_is_a_usage_error_not_a_verdict(self, tmp_path: Path) -> None:
        code, _, err = run_cli(tmp_path / "absent.env")

        assert code == 1, "a missing file must not be reported as a configuration verdict"
        assert "not a file" in err

    def test_ambient_variables_cannot_silently_override_the_file(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shell that has exported a setting would otherwise produce a
        confident answer about a configuration that is not on disk."""
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "false")
        monkeypatch.setenv("POSTGRES_DB", "some_review_database")

        code, out, _ = run_cli(write_env(tmp_path, READY))

        # The file says the cookie is secure and the database is droppilot, and
        # the file wins.
        assert "matches the expected database name" in out
        assert code == 3
        # And the operator is told their shell was interfering.
        assert "AMBIENT OVERRIDES REMOVED" in out
        assert "SECURITY_COOKIE_SECURE" in out

    def test_no_configuration_value_reaches_stdout_or_stderr(
        self, tmp_path: Path, deployed: Any
    ) -> None:
        _, out, err = run_cli(write_env(tmp_path, READY))
        blob = out + err

        for secret in (VALID_SIGNING_KEY, VALID_OTP_KEY, VALID_FERNET):
            assert secret not in blob, "the CLI printed a configuration value"

    def test_broken_secrets_are_named_but_never_shown(self, tmp_path: Path, deployed: Any) -> None:
        values = {
            **READY,
            "SECURITY_SECRET_KEY": SecuritySettings.LOCAL_PLACEHOLDER_KEY,
            "SECURITY_OTP_HMAC_KEY": SecuritySettings.INSECURE_OTP_KEY_DEFAULT,
        }
        code, out, err = run_cli(write_env(tmp_path, values))

        assert code == 2
        assert "SECURITY_SECRET_KEY" in out
        assert SecuritySettings.LOCAL_PLACEHOLDER_KEY not in out + err
        assert SecuritySettings.INSECURE_OTP_KEY_DEFAULT not in out + err

    def test_the_report_marks_which_settings_are_secret(
        self, tmp_path: Path, deployed: Any
    ) -> None:
        _, out, _ = run_cli(write_env(tmp_path, READY))

        assert "* marks a secret" in out
        assert "No value has been printed" in out

    def test_the_runbook_documents_the_exit_codes(self) -> None:
        """A number nobody can look up is not a contract."""
        runbook = (
            Path(__file__).resolve().parents[3] / "docs" / "operations" / "PRODUCTION_CONFIG.md"
        ).read_text(encoding="utf-8")

        for code in ("0", "1", "2", "3", "4"):
            assert f"| {code} " in runbook, f"exit code {code} is not in the runbook table"
        assert textwrap.dedent("verify_production_config.py") in runbook


# ---------------------------------------------------------------------------
# PROD-H1-R1 — the review findings
# ---------------------------------------------------------------------------


class TestTheAmbientScrubIsTakenOnce:
    """The defect: `_scrub_environment()` was called twice.

    The first call removed the variables; the second therefore found nothing, so
    `ambient_only` was silently always empty and an operator whose shell held a
    stray `SECURITY_SECRET_KEY` was never told. Every test below fails against
    that version.
    """

    def test_a_shell_only_variable_is_removed_and_reported(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDIS_PASSWORD", "an-ambient-value-that-must-not-be-shown")

        code, out, err = run_cli(write_env(tmp_path, READY))

        assert "AMBIENT ONLY" in out
        assert "~ REDIS_PASSWORD" in out
        assert "an-ambient-value-that-must-not-be-shown" not in out + err
        assert code == 3

    def test_a_variable_in_both_shell_and_file_is_reported_as_overridden(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "false")

        _, out, _ = run_cli(write_env(tmp_path, READY))

        assert "AMBIENT OVERRIDES REMOVED" in out
        assert "! SECURITY_COOKIE_SECURE" in out
        # The file, which says true, is what was judged.
        assert "is true." in out

    def test_variables_across_every_prefix_are_classified_correctly(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One from each family the application reads."""
        in_file = {
            "SECURITY_COOKIE_SECURE": "false",
            "POSTGRES_DB": "some_other_database",
            "EBAY_FRONTEND_RETURN_URL": "http://localhost:3000/x",
        }
        shell_only = {
            "REDIS_PASSWORD": "ambient-redis",
            "GOOGLE_OAUTH_CLIENT_ID": "ambient-google",
            "EMAIL_PROVIDER": "resend",
            "RESEND_API_KEY": "ambient-resend",
        }
        for name, value in {**in_file, **shell_only}.items():
            monkeypatch.setenv(name, value)

        _, out, err = run_cli(write_env(tmp_path, READY))

        overridden_block, _, ambient_block = out.partition("AMBIENT ONLY")
        for name in in_file:
            assert f"! {name}" in overridden_block, name
        for name in shell_only:
            assert f"~ {name}" in ambient_block, name
        for value in {**in_file, **shell_only}.values():
            if len(value) > 8:
                assert value not in out + err, "an ambient value was printed"

    def test_unrelated_shell_variables_are_neither_removed_nor_reported(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tool has no business touching the rest of the environment."""
        monkeypatch.setenv("PROD_H1_UNRELATED", "left-alone")
        monkeypatch.setenv("MY_COMPANY_TOKEN", "also-left-alone")

        _, out, err = run_cli(write_env(tmp_path, READY))

        assert os.environ.get("PROD_H1_UNRELATED") == "left-alone"
        assert os.environ.get("MY_COMPANY_TOKEN") == "also-left-alone"
        assert "PROD_H1_UNRELATED" not in out + err
        assert "MY_COMPANY_TOKEN" not in out + err

    def test_ordering_is_deterministic_across_runs(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A report that reorders itself cannot be diffed between runs."""
        for name in ("REDIS_PASSWORD", "AI_PROVIDER", "GOOGLE_OAUTH_CLIENT_ID", "FX_API_KEY"):
            monkeypatch.setenv(name, "a-value")
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "false")
        monkeypatch.setenv("POSTGRES_DB", "elsewhere")

        path = write_env(tmp_path, READY)
        _, first, _ = run_cli(path)
        _, second, _ = run_cli(path)

        assert first == second
        ambient_block = first.partition("AMBIENT ONLY")[2].partition("Expectations")[0]
        names = [line.split("~ ")[1].strip() for line in ambient_block.splitlines() if "~ " in line]
        assert names == sorted(names)

    def test_a_second_invocation_sees_a_restored_environment(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI loads the file into `os.environ`; it must put it back.

        Otherwise the second run in a process audits the first run's leftovers
        while reporting the second one's filename.
        """
        monkeypatch.setenv("REDIS_PASSWORD", "ambient")
        before = dict(os.environ)

        run_cli(write_env(tmp_path, READY))

        assert dict(os.environ) == before, "the environment was not restored"

        _, out, _ = run_cli(write_env(tmp_path, READY))
        assert "~ REDIS_PASSWORD" in out, "the second run lost the ambient inventory"
        assert dict(os.environ) == before

    def test_the_environment_is_restored_even_when_the_run_fails(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDIS_PASSWORD", "ambient")
        before = dict(os.environ)

        code, _, _ = run_cli(write_env(tmp_path, {**READY, "ENVIRONMENT": "nonsense"}))

        assert code == 2
        assert dict(os.environ) == before, "an error path leaked the loaded configuration"

    def test_the_snapshot_is_taken_exactly_once(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The structural guard on the defect itself.

        `AmbientScrub.take` removes what it classifies, so a second call returns
        two empty tuples — which is precisely how the bug hid. Counting the calls
        states the invariant directly.
        """
        import scripts.verify_production_config as cli

        calls = {"n": 0}
        original = cli.AmbientScrub.take

        def counting(file_keys: Any) -> Any:
            calls["n"] += 1
            return original(file_keys)

        monkeypatch.setattr(cli.AmbientScrub, "take", counting)
        run_cli(write_env(tmp_path, READY))

        assert calls["n"] == 1, f"the environment was scrubbed {calls['n']} times"

    def test_both_categories_come_from_one_snapshot(
        self, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The API makes the old mistake unavailable: one call, both answers."""
        from scripts.verify_production_config import AmbientScrub

        monkeypatch.setenv("REDIS_PASSWORD", "ambient")
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "true")

        scrub = AmbientScrub.take(frozenset({"SECURITY_COOKIE_SECURE"}))
        try:
            assert "SECURITY_COOKIE_SECURE" in scrub.overridden
            assert "REDIS_PASSWORD" in scrub.ambient_only
            # A second call is not how the other half is obtained — it has
            # nothing left to find, which is exactly the trap the old shape set.
            second = AmbientScrub.take(frozenset({"SECURITY_COOKIE_SECURE"}))
            assert second.overridden == ()
            assert second.ambient_only == ()
        finally:
            scrub.restore()

    @pytest.mark.parametrize(
        "value",
        [
            "has=an=equals",
            "has:a:colon",
            'has"double"quotes',
            "has'single'quotes",
            "påsswörd-ünicode-ключ",
            "sk-livekeyshapedmaterial0123456789",
            "",
        ],
        ids=[
            "equals",
            "colon",
            "double-quote",
            "single-quote",
            "unicode",
            "secret-shaped",
            "empty",
        ],
    )
    def test_no_ambient_value_of_any_shape_reaches_the_output(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("REDIS_PASSWORD", value)

        _, out, err = run_cli(write_env(tmp_path, READY))

        assert "~ REDIS_PASSWORD" in out, "the variable was not classified"
        if value:
            assert value not in out + err

    def test_an_empty_ambient_variable_is_still_classified(
        self, tmp_path: Path, deployed: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty is a value, and an empty override still shadows the file."""
        monkeypatch.setenv("SECURITY_OTP_HMAC_KEY", "")

        _, out, _ = run_cli(write_env(tmp_path, READY))

        assert "! SECURITY_OTP_HMAC_KEY" in out
        # The file's key was used, so the rule passes.
        assert "distinct from the signing key" in out


#: The UTF-8 byte-order mark a Windows editor writes at the start of a file.
BOM = codecs.BOM_UTF8


class TestAByteOrderMarkIsTolerated:
    """Windows editors add one routinely, and it lands on line 1.

    Without handling it the first key parses as a name beginning with U+FEFF, so
    a file whose first line declares the environment reads as declaring none —
    and the tool reports a problem the operator cannot see in their editor.
    """

    @staticmethod
    def write_bom(tmp_path: Path, *, newline: str = "\n", extra: str = "") -> Path:
        path = tmp_path / ".env"
        body = newline.join(f"{k}={v}" for k, v in READY.items())
        path.write_bytes(b"\xef\xbb\xbf" + (body + newline + extra).encode("utf-8"))
        return path

    def test_a_bom_does_not_break_the_first_line(self, tmp_path: Path, deployed: Any) -> None:
        code, out, err = run_cli(self.write_bom(tmp_path))

        assert "matches the expected environment" in out, out + err
        assert code == 3

    def test_a_bom_with_crlf_line_endings_parses(self, tmp_path: Path, deployed: Any) -> None:
        """The combination a Windows editor actually produces."""
        code, out, err = run_cli(self.write_bom(tmp_path, newline="\r\n"))

        assert "matches the expected environment" in out, out + err
        assert "matches the expected database name" in out
        assert code == 3

    def test_a_bom_does_not_hide_a_malformed_line(self, tmp_path: Path, deployed: Any) -> None:
        path = self.write_bom(tmp_path, newline="\r\n", extra="a-line-with-no-equals\r\n")

        code, _, err = run_cli(path)

        assert code == 1
        assert "malformed" in err
        assert "a-line-with-no-equals" not in err

    def test_only_a_leading_mark_is_removed(self, tmp_path: Path) -> None:
        """Inside a value a U+FEFF is data, not a mark.

        Asserted against the parser rather than through `Settings`, because the
        parser owns the encoding decision — and because an interior mark in a
        *typed* setting would be rejected by that setting's own validation,
        which would prove nothing about the mark.

        `utf-8-sig` removes one at the start and nothing else, which is why the
        fix is an encoding rather than a global replace.
        """
        from scripts.verify_production_config import _parse_env_file

        path = tmp_path / ".env"
        body = "ENVIRONMENT=production\nEMAIL_FROM=a﻿b\n"
        path.write_bytes(BOM + body.encode("utf-8"))

        values, malformed = _parse_env_file(path)

        assert not malformed
        # The leading mark is gone: the key is ENVIRONMENT, not one prefixed
        # with U+FEFF.
        assert "ENVIRONMENT" in values
        assert not any(key.startswith("﻿") for key in values)
        # The interior one survives, because it belongs to the value.
        assert values["EMAIL_FROM"] == "a﻿b"

    def test_a_leading_mark_is_what_would_otherwise_break_the_key(self, tmp_path: Path) -> None:
        """The failure this fix prevents, stated as its counterfactual."""
        from scripts.verify_production_config import _parse_env_file

        path = tmp_path / ".env"
        path.write_bytes(BOM + b"ENVIRONMENT=production\n")

        # Read as plain utf-8, the first key carries the mark and is therefore
        # not ENVIRONMENT at all — so the file would read as declaring none.
        naive = path.read_text(encoding="utf-8").splitlines()[0].partition("=")[0]
        assert naive != "ENVIRONMENT"
        assert naive.startswith("﻿")

        # Read as the tool reads it, it is.
        assert "ENVIRONMENT" in _parse_env_file(path)[0]


class TestTheReasonContractRejectsInnocentText:
    """The equals ban is blunter than the leak it prevents, on purpose.

    A rule needing judgement gets talked past; this one cannot be. These pin the
    cost, so nobody relaxes it later for author ergonomics.
    """

    @pytest.mark.parametrize(
        "reason",
        [
            "must be >= 32 characters",
            "must be <= 100 characters",
            "is not == the signing key",
            "should read SECURITY_COOKIE_SECURE=true",
            "ends with base64 padding such as abc=",
        ],
        ids=["gte", "lte", "eq", "example-assignment", "padding"],
    )
    def test_innocent_equals_text_is_still_refused(self, reason: str) -> None:
        with pytest.raises(ValueError, match="interpolated configuration value"):
            Finding("SECURITY_SECRET_KEY", Status.FAIL, reason)

    def test_the_prose_alternatives_are_accepted(self) -> None:
        """The cost is a sentence, not a capability."""
        Finding("SECURITY_SECRET_KEY", Status.FAIL, "is shorter than 32 characters")
        Finding("SECURITY_COOKIE_SECURE", Status.FAIL, "is false.")

    def test_no_shipped_rule_reason_contains_an_equals_sign(self, deployed: Any) -> None:
        """Every reason the table can emit, checked rather than assumed."""
        report = evaluate(settings_with(deployed))
        for finding in report.findings:
            assert "=" not in finding.reason, finding.setting

    def test_the_contract_is_documented_where_an_author_will_look(self) -> None:
        source = (
            Path(__file__).resolve().parents[2] / "app" / "core" / "production_readiness.py"
        ).read_text(encoding="utf-8")

        assert "no `=`, anywhere, for any purpose" in source
        assert "at least" in source
