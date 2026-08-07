# FX Rate Provider

## Interface

`FXRateProvider.get_rate(base, quote, at_or_before=None) -> FxRateQuote | None`

Returning `None` means unavailable. Implementations must **never** invent a 1:1
rate when `base ≠ quote`.

Application code must call **`FxService` only** — never a provider directly
from pricing/domain.

## Implementations

| Provider | Env `FX_PROVIDER` | Use |
|---|---|---|
| `UnavailableFXRateProvider` | `unavailable` (default) | Production-safe default — blocks cross-currency pricing |
| `StubFXRateProvider` | `stub` | Automated tests only |
| `OpenExchangeRatesProvider` | `openexchangerates` | First production provider |

## Quote fields

| Field | Meaning |
|---|---|
| `provider_timestamp` | FX market/source time from the provider |
| `fetched_at` | When *our* server retrieved the quote |
| `rate` | `Decimal` — parsed without a binary-float intermediate |
| `status` | `current` \| `stale` \| `unavailable` |
| `derivation` | `direct` \| `inverted` \| `via_usd` (explicit USD cross only) |

## Freshness vs cache retention

These are **separate**:

| Setting | Role |
|---|---|
| `FX_CACHE_TTL_SECONDS` (default 3600) | Freshness window — quote is `current` |
| `FX_MAX_STALENESS_SECONDS` (default 21600) | Redis retention **and** outer bound for controlled stale fallback |

Age is measured from `provider_timestamp` (preferred) so re-fetching an old
source feed cannot make a rate look new.

Policy:

1. Age ≤ freshness → `current`
2. Freshness < age ≤ max staleness → attempt provider refresh; on failure,
   controlled stale may be returned (`status=stale`, `fx_is_stale=true`)
3. Age > max staleness → reject (`fx_rate_stale` / unavailable)

## Configuration

```bash
FX_PROVIDER=openexchangerates
FX_API_KEY=
FX_BASE_URL=https://openexchangerates.org/api
FX_TIMEOUT_SECONDS=10
FX_CACHE_TTL_SECONDS=3600
FX_MAX_STALENESS_SECONDS=21600
```

JSON responses are parsed with `json.loads(..., parse_float=Decimal)`.
Never `float → Decimal`.

## Live verification

```bash
cd backend && python scripts/verify_fx.py
```

Skips cleanly when `FX_PROVIDER` / `FX_API_KEY` are unset. Never prints the key.

See also: [SHOPIFY_API_MODERNISATION.md](SHOPIFY_API_MODERNISATION.md),
[MONEY_AND_CURRENCY_ARCHITECTURE.md](MONEY_AND_CURRENCY_ARCHITECTURE.md).
