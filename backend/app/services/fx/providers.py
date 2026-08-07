"""FX rate provider implementations.

Production never invents a 1:1 rate when currencies differ. The default
``UnavailableFXRateProvider`` forces callers to surface a blocking error until
a real feed is configured. ``StubFXRateProvider`` exists only for tests.
``OpenExchangeRatesProvider`` is the first production provider.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.core.exceptions import FxProviderTimeoutError, FxRateInvalidError
from app.core.logging import get_logger
from app.domain.fx import FxRateQuote, FxRateStatus
from app.domain.money import normalise_currency

logger = get_logger(__name__)


class UnavailableFXRateProvider:
    """Default provider — always reports unavailable for cross-currency pairs."""

    @property
    def provider_name(self) -> str:
        return "unavailable"

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        _ = base_currency, quote_currency, at_or_before
        return None


class StubFXRateProvider:
    """Deterministic rates for unit/integration tests — never used in production."""

    def __init__(self, rates: dict[tuple[str, str], Decimal] | None = None) -> None:
        self._rates = {
            (normalise_currency(b), normalise_currency(q)): Decimal(str(r))
            for (b, q), r in (rates or {}).items()
        }
        defaults = {
            ("CNY", "GBP"): Decimal("0.1100"),
            ("CNY", "USD"): Decimal("0.1400"),
            ("USD", "GBP"): Decimal("0.7800"),
            ("GBP", "USD"): Decimal("1.2800"),
            ("USD", "EUR"): Decimal("0.9200"),
        }
        for key, value in defaults.items():
            self._rates.setdefault(key, value)

    @property
    def provider_name(self) -> str:
        return "stub"

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        _ = at_or_before
        base = normalise_currency(base_currency)
        quote = normalise_currency(quote_currency)
        if base == quote:
            return None
        rate = self._rates.get((base, quote))
        derivation = "direct"
        if rate is None:
            inverse = self._rates.get((quote, base))
            if inverse is None or inverse == 0:
                return None
            rate = (Decimal("1") / inverse).quantize(Decimal("0.00000001"))
            derivation = "inverted"
        now = datetime.now(UTC)
        return FxRateQuote(
            base_currency=base,
            quote_currency=quote,
            rate=rate,
            provider_name=self.provider_name,
            fetched_at=now,
            provider_timestamp=now,
            expires_at=now + timedelta(minutes=60),
            status=FxRateStatus.CURRENT,
            derivation=derivation,
        )


class OpenExchangeRatesProvider:
    """Open Exchange Rates production feed.

    Does not invent conversions. Safe inversion of a direct pair is allowed.
    Explicit USD cross (``via_usd``) is used only when the account cannot serve
    the requested base and both USD legs are present — never silently.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openexchangerates.org/api",
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @property
    def provider_name(self) -> str:
        return "openexchangerates"

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        _ = at_or_before
        base = normalise_currency(base_currency)
        quote = normalise_currency(quote_currency)
        if base == quote:
            return None
        if not self._api_key:
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                reason="missing_api_key",
            )
            return None

        fetched_at = datetime.now(UTC)
        try:
            direct = await self._fetch_latest(base=base, symbols=(quote,))
            rate_value = direct["rates"].get(quote)
            if isinstance(rate_value, Decimal) and rate_value > 0:
                return self._quote(
                    base=base,
                    quote=quote,
                    rate=rate_value,
                    provider_timestamp=direct["provider_timestamp"],
                    fetched_at=fetched_at,
                    derivation="direct",
                )
            # Provider returned the inverse base unexpectedly — try invert path below.
        except _ProviderPlanError:
            # Account cannot use arbitrary base — try USD book + explicit cross/invert.
            pass
        except FxProviderTimeoutError:
            raise
        except Exception as exc:
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                base_currency=base,
                quote_currency=quote,
                error=type(exc).__name__,
            )
            return None

        # Safe reverse: fetch quote as base, invert.
        try:
            inverse_payload = await self._fetch_latest(base=quote, symbols=(base,))
            inv = inverse_payload["rates"].get(base)
            if isinstance(inv, Decimal) and inv > 0:
                rate = (Decimal("1") / inv).quantize(Decimal("0.00000001"))
                return self._quote(
                    base=base,
                    quote=quote,
                    rate=rate,
                    provider_timestamp=inverse_payload["provider_timestamp"],
                    fetched_at=fetched_at,
                    derivation="inverted",
                )
        except _ProviderPlanError:
            pass
        except FxProviderTimeoutError:
            raise
        except Exception as exc:
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                base_currency=base,
                quote_currency=quote,
                phase="invert",
                error=type(exc).__name__,
            )

        # Explicit USD triangulation when the plan is USD-base-only.
        if base != "USD" and quote != "USD":
            try:
                usd_book = await self._fetch_latest(base="USD", symbols=(base, quote))
                base_per_usd = usd_book["rates"].get(base)
                quote_per_usd = usd_book["rates"].get(quote)
                if (
                    isinstance(base_per_usd, Decimal)
                    and isinstance(quote_per_usd, Decimal)
                    and base_per_usd > 0
                    and quote_per_usd > 0
                ):
                    rate = (quote_per_usd / base_per_usd).quantize(Decimal("0.00000001"))
                    logger.info(
                        "fx_provider_success",
                        provider=self.provider_name,
                        base_currency=base,
                        quote_currency=quote,
                        derivation="via_usd",
                    )
                    return self._quote(
                        base=base,
                        quote=quote,
                        rate=rate,
                        provider_timestamp=usd_book["provider_timestamp"],
                        fetched_at=fetched_at,
                        derivation="via_usd",
                    )
            except FxProviderTimeoutError:
                raise
            except Exception as exc:
                logger.warning(
                    "fx_provider_failure",
                    provider=self.provider_name,
                    base_currency=base,
                    quote_currency=quote,
                    phase="via_usd",
                    error=type(exc).__name__,
                )

        if base == "USD" or quote == "USD":
            try:
                usd_book = await self._fetch_latest(
                    base="USD",
                    symbols=(quote if base == "USD" else base,),
                )
                if base == "USD":
                    rate_value = usd_book["rates"].get(quote)
                    if isinstance(rate_value, Decimal) and rate_value > 0:
                        return self._quote(
                            base=base,
                            quote=quote,
                            rate=rate_value,
                            provider_timestamp=usd_book["provider_timestamp"],
                            fetched_at=fetched_at,
                            derivation="direct",
                        )
                else:
                    inv = usd_book["rates"].get(base)
                    if isinstance(inv, Decimal) and inv > 0:
                        rate = (Decimal("1") / inv).quantize(Decimal("0.00000001"))
                        return self._quote(
                            base=base,
                            quote=quote,
                            rate=rate,
                            provider_timestamp=usd_book["provider_timestamp"],
                            fetched_at=fetched_at,
                            derivation="inverted",
                        )
            except FxProviderTimeoutError:
                raise
            except Exception as exc:
                logger.warning(
                    "fx_provider_failure",
                    provider=self.provider_name,
                    base_currency=base,
                    quote_currency=quote,
                    phase="usd_leg",
                    error=type(exc).__name__,
                )

        logger.warning(
            "fx_provider_failure",
            provider=self.provider_name,
            base_currency=base,
            quote_currency=quote,
            reason="pair_unavailable",
        )
        return None

    def _quote(
        self,
        *,
        base: str,
        quote: str,
        rate: Decimal,
        provider_timestamp: datetime,
        fetched_at: datetime,
        derivation: str,
    ) -> FxRateQuote:
        if not isinstance(rate, Decimal):
            raise FxRateInvalidError(details={"type": type(rate).__name__})
        logger.info(
            "fx_provider_success",
            provider=self.provider_name,
            base_currency=base,
            quote_currency=quote,
            derivation=derivation,
            provider_timestamp=provider_timestamp.isoformat(),
        )
        return FxRateQuote(
            base_currency=base,
            quote_currency=quote,
            rate=rate,
            provider_name=self.provider_name,
            fetched_at=fetched_at,
            provider_timestamp=provider_timestamp,
            expires_at=None,
            status=FxRateStatus.CURRENT,
            derivation=derivation,
        )

    async def _fetch_latest(
        self,
        *,
        base: str,
        symbols: tuple[str, ...],
    ) -> dict[str, Any]:
        params = {
            "app_id": self._api_key,
            "base": base,
            "symbols": ",".join(symbols),
        }
        url = f"{self._base_url}/latest.json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            logger.warning(
                "fx_provider_timeout",
                provider=self.provider_name,
                base_currency=base,
            )
            raise FxProviderTimeoutError(
                details={"provider": self.provider_name, "base": base},
            ) from exc

        # Never log app_id / full URL with secrets.
        if response.status_code in {401, 403}:
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                status=response.status_code,
                reason="auth",
            )
            raise _ProviderPlanError("auth")
        if response.status_code == 400:
            # Common when the plan forbids non-USD base.
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                status=400,
                reason="bad_request_or_plan",
                base_currency=base,
            )
            raise _ProviderPlanError("plan_or_base")
        if response.status_code >= 400:
            logger.warning(
                "fx_provider_failure",
                provider=self.provider_name,
                status=response.status_code,
            )
            raise RuntimeError(f"OER HTTP {response.status_code}")

        payload = json.loads(response.text, parse_float=Decimal)
        if not isinstance(payload, dict):
            raise FxRateInvalidError(details={"reason": "non_object_payload"})
        rates_raw = payload.get("rates")
        if not isinstance(rates_raw, dict):
            raise FxRateInvalidError(details={"reason": "missing_rates"})
        rates: dict[str, Decimal] = {}
        for code, value in rates_raw.items():
            if isinstance(value, Decimal):
                rates[str(code).upper()] = value
            elif isinstance(value, int) and not isinstance(value, bool):
                rates[str(code).upper()] = Decimal(value)
            else:
                # Reject float / string paths that would hide a float intermediate.
                raise FxRateInvalidError(
                    details={
                        "reason": "non_decimal_rate",
                        "currency": str(code),
                        "type": type(value).__name__,
                    }
                )
        ts_raw = payload.get("timestamp")
        if isinstance(ts_raw, Decimal):
            provider_timestamp = datetime.fromtimestamp(int(ts_raw), tz=UTC)
        elif isinstance(ts_raw, int) and not isinstance(ts_raw, bool):
            provider_timestamp = datetime.fromtimestamp(ts_raw, tz=UTC)
        else:
            provider_timestamp = datetime.now(UTC)
        return {"rates": rates, "provider_timestamp": provider_timestamp, "base": base}


class _ProviderPlanError(Exception):
    """Account/plan cannot serve the requested base (not a network failure)."""
