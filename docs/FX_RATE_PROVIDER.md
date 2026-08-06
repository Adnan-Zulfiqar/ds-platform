# FX Rate Provider

## Interface

`FXRateProvider.get_rate(base, quote, at_or_before=None) -> FxRateQuote | None`

Returning `None` means unavailable. Implementations must **never** invent a 1:1
rate when `base ≠ quote`.

## Implementations

| Provider | Env `FX_PROVIDER` | Use |
|---|---|---|
| `UnavailableFXRateProvider` | `unavailable` (default) | Production-safe default — blocks cross-currency pricing |
| `StubFXRateProvider` | `stub` | Automated tests only |

Production feeds (Frankfurter, Open Exchange Rates, etc.) plug into the same
interface; credentials use `FX_API_KEY` and are never committed.

## Freshness

- `FX_RATE_MAX_AGE_MINUTES` (default 60) marks quotes `stale`
- Stale rates warn and block apply/publish until refreshed
- Unavailable rates block pricing preview calculations and publication

## Configuration

```bash
FX_PROVIDER=unavailable
# FX_API_KEY=
FX_RATE_MAX_AGE_MINUTES=60
FX_CACHE_TTL_SECONDS=300
```

See `.env.example`.
