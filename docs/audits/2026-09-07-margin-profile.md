# D35 — User-requested 10% TESTNET margin profile (2026-09-07)

This is a position-size experiment, **not a profitability improvement or a
mainnet approval**. Only `SCALPER_MAX_MARGIN_PCT` changes from `0.5` to `10`.
The request means initial margin, not notional exposure or a 10% stop-loss budget.

## Why a green 14.56% trade earned only 0.65 USDT

Read-only AWA ledger and runtime inspection at 06:24–06:30 UTC:

| Trade | Initial margin (USDT) | Net PnL (USDT) | Margin ROI |
|---|---:|---:|---:|
| SOL TV LONG #350 | 4.486300 | +0.652982 | +14.5550% |
| LTC C LONG #351 | 4.522031 | +0.757371 | +16.7485% |

`tracker.py:close_trade` calculates `realized_pnl / margin_usdt * 100`.
The screenshot is arithmetically consistent. AWA had an explicit 0.5% margin
ceiling, introduced during the prior risk rollback; this was not a rounding bug.
The 1000-USDT virtual ledger, starting at trade #278, stood at
**905.96078637 USDT**. Historical losses are preserved; the balance is not reset.

Last 24 hours at inspection: 8 closed trades, 7 winners, net +3.312907 USDT.
Since the 30 August 21:03 UTC rollback: 57 closed trades, net -7.126484,
PF 0.658382. A short green run does not establish positive expectancy.

## Exact change and sizing semantics

`SCALPER_MAX_MARGIN_PCT=10` applies to **new C and TV entries** through the same
executor. No risk multiplier, signal filter, stop, TP, trailing, leverage,
capacity, daily-loss setting, credentials, or virtual-ledger boundary is changed.
Local `.env` already had this key at 10; no other local settings were copied to AWA.

The existing formula is:

```text
equity = min(exchange available, virtual base + eligible CLOSED net PnL)
quantity = min(equity * risk_pct / 100 * signal.risk_multiplier / stop_distance,
               equity * leverage * max_margin_pct / 100 / entry_price)
quantity = exchange LOT_SIZE floor(quantity)
```

With the current risk percentage 10, fixed stop ROI 50, and C multiplier 0.5
(TV 1.0), both sources reach the 10% margin ceiling before rounding. This is a
ceiling/target, **not a promise of exactly 10% on every actual fill**: exchange
availability, lot size, price drift, and partial fills can reduce the amount.
Partial fills must not be topped up merely to force the target.

An independent no-network/no-order executor + harness check covered
LONG/SHORT × C/TV × both ceilings (8 cases). At equity 905.96078637,
entry 105.14, leverage 20, and quantity step 0.01:

| Ceiling | Quantity | Margin (USDT) | Equity allocation | Initial stop risk, before fees/slippage |
|---|---:|---:|---:|---:|
| 0.5% | 0.86 | 4.521020 | 0.499030% | 2.260510 |
| 10% | 17.23 | 90.578110 | 9.998017% | 45.289055 |

The harness's ideal quantities, floored to that step, matched the executor.
At unchanged ROI 14.555%, approximately 90.60 USDT margin would produce about
13.19 USDT net PnL; that is arithmetic, not a forecast or a replayed fill.

## Risk boundary — unchanged protections are not a 1% loss guarantee

A 10% margin position at the current 50% margin-stop setting risks about **5%
of equity**, plus fees/funding/slippage. Five concurrent full stops can amount
to roughly **25% of equity**; correlation can make losses simultaneous.
`SCALPER_DAILY_LOSS_LIMIT_PCT=1` is a realized-income latch that blocks new
entries after the threshold. It does not reserve open/pending stop risk and
cannot prevent an individual position from exceeding that daily percentage.
It remains enabled and was not loosened to keep trading after losses.

The user requested larger TESTNET allocations. This change must not be
described as a safer strategy, better edge, zero-loss configuration, or permission
to deploy to mainnet. Another AI must not silently increase risk again.

## E15 — Three known regimes, fixed 1000-USDT scale comparison

Current D34 code (`9d6f4b5`, source-equivalent to `4e7ea55`) and current AWA
nonsecret SCALPER settings. Six sequential cache-only runs; network misses
raise instead of downloading. All scalper singleton settings are rebound for
each variant, avoiding the D34 global-config trap. No parameter search.

