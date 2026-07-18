# ADR-007: Next-Trading-Day, Ceiling-Bounded Entry Simulation

**Status:** Accepted for MVP 2
**Date:** 2026-07-18
**Related documents:**

- `docs/02_mvp/MVP_2_Recommendation_Sandbox_Specification.md`
- `docs/04_decisions/ADR-003-NextOpen.md` (the frozen SWING_20 label's own next-day-Open
  entry convention -- this ADR extends the same never-buy-at-the-signal-close
  principle into a *realistic, sometimes-unfilled* execution simulation, rather than
  the label's always-filled-at-next-Open convention)

---

## Context

The frozen SWING_20 label (ADR-003) always enters at the next day's Open, unconditionally.
That is correct for a *label* -- it needs a single, always-computable point-in-time
return, and it is what Model 2 was fit and Locked-Test-evaluated against.

MVP 2 is not computing a label; it is simulating what an operator following the
sandbox's own recommendations could actually have done. Always filling at the next
Open, no matter how far that Open has already gapped above the signal-day close,
is unrealistic: a name that gaps 15% up overnight is a materially worse entry than
the model's signal-day evidence supported, and a mechanical process that still "buys"
it uncritically would overstate what is achievable.

MVP 2 therefore needs an entry *ceiling* and an execution rule that can legitimately
result in no fill at all -- while remaining fully deterministic and point-in-time
correct (no intraday data, no data from after the execution session).

---

## Decision

1. **Entry ceiling** (Section 8 of the MVP 2 spec):
   `max_entry_price = min(signal_close * 1.02, signal_close + 0.25 * ATR14)`, computed
   from `as-of DATE`'s own close and ATR14 only.

2. **Execution rule**, evaluated using only the execution session's own daily OHLC bar:

   ```
   If next_day_open <= max_entry_price:
       fill at next_day_open
   Else if next_day_low <= max_entry_price < next_day_open:
       fill at max_entry_price
   Else:
       no fill for that day
   ```

3. **Validity window**: at most 2 trading sessions after the signal date. The order
   expires (`EXPIRED_ENTRY`) if neither session fills it.

4. **No same-signal-day fill, ever.** The earliest possible execution session is the
   signal date's next trading session, mirroring ADR-003's principle that a
   recommendation cannot be acted on before it exists, but -- unlike ADR-003 -- not
   guaranteeing a fill.

These constants (2% close extension, 0.25x ATR extension, 2-session validity) are
explicitly **not** derived from or tuned against validation or Locked Test performance.
They are conservative, documented, configurable defaults for forward simulation.

---

## Rationale

- Using `min()` of a flat percentage cap and an ATR-scaled cap gives a ceiling that
  tightens automatically in low-volatility names (where a 2% gap is unusually large)
  and loosens automatically in high-volatility names (where 2% is unremarkable) --
  without introducing a second free parameter to tune per name.
- The three-way execution rule (`open` / `ceiling-if-touched` / `no fill`) is the
  simplest rule that (a) never assumes a fill at a worse price than the ceiling, (b)
  gives credit for the realistic case where price gaps above the ceiling at the open
  but pulls back intraday to a fillable level, and (c) never requires intraday
  timestamp data -- only the session's own O/H/L/C, consistent with the project's
  existing daily-bar-only data model.
- A hard 2-session validity window keeps `entry_orders` from lingering indefinitely as
  stale recommendations, and keeps the sandbox's notion of "pending" bounded and
  auditable.
- Explicitly *not* optimizing these constants against validation/Locked Test data
  keeps the entry-simulation layer from becoming a second, unaudited tuning surface
  that could quietly inflate the sandbox's apparent performance relative to what
  EXP-003 actually validated (which measured Model 2's *ranking*, not any particular
  entry-price policy).

---

## Consequences

- The sandbox will sometimes recommend a candidate that never gets filled
  (`EXPIRED_ENTRY`). This is intended behavior, not a bug -- it is exactly the
  information later risk research needs (how often does the ranking signal arrive too
  late to act on at a reasonable price).
- Because entries can fail to fill, the sandbox's realized position count will be
  lower than its candidate count. Any later analysis of "how good is Model 2" must use
  the shadow top-10 / ranking-level metrics already established in EXP-002/EXP-003,
  not the sandbox's fill rate, to avoid conflating ranking quality with this entry
  policy's conservatism.
- If a future phase revisits these constants (e.g. to study fill-rate sensitivity),
  that is new, separately pre-registered research -- not a retroactive edit to MVP 2's
  frozen defaults.
