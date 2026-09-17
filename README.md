# Design-Constrained Tail Pricing in Prediction Markets

**Measurement pipeline and first results.** August 2026. Paper in progress.

Prediction-market binary contracts quote probabilities on a coarse price grid
(1¢ or 0.1¢ ticks), in heterogeneous contract forms (touch / European / range),
against venue-specific listed statistics (the same CPI release trades as YoY
on Polymarket US and as MoM on Kalshi). This repository measures how those
design choices shape tail prices, and builds toward a pre-registered decision
rule for when a prediction-market contract prices a tail more cheaply than the
listed-option replication an institution could actually execute. A recurring
finding is that the effects concentrate in illiquid tails: where contracts
trade, prices behave; where they do not, the grid sets the quote.

Everything here is Python standard library only. Results 1–7 replicate with
no accounts or keys at all; the depth-history jobs behind results 8–9 need one
free API key (Predexon — its historical order-book endpoints are free and
unmetered).

## First results

Each result is registered in `verify/claims.json` with its source artifact, its
recompute script and its current reproducibility status. `bash scripts/check.sh`
recomputes what this repository can reach and reports what it cannot;
`CHECKS.md` documents the loop.

### 1. Ladders identify the underlying; single strikes do not
Individual strike contracts correlate weakly with the underlying
(gold, best single strike: ρ² = 0.041). But each contract's price is the
probability that the underlying exceeds its strike, so reading the ladder for
the strike where that probability crosses one half gives the market's own
median estimate. Interpolating for that crossing recovers the underlying with
ρ² = 0.965 at a 12-hour horizon and a level gap of $7.4 (0.17%) versus spot.
Applied to Polymarket US CPI (Aug 2026, 12 strikes, all quoted at 1-tick
spread): implied CPI YoY = 3.311%, intraday noise σ = 1.4bp, 98.7% extraction
success across 47 hours at 2.1-minute cadence, with a −3.3bp drift that
exceeds within-window noise ×2.4 (information flow, not noise).

### 2. The quoting model is recoverable, and the ATM and tail premia differ in kind
Fitting Φ⁻¹(p) = a + b·ln(S/K)/√T per contract recovers an implied volatility
σ = 1/b. For Kalshi gold across the maturity structure: dailies ATM σ = 0.301
(R² = 0.954) and OTM σ = 0.758; weeklies 0.283 (R² = 0.945) and 0.756; hourlies
0.383 (R² = 0.800) and 0.471. Realized volatility over the same window was
0.265. ATM implied runs 7–13% above realized, an ordinary variance risk
premium. OTM runs **2.86× realized** — a different order of magnitude, not an
extension of the ATM premium. That gap is the object this project studies.
A tested-and-rejected hypothesis is retained in `results/`: switching the
underlying proxy from Binance to Coinbase (BRTI proxy) left R² essentially
unchanged (0.874 → 0.865), so the unexplained variance is not index mismatch.

### 3. The favorite–longshot bias is a liquidity phenomenon, not a pricing one
Kalshi's 1-minute candles carry the quote (bid/ask close) and the last trade
price in the same bar, so both legs can be measured on the same contract at
the same minute. Two runs of that paired test, differing only in how stale a
trade may be before the snapshot is dropped, give opposite pictures
(`analysis/job8b_paired.py`; `results/job8b_paired.json`,
`results/job8b_paired_stale5.json`).

| Low band (<0.15) | trades ≤120 min old | trades ≤5 min old |
|---|---|---|
| bias at quote mid | −2.40pp | **−0.21pp** |
| bias at trade price | −2.05pp | **−0.23pp** |
| Δ (quote − trade) | −0.36 [−0.86, +0.08] n.s. | +0.03 [−0.20, +0.26] n.s. |
| contracts | 1,135 | 422 |

Restricting to contracts that actually traded within five minutes of the
snapshot (median staleness 0 min, p90 3 min) collapses the low-band bias to
roughly zero — and it collapses on **both** legs. The quote-based estimate
falls just as far as the trade-based one, so this is not an execution effect;
it is a sample effect. The −2.05pp figure describes contracts whose "trade
price" was up to two hours old, which is to say a stale quote wearing a
trade's clothing.

