"""Scoring terms, and the sum over them.

A term is a pure function of ``(book, action, target)`` returning a
:class:`Contribution`. They compose by addition, so a book-aware term and any
other additive bias a caller already computes are the same kind of thing and can
live in one sum.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from bookpolicy.errors import PnlLeakError
from bookpolicy.types import Action, Book, Close, Contribution, Greeks, Hold, Open, Target

_ZERO = Decimal(0)


class Term(Protocol):
    name: str

    def contribute(self, book: Book, action: Action, target: Target) -> Contribution: ...


def after(book: Book, action: Action) -> Greeks:
    """The book's greeks once ``action`` has been taken."""
    if isinstance(action, Open):
        return book.greeks + action.greeks
    if isinstance(action, Close):
        return book.greeks + (-book.holding(action.holding_id).greeks)
    return book.greeks


def score(
    book: Book, action: Action, terms: tuple[Term, ...], target: Target | None = None,
) -> tuple[Decimal, list[Contribution]]:
    """Sum the terms. Returns ``(total, contributions)``, never just the total, so
    a caller cannot report a score without the reasoning that produced it."""
    target = target or Target()
    parts = [t.contribute(book, action, target) for t in terms]
    return sum((p.value for p in parts), _ZERO), parts


# ── terms ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeltaGap:
    """Reward an action for closing the book's delta gap; charge it for widening one.

    Flat at zero inside the band. That is the no-trade region (Constantinides 1986;
    Davis & Norman 1990): within it no move is worth its costs, which is what allows
    frequent evaluation without churning on noise. Outside it the term is linear in
    the distance closed, so an action that halves the gap scores half of one that
    closes it.

    Reads dollar delta, because share-equivalent deltas from different underlyings
    are not addable.
    """

    weight: Decimal = Decimal('1')
    name: str = 'delta_gap'

    def contribute(self, book: Book, action: Action, target: Target) -> Contribution:
        if isinstance(action, Hold):
            return Contribution(self.name, _ZERO, 'holding changes no exposure')
        before = abs(book.greeks.delta - target.delta)
        now = abs(after(book, action).delta - target.delta)
        if before <= target.delta_band and now <= target.delta_band:
            return Contribution(
                self.name, _ZERO,
                f'book delta ${book.greeks.delta:+,.0f} stays inside the '
                f'±${target.delta_band:,.0f} band; no pressure either way')
        if not target.delta_band:
            return Contribution(self.name, _ZERO, 'no delta band configured')
        closed = before - now
        value = self.weight * closed / target.delta_band
        verb = 'closes' if closed > 0 else 'widens'
        return Contribution(
            self.name, value,
            f'{verb} the delta gap by ${abs(closed):,.0f} '
            f'(${before:,.0f} → ${now:,.0f} from a ${target.delta_band:,.0f} band)')


@dataclass(frozen=True, slots=True)
class GammaTheta:
    """Legacy local-sensitivity diagnostic, not an economic exit criterion.

    Theta times remaining days is a linear extrapolation, not expected decay.
    The ratio has units and no calibrated link to an optimal management date.
    Positive gamma and nonpositive theta provide no short-gamma diagnostic.
    Use complete scenario valuations for economic comparisons.
    """

    weight: Decimal = Decimal('1')
    name: str = 'gamma_theta'

    def contribute(self, book: Book, action: Action, target: Target) -> Contribution:
        if not isinstance(action, Close):
            return Contribution(self.name, _ZERO, 'not a close')
        h = book.holding(action.holding_id)
        remaining = h.greeks.theta * h.days_to_expiry
        if h.greeks.theta <= 0 or h.days_to_expiry <= 0 or h.greeks.gamma >= 0:
            return Contribution(
                self.name, _ZERO, 'short-gamma/positive-theta diagnostic unavailable; '
                'no economic close benefit inferred')
        pressure = -h.greeks.gamma / remaining
        return Contribution(
            self.name, self.weight * pressure,
            f'gamma {h.greeks.gamma:+,.2f} divided by linear theta × '
            f'{h.days_to_expiry}d (${remaining:,.0f}); uncalibrated ratio {pressure:.3f}')


@dataclass(frozen=True, slots=True)
class TransactionCost:
    """What the action costs to take, straight through.

    Always negative. Without a real cost any non-zero improvement is worth taking
    and the no-trade band collapses to zero width.
    """

    weight: Decimal = Decimal('1')
    name: str = 'transaction_cost'

    def contribute(self, book: Book, action: Action, target: Target) -> Contribution:
        cost = getattr(action, 'cost', _ZERO)
        if not cost:
            return Contribution(self.name, _ZERO, 'no cost to take this action')
        return Contribution(self.name, -self.weight * cost,
                            f'costs ${cost:,.2f} in fees and crossing')


@dataclass(frozen=True, slots=True)
class ThetaYield:
    """Theta gained per dollar of capital the action ties up.

    For the mandate "maximize theta while carrying the exposure of someone who
    simply owns the underlying", `DeltaGap` is the constraint and this is the
    objective. They are paired because theta alone recommends a delta-neutral book
    and a delta target alone recommends one that collects nothing.

    Per dollar of capital rather than in absolute dollars, since capital is the
    binding constraint: $9/day against $500 beats $12/day against $1,000.

    Closing gives up theta and is charged for it, which is what stops the exit terms
    unwinding a healthy book.
    """

    weight: Decimal = Decimal('1')
    name: str = 'theta_yield'

    def contribute(self, book: Book, action: Action, target: Target) -> Contribution:
        if isinstance(action, Open):
            gained, capital = action.greeks.theta, action.margin
            if capital <= 0:
                return Contribution(self.name, _ZERO,
                                    'no margin figure, cannot judge theta per dollar')
            value = self.weight * gained / capital
            return Contribution(
                self.name, value,
                f'collects ${gained:,.2f}/day against ${capital:,.0f} of capital '
                f'({gained / capital * 100:.3f}%/day)')
        if isinstance(action, Close):
            h = book.holding(action.holding_id)
            if h.margin <= 0:
                return Contribution(self.name, _ZERO, 'no margin figure on the holding')
            value = -self.weight * h.greeks.theta / h.margin
            return Contribution(
                self.name, value,
                f'gives up ${h.greeks.theta:,.2f}/day of decay on ${h.margin:,.0f} '
                f'of freed capital')
        return Contribution(self.name, _ZERO, 'holding collects what it already collects')


def assert_pnl_blind(
    book: Book, action: Action, terms: tuple[Term, ...], target: Target,
) -> None:
    """Check sunk-cost independence, not independence from current market value.

    Only historical entry premium changes. Today's liquidation value, quantities,
    capital, sensitivities and action stay fixed. Current value may legitimately
    affect an action's remaining payoff. Call for every action being evaluated.
    This is not a tax optimizer; a tax-aware policy needs a different contract.
    """
    from dataclasses import replace

    worse = replace(book, holdings=tuple(
        replace(h, credit_received=h.credit_received * 10 + Decimal('1000'))
        for h in book.holdings))
    for term in terms:
        before = term.contribute(book, action, target)
        after_ = term.contribute(worse, action, target)
        if before.value != after_.value:
            raise PnlLeakError(
                f'{term.name} moved from {before.value} to {after_.value} when only '
                f'unrealized P&L changed, entry price cannot reach the decision')
