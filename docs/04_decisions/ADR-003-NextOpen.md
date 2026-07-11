# ADR-003: Use Next Trading Day Open as Entry Price

**Status:** Accepted  
**Date:** 2026-07-11  
**Related documents:**

- `docs/02_mvp/MVP_1_Specification.md`

---

## Context

Many backtests use signal-day Close as the entry price. That is often unrealistic because
the signal may be generated after the market close or after all features are calculated.

MVP 1 needs a realistic and conservative entry assumption.

---

## Decision

Use next trading day Open as the entry price:

```text
entry_date = next trading day after signal date
entry_price = Open[ticker, entry_date]
target_price = entry_price * 1.20
```

---

## Options Considered

1. Signal-day Close.
2. Next trading day Open.
3. Next trading day VWAP.
4. Intraday simulated execution.

Option 2 was selected.

---

## Rationale

Next Open is simple, realistic, and avoids using an entry price the user likely could not
obtain after seeing the signal.

It also makes label generation deterministic and auditable.

---

## Consequences

The audit must report gap-related diagnostics:

- missing next-day Open;
- large gap at entry;
- cases where next Open is already at least 20% above signal-day Close;
- corporate-action conflicts around entry.

The target is based on entry Open, not signal Close. A large overnight gap may make a
candidate difficult to trade, but it does not automatically count as an entry-based target
hit.