The high band moves the same way: Δ = +1.34pp [+0.29, +2.46] at 120 minutes
becomes +0.65pp [−0.24, +1.96] at five, losing significance.

The reading that survives is that longshots are fairly priced in contracts
that trade, and the bias lives in contracts that barely trade. That places it
alongside results 4 and 9 rather than apart from them: floor pinning, the
66–78% of Kalshi depth parked at the 1–2¢ floor, and this bias are three
views of the same object — an illiquid tail where the grid, not demand, sets
the quote.

Two earlier framings of this section are retracted and kept on the record.
Unpaired estimates (−1.66pp quote vs −0.26pp trade) suggested the bias halves
at execution; that gap was contract composition, since the two samples shared
no contracts (1,191 vs 10,949 in the low band). The paired 120-minute run then
suggested the tail premium was executable at −2.05pp; the staleness guard
shows that figure is a property of illiquid contracts, not of trade prices.
The decomposition tooling built for those runs still stands and is reusable:
Δ splits into a location term E[trade − mid | mid ∈ band], where the outcome
cancels algebraically, and a selection term from conditioning on a noisy
price. In the 120-minute high band that split attributed the entire gap to
selection (location −0.06pp, selection +1.40pp) — a caveat for any
favorite–longshot study that mixes quote- and trade-based estimates.

Whether tick size *causes* the residual bias remains open; a matched-period,
matched-statistic cross-venue comparison is queued.

### 4. Tick pinning is quantitatively dominant exactly where hedges live
Share of out-of-the-money minute-bars (ask ≤ 0.15) quoted exactly at the
minimum tick: gold daily 7.9%, **BTC daily 57.6%**. Below one tick of fair
value, quoted markup is a property of the grid, not of demand.

### 5. Both the observable floor and the decay speed are set by contract design
The resolution floor h* = ((Δ/S)/σ_1h)² — the horizon below which a one-grid-step
move is larger than typical price motion — makes shorter-horizon hedge
effectiveness unmeasurable; screening below it ranks noise above true hedges.
Attenuation is well described by ρ²(h) = A/(1+c/h), fit by constrained grid
search (the 1/ρ² linearization is unstable at small ρ², documented in
`prereg/`).

The decay constant c varies sharply with maturity structure. Kalshi gold
dailies give c = 0.208h (R² = 0.859, 38 events): correlation halves about 12
minutes after a shock. Weeklies give c = 1.421h (R² = 0.940, 10 events), seven
times slower. An institution whose rebalancing period is shorter than c cannot
obtain hedge effectiveness from that contract, so c is a selection constraint,
not a descriptive statistic. BTC and ETH dailies could not be estimated: an
eviction defect in an early collector version left no sample for that window.
Re-estimation is queued as current collection accumulates.

### 6. Settlement fidelity and venue fragmentation
Kalshi gold settlement vs PAXG over 40 events: median |diff| = $3.54.
Cross-venue: the same macro event is listed as different statistics
(CPI YoY vs MoM) and in different contract forms (touch vs European vs range);
comparing them naively manufactures spurious "mispricing". CME event-contract
time & sales (a third, institutionally-cleared venue) is ingested daily for the
same underlyings (CPI, unemployment, GDP, BTC hourly). Liquidity across venues
is severely asymmetric, however. Polymarket US macro books rest $155k (Fed),
$146k (CPI) and $108k (payrolls), while CME non-sports event contracts printed
roughly 230 trades and 6.4k contracts over five days, with no Fed product
listed at all. Payoff-matched comparison against CME therefore does not have the
sample to stand on; the Kalshi–Polymarket US comparison does, and is in
result 10.

