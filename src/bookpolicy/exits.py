"""Should a holding be closed? Asked as a portfolio decision, not a profit check.

The usual exit rules read the position alone: take profit at 50%, cut at 2x credit,
close at 21 days. They answer the wrong question. Whether a position is green or red
is sunk cost; what matters is what holding it still offers against what it can still
cost, and what closing it does to the rest of the book. A profitable hedge can be
worth keeping. A spread near maximum loss can be worth keeping too: little is left
to lose and much can still be won back.

``decide_exit`` asks, in order, and stops at the first answer:

1. **Is closing dominated?** If closing at the executable (natural) price costs at
   least the worst settlement outcome, holding cannot end worse than closing: hold.
2. **Does what is left to gain still cover one bad day?** Reward left (to
   expiration, from today's midpoint closing price) divided by what one adverse day
   would cost from here. Time enters through gamma: the same adverse move costs more
   as expiration nears, so a position at 20% profit can be worth holding at 30 days
   and worth closing at 14. Above the caller's threshold: hold. (Reward against
   *loss* left, by contrast, is blind to time.)
3. **Does closing move the book toward its goals?** Without the holding, the book's
   dollar delta must not sit farther from its target than before (beyond the
   target's no-trade band), and its worst stressed loss must not rise. If closing
   fails either, the holding is protecting the book: hold, and say which.

Only a holding that passes all three is an exit. Every check reports its measured
value and a plain-language rationale either way. The decision says *whether*; *how*
(close now, or move a resting target) is the caller's, since it depends on orders
this package never sees. No probabilities: these are conditional extremes and a
named adverse day, not expected values.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from bookpolicy.errors import BookPolicyError
from bookpolicy.types import Book, Target

_ZERO = Decimal(0)
EXIT_REASON = ('what is left to gain no longer covers one bad day, and closing moves the book '
               'toward its goals')


class ExitEvidenceError(BookPolicyError, ValueError):
    """Evidence that cannot describe a real holding (negative loss, inverted bounds)."""


@dataclass(frozen=True, slots=True)
class ExitEvidence:
    """What closing one holding now gives up and costs, all in dollars.

    ``reward_left``          most it can still gain by expiration, against closing now
                             at the midpoint
    ``loss_left``            most it can still lose by expiration, as a positive figure
    ``bad_day``              what one named adverse day would cost from here, positive;
                             zero when no adverse move loses value
    ``closing_dominated``    closing at the executable price costs at least the worst
                             settlement outcome
    ``worst_loss_with``      the book's worst loss across the caller's stress scenarios,
    ``worst_loss_without``   with the holding and without it (positive figures)
    """
    reward_left: Decimal
    loss_left: Decimal
    bad_day: Decimal
    closing_dominated: bool
    worst_loss_with: Decimal
    worst_loss_without: Decimal

    def __post_init__(self):
        for name in ('reward_left', 'loss_left', 'bad_day', 'worst_loss_with',
                     'worst_loss_without'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ExitEvidenceError(f'{name} must be a finite, non-negative Decimal')

    @classmethod
    def from_values(cls, *, settlement_best: Decimal, settlement_worst: Decimal,
                    close_mid: Decimal, close_natural: Decimal, bad_day: Decimal,
                    worst_loss_with: Decimal, worst_loss_without: Decimal) -> 'ExitEvidence':
        """From signed position values (long positive, short negative), as an exact
        payoff model and a quote give them: the value at the best and worst settlement,
        and the value of closing now at the midpoint and at the executable side.

        A short 5-wide put spread costing $286 to close at the midpoint and $297 at the
        natural, settling between -$500 and $0: reward left $286, loss left $214."""
        if settlement_worst > settlement_best or close_natural > close_mid:
            raise ExitEvidenceError('inverted settlement bounds or closing prices')
        return cls(reward_left=max(_ZERO, settlement_best - close_mid),
                   loss_left=max(_ZERO, close_mid - settlement_worst),
                   bad_day=bad_day, closing_dominated=settlement_worst >= close_natural,
                   worst_loss_with=worst_loss_with, worst_loss_without=worst_loss_without)


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    """The caller's threshold, with no default: propose an exit when reward left is at
    most this many bad days. ``Decimal('1')`` reads "what is left to gain no longer
    covers one bad day"."""
    reward_to_bad_day: Decimal

    def __post_init__(self):
        if not self.reward_to_bad_day.is_finite() or self.reward_to_bad_day <= 0:
            raise ExitEvidenceError('reward_to_bad_day must be a positive, finite Decimal')


class Verdict(StrEnum):
    HOLD = 'hold'
    EXIT = 'exit'


@dataclass(frozen=True, slots=True)
class Check:
    test: str
    passes: bool
    measured: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class ExitDecision:
    holding_id: str
    verdict: Verdict
    reasons: tuple[str, ...]
    checks: tuple[Check, ...]


def _hold(holding_id, reasons, checks) -> ExitDecision:
    return ExitDecision(holding_id, Verdict.HOLD, tuple(reasons), tuple(checks))


def decide_exit(book: Book, holding_id: str, evidence: ExitEvidence, target: Target,
                policy: ExitPolicy) -> ExitDecision:
    """Hold or exit one holding, with every check that was reached."""
    holding = book.holding(holding_id)
    if evidence.closing_dominated:
        return _hold(holding_id, ['closing at the natural costs at least the worst settlement outcome'],
                     [])
    if evidence.bad_day == 0:
        return _hold(holding_id, ['no adverse day loses value: no bad day to weigh'], [])
    ratio = evidence.reward_left / evidence.bad_day
    threshold = policy.reward_to_bad_day
    checks = [Check('reward left against one bad day', ratio <= threshold, ratio,
                    f'${evidence.reward_left:,.2f} left to gain against ${evidence.bad_day:,.2f} '
                    f'for one bad day: {ratio:.2f}, threshold {threshold}')]
    if ratio > threshold:
        return _hold(holding_id, [f'what is left to gain covers {ratio:.2f} bad days, above {threshold}'],
                     checks)
    with_it = book.greeks.delta
    without = with_it - holding.greeks.delta
    before, after = abs(with_it - target.delta), abs(without - target.delta)
    band = abs(target.delta_band)
    checks.append(Check('delta gap', after <= max(before, band), after,
                        f'gap to the ${target.delta:,.0f} target ${before:,.0f} → ${after:,.0f} '
                        f'(band ${band:,.0f})'))
    checks.append(Check('worst stressed loss',
                        evidence.worst_loss_without <= evidence.worst_loss_with,
                        evidence.worst_loss_without,
                        f'worst stressed loss ${evidence.worst_loss_with:,.0f} → '
                        f'${evidence.worst_loss_without:,.0f}'))
    failed = [c.test for c in checks[1:] if not c.passes]
    if failed:
        return _hold(holding_id, [f'closing moves the book away from its goals: {", ".join(failed)}'],
                     checks)
    return ExitDecision(holding_id, Verdict.EXIT,
                        (EXIT_REASON,), tuple(checks))