| Window (2026, UTC) | Trades in each | PF in each | Net, 0.5% / 10% | Bar drawdown, 0.5% / 10% |
|---|---:|---:|---:|---:|
| Bear, Jan 23–Feb 13 | 149 | 2.028385 | 28.5162 / 570.3232 | 7.1425 / 142.8495 |
| Range, Jul 1–Jul 21 | 136 | 1.503685 | 13.8205 / 276.4099 | 10.5072 / 210.1441 |
| Bull, Aug 7–Aug 21 | 101 | 1.327512 | 6.3829 / 127.6583 | 7.7998 / 155.9952 |

Amounts are USDT. Trade count, ROI and PF are unchanged; nominal PnL and
drawdown scale 20×. The candidate's bar drawdowns are **14.28%, 21.01%, 15.60%**
of the fixed 1000 baseline. P2's scale-aware rule is used: larger PnL is not
evidence of a better strategy. The prior D29 20%-DD warning is relevant risk
context; these results do not establish acceptable real-money risk.

Important limits: these are repeatedly used selection windows, **not fresh
OOS**; C only (no actual TradingView alerts); each trade uses fixed 1000 equity,
not compounded portfolio equity. Daily-loss shutdown is not simulated; capacity
is post-hoc. Exchange quantity rounding, order-book impact, actual fill latency
and stop-fill slippage are not reproduced. No new fee-stress run this turn.
This is a scale-parity check, not a forecast of the live 10% account path.

Evidence on the operator Mac:

- `/Users/max/Documents/Codex/2026-08-08/aw/work/compare_margin_20260907.py`
- `/Users/max/Documents/Codex/2026-08-08/aw/work/compare_margin_20260907.json`
- `/Users/max/Documents/Codex/2026-08-08/aw/work/margin_executor_audit_20260907.md`

Reproduction after the live setting changes (retain the recorded pre-change
configuration and do not overwrite the evidence):

```bash
cd /Users/max/TRADINGBOT/v2
.venv/bin/python -B /Users/max/Documents/Codex/2026-08-08/aw/work/compare_margin_20260907.py \
  --config-snapshot /Users/max/Documents/Codex/2026-08-08/aw/work/compare_margin_20260907.json \
  --output /Users/max/Documents/Codex/2026-08-08/aw/work/compare_margin_20260907_reproduced.json
```

Full local suite before the setting change: `.venv/bin/python -m pytest tests -q`
→ **2852 passed, 2 skipped, 2 warnings, 62.51 seconds**. No motor source changed.

## Activation and rollback

Pre-change AWA source/HEAD: clean `9d6f4b5`; TESTNET healthy; shadow STOPPED;
no entry halt and no pending maker entries. Existing SOL LONG #352 had
quantity 0.86, entry 105.14, and three native reduce-only protection orders:
SL `1000000195822632` at 102.52, TP1 `1000000195822664` (0.34 at 105.67),
TP2 `1000000195822699` (0.17 at 106.46). No intentional resize or close.

At 06:31:47 UTC, `.env` was backed up and a byte-level comparison proved the
only change was `SCALPER_MAX_MARGIN_PCT=0.5` → `10`. Settings validation
confirmed TESTNET. Restart uses `RESTART_LABEL=d35-margin10
scripts/restart_safe.sh testnet`, including ban/halt/config/health checks.

- Pre-change backup: `/opt/tradingbot-v2/backups/env.bak-20260907T063147Z-d35-margin10-before`.
- Restart backup (already contains 10): `backups/env.bak-20260907-063147-d35-margin10`.
- Guarded restart completed at **06:32:48 UTC**, PID **809319**; strict health
  became ready after 45 seconds. At 06:34:07 UTC, `/health` was healthy/core
  healthy/TESTNET; `/config` reported active margin ceiling 10, stop ROI 50,
  daily loss 1 and capacity 5. Sizing equity was unchanged at 905.96078637;
  SOL remained quantity 0.86, with no pending entries or entry blocks. Scan
  advanced to 06:33:56 UTC; all reported protection-error counts were zero.
  No natural post-change entry has yet established actual larger exchange fills.

Rollback only the margin key to 0.5, preserve any later unrelated env changes,
validate TESTNET, then use `RESTART_LABEL=d35-margin-rollback
scripts/restart_safe.sh testnet`. Do not reset the database, cohort, protections,
or historical PnL. Do not start a second manager or use mainnet to verify sizing.
The next natural fill, not a synthetic order, is the exchange sizing proof.
