# bookpolicy

Dependency-free scoring for the question "given what I already hold, is this
action worth taking?" over a book of options positions.

Most options tooling scores a candidate standalone: this spread has a 70%
probability of profit, that one collects more credit, take the better number. Once
you hold something that is the wrong comparison, because the same spread helps one
book and hurts another. bookpolicy scores an action against a book and a target,
and puts opening, closing, and doing nothing on one scale.

- No dependencies. Stdlib and `Decimal`. No pandas, no numpy, no broker SDK, no
  network or disk I/O, and no knowledge of the calling application.
- Every score carries a plain-language rationale alongside the number.
- `assert_pnl_blind` checks independence from historical entry cost, holding
  current liquidation values, exposures and actions fixed. Current value is a
  legitimate forward-looking input; this guard is not a tax optimizer.

```python
from decimal import Decimal as D
from bookpolicy import (Book, Holding, Greeks, Open, Close, Hold, Target,
                        DeltaGap, ThetaYield, TransactionCost, score)

book = Book(
    holdings=(Holding(id='pcs-aug', greeks=Greeks(delta=D('1700'), theta=D('9'),
                                                  gamma=D('-2.4')),
                      margin=D('500'), days_to_expiry=9),),
    net_liq=D('30000'),
)
target = Target.like_holding_the_underlying(
    D('30000'), invested_ratio=D('1.0'), band_ratio=D('0.4'))
terms = (DeltaGap(D('1')), ThetaYield(D('100')), TransactionCost(D('0.01')))

candidate = Open('put_credit_spread',
                 greeks=Greeks(delta=D('19200'), theta=D('7')),
                 margin=D('500'), cost=D('2.24'))

total, parts = score(book, candidate, terms, target)
for p in parts:
    if p.value:
        print(f'{p.term:<18}{p.value:+8.3f}  {p.rationale}')
# delta_gap           +1.600  closes the delta gap by $19,200 ($28,300 → $9,100 from a $12,000 band)
# theta_yield         +1.400  collects $7.00/day against $500 of capital (1.400%/day)
# transaction_cost    -0.022  costs $2.24 in fees and crossing
```

Every term reports on every call. These example weights are not a calibrated
portfolio utility or an execution recommendation.

`examples/score_a_book.py` runs the full open/close/hold comparison with no data
source.

## Model

| Piece | What it is |
|---|---|
| `Greeks` | dollar-denominated delta / theta / gamma / vega; adds and negates |
| `Holding` | one position: greeks, margin, days to expiry, entry credit, cost to close |
| `Book` | everything held, plus the capital it is held against |
| `Target` | the book shape being steered toward, plus a no-trade band |
| `Open` / `Close` / `Hold` | the three actions, scored on one scale |
| `Contribution` | one term's signed value and its rationale |
| `TaxTreatment` | whether realizing a gain is a choice (see below) |
| `score(book, action, terms, target)` | `(total, contributions)`, never just the total |
| `assert_pnl_blind(...)` | raises `PnlLeakError` if a term changes when only entry cost changes |

Bundled terms: `DeltaGap` (distance to target, flat inside the band),
`ThetaYield` (local theta per dollar of capital), `GammaTheta` (legacy, uncalibrated
short-gamma / linear-theta diagnostic), `TransactionCost`. A term is anything with a `name` and
a `contribute(book, action, target)`, so callers can add their own.

## Prior work

These papers motivate the architecture; the terms do not solve their control
problems or inherit their optimality results:

- No-trade bands (Constantinides 1986; Davis & Norman 1990). With transaction
  costs the optimal policy is not to hold the target exactly but to do nothing
  inside a band around it. `DeltaGap` is flat at zero inside `Target.delta_band`,
  which is what allows frequent evaluation without churning on noise.
- Partial adjustment toward an aim (Gârleanu & Pedersen 2013). Trade toward a
  target, paying costs against the improvement. `Target` is the aim;
  `TransactionCost` is what makes partial adjustment fall out rather than be
  imposed.
- Inventory skewing (Avellaneda & Stoikov 2008). A market maker's quotes lean on
  inventory, never on whether the position is up or down. `assert_pnl_blind`
  enforces the same separation.
- The disposition effect (Shefrin & Statman 1985) is the failure mode being
  guarded against: riding losers and cutting winners, which a P&L-reading term
  reproduces while still looking like a risk model.

`GammaTheta` does not derive a 21-DTE rule. Theta multiplied by time is not an
expected remaining return, and its ratio with gamma is unit-dependent. The
legacy diagnostic returns zero for nonpositive theta/tenor or nonnegative gamma;
those cases imply no economic close benefit. Complete scenario comparisons,
current value, transaction costs and portfolio constraints belong in an
application's validated decision model.

## Two design choices

No financial defaults. `Target.like_holding_the_underlying` requires both ratios
as keyword arguments and defaults neither. A default agreeing with a threshold the
caller keeps elsewhere would put the same number in two places.

Tax treatment is configuration. "Unrealized P&L is not a decision input" has one
real exception: under realization accounting, cost basis becomes relevant near the
year boundary. Under mark-to-market it does not, since the result lands in the
year's income whether or not the position was closed. `TaxTreatment` carries the
distinction so it stays a property of the holder rather than an assumption inside
a term.

## Scope

Scoring only. No broker, no data fetching, no order placement, no position sizing,
no backtester, and no view on whether the top-ranked action is one you should take.

## License

MIT.