### 7. The traditional benchmark an institution would actually use
Digital puts replicated from 5%-wide listed ETF put spreads (mid), 115-day
expiry, per $100M fully-protected notional: gold (GLD) 30% drawdown = 89×
payout, 3.6%/yr premium bleed; BTC (IBIT) 30% = 16×, 19.8%/yr. These are the
baselines against which prediction-market tail quotes are judged.

### 8. Retrospective event studies: eleven settled macro events (full depth)
Using free vendor order-book history (Predexon, ~5-second median cadence,
verified by recovering pre-settlement books of settled contracts), eleven
settled 2026 events were reconstructed end-to-end — CPI MoM and YoY,
unemployment, Fed decisions, payrolls, GDP — each as (implied-value
trajectory, resting-depth trajectory, settlement calibration). Two design
facts emerged. First, Kalshi halts trading ~5 minutes before the release:
there is no "post-release collapse" to study — the measurable objects are
pre-close convergence and settlement calibration. Second, calibration is
sharply event-heterogeneous: July CPI was nailed (Brier 0.021 MoM / 0.057
YoY) while June CPI YoY scored **0.436 — worse than an uninformed 0.5 bet**,
a genuine surprise print. Tail insurance pays exactly in the second kind of
month, and both kinds now sit in the same dataset (`results/event_studies.json`,
`analysis/job6_events.py`). Kalshi also lists CPI **both** as YoY (KXCPIYOY,
21 strikes) and MoM (KXCPI, 10 strikes), giving a statistic-matched
cross-venue twin for Polymarket US's YoY ladder.

### 9. Floor pinning is a size phenomenon — but only where events are scheduled
Result 4 measured pinning by *frequency* of quotes at the minimum tick. With
full-depth history the *size* dimension separates by market design
(`results/floor_size.json`, `analysis/job7_floor_size.py`): in scheduled-event
ladders, resting size piles onto the 1–2¢ floor — median floor-level size runs
**121× (CPI YoY), 635× (Fed), 829× (Fed decision)** the median size elsewhere
in the same books, with up to ~17% of all resting contracts parked at the
floor — while continuous-underlying dailies show little or none (gold 0.8×,
BTC 1.7×). Frequency-pinning and size-pinning are distinct dimensions:
BTC dailies pin 57.6% of OTM quotes at the floor but park little size there;
Fed ladders pin enormous size at the floor. The floor is where the
"sell-the-impossible" premium harvest lives, and it lives in event markets.

### 10. Live matched-statistic comparison: two venues, identical contracts, ≤1¢ apart for twelve hours
The Sep 4 unemployment and Sep 11 CPI releases were recorded live on both
Kalshi and Polymarket US at ~2-minute cadence with zero gaps in either event
window (`collect/events.json` pre-registered the windows). On every strike the
two venues list in common (7 for U3, 6 for CPI YoY), the 12-hour median mid
differed by 1¢ (U3) and 0.5¢ (CPI), maximum 2.5¢, and the bid–ask ranges
overlapped at all 13 strikes — no cross-venue arbitrage existed at any point.
Both venues also *erred together*: implied CPI YoY 3.33 on both against a
3.4 print, implied U3 4.08 on both against 4.1. Price discipline across the
two liquid venues is tight; CME (result 6) is the outlier because it has no
book, not because arbitrage fails.

Design still shows in the microstructure around the shared price. At-the-money
spreads ran 2.5–4¢ on Kalshi versus 1–2¢ on Polymarket US (1.2–4×). Headline
resting depth favored Kalshi 5× on CPI, but 66–78% of Kalshi's depth sat at
the 1–2¢ floor on strikes that could not resolve YES (result 9 live); at the
money the venues were comparable and event-dependent (U3: PMUS $75k vs
Kalshi $28k; CPI: Kalshi $41k vs PMUS $18k per snapshot). Brier at close was
lower on Polymarket US for both events (U3 0.037 vs 0.062; CPI YoY 0.015 vs
0.030), on differing strike sets and n = 2 — an observation, not a result.
Far-month Kalshi contracts listed alongside (26SEP) quoted 30–47¢ spreads
against 1–4¢ for the near month: liquidity has a term structure. A method
pitfall surfaced here and is documented in-code: keying a ladder by strike
alone silently merges concurrently listed months.

### 11. Post-release mechanics differ by design, and halt timing does not bind
Kalshi halts by rule 1–5 minutes before the print; Polymarket US does not
halt. Yet within two minutes of the print the Polymarket US book is one-sided
on every strike: bids vanish on strikes that will resolve NO, asks vanish on
strikes that will resolve YES. Resting notional barely moves ($130k → $142k
on U3) but none of it is executable. Stale wrong-side quotes linger — an
0.83 ask on U3 `>4.1` (worth 0) for 20 minutes, a 0.17 bid on CPI `>3.4`
(worth 0) for 14 minutes. The "post-release collapse" is a collapse of
two-sidedness, not of price; the ladder-implied value simply freezes.

The 12:25 (KXCPI) vs 12:29 (KXCPIYOY) close-time difference is a natural
experiment on halt timing and returns a clean null: market makers widened
boundary-strike spreads 5–14× (0.01 → 0.09 MoM, 0.01 → 0.14 YoY) and cut
depth by half starting ~15 minutes before *either* close. The extra four
minutes of YoY trading produced a further 20% depth withdrawal and no
repricing. The self-imposed halt precedes the rule.

## Repository layout

```
collect/    Always-on collector (order books, BBO, universe deltas) for
            Polymarket US / Kalshi / Limitless. 2.1-min cadence on 422
            economic markets; low-frequency BBO tier for 2,500
            politics/culture markets (no history API exists — miss it and
            it is gone); Kalshi restricted to near-dated contracts
            (its candlestick API backfills 1-min history; depth does not).
analysis/   Jobs 1–5: FLB at scale, smile/reverse-engineering, attenuation,
            Polymarket FLB, trade backfill. Jobs 6–7: retrospective event
            studies and floor-size concentration (need a free Predexon key).
            Resumable JSONL, disk cache.
pairscan/   Instrument-matching scanner: which prediction markets have a
            tradable traditional twin (CBOE chains) and/or a CME event-contract
            twin. Includes the browser-driven CME time & sales fetcher.
prereg/     Pre-registrations with content hashes recorded at confirmation
            time. PREREG 1 failed procedurally; the failure and exactly what
            changed are documented rather than silently revised.
results/    Derived aggregates only (fits, FLB tables, macro-ladder quotes).
            Raw streams are regenerable with collect/.
```

## Replication

```bash
# one-shot sanity run of the collector (writes to ./raw)
python3 collect/collect.py --once

# research jobs (Kalshi candlesticks + public gateways; ~hours, resumable)
cd analysis && bash run_all.sh

# instrument-matching scoreboard
python3 pairscan/pairscan.py
```

Notes: Polymarket (decentralized) endpoints are geo-blocked in some
jurisdictions (HTTP 451); Polymarket US gateway and Kalshi are not.
CME blocks non-browser clients (HTTP 403) — `pairscan/cme_fetch.sh` drives a
real browser for the daily CSV. Code comments are bilingual (Korean/English);
all documentation needed for replication is in English.

## Method discipline

- Pre-registration before scale-up, hashes stamped at confirmation
  (`prereg/`), failed registrations reported as failures.
- Contract-level cluster bootstrap (snapshots within a contract are
  correlated); empirical nulls via block-shuffled exposure series.
- Contract-form taxonomy enforced before any cross-venue comparison
  (touch vs European vs range; YoY vs MoM).
- Measured pitfalls are documented in-code: asks must be recorded even when
  the bid is absent (that *is* the tick-floor case); multi-choice events carry
  an `endDate` months after the economic condition date (parse the slug);
  resting notional ≠ tradability (one-sided books).

## Status

Collector running continuously; event studies target the Sep 4 (unemployment)
and Sep 11 (CPI) releases, where implied ladders meet realized outcomes.
Paper draft in progress under the working title above.

## License

MIT for all code. Derived data in `results/` is provided for replication and
review.
