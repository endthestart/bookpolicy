"""What a book, an action, and a scored term are.

Plain frozen dataclasses over ``Decimal``. Nothing here knows about a broker, a
database, or the application holding the book.

Greeks are dollar-denominated: one share-equivalent delta of SPY and one of QQQ
are different amounts of money, so only the dollar figures add across a mixed
book. `theta` is dollars per calendar day, positive when the book collects.
"""

from dataclasses import dataclass
from decimal import Decimal

from bookpolicy.errors import UnknownHoldingError

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Greeks:
    """Dollar-denominated book or position sensitivities."""

    delta: Decimal = _ZERO       # dollars of directional exposure
    theta: Decimal = _ZERO       # dollars per calendar day; positive = collecting
    gamma: Decimal = _ZERO       # rate of delta change
    vega: Decimal = _ZERO        # dollars per volatility point

    def __add__(self, other: 'Greeks') -> 'Greeks':
        return Greeks(self.delta + other.delta, self.theta + other.theta,
                      self.gamma + other.gamma, self.vega + other.vega)

    def __neg__(self) -> 'Greeks':
        return Greeks(-self.delta, -self.theta, -self.gamma, -self.vega)


@dataclass(frozen=True, slots=True)
class Holding:
    """One position as the policy sees it.

    ``credit_received`` is the entry premium, signed as the book stores it
    (positive = credit). ``cost_to_close`` is what closing costs now. Both exist so
    a term can reason about what is left to collect, not about whether the position
    is winning: closing because the book needs it is risk management, closing
    because the position is red is sunk cost, and no term may read the difference
    as a signal.
    """

    id: str
    greeks: Greeks
    margin: Decimal
    days_to_expiry: int
    credit_received: Decimal = _ZERO
    cost_to_close: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class Book:
    """Everything held, plus the capital it is held against."""

    holdings: tuple[Holding, ...] = ()
    net_liq: Decimal = _ZERO

    @property
    def greeks(self) -> Greeks:
        total = Greeks()
        for h in self.holdings:
            total = total + h.greeks
        return total

    @property
    def deployed_margin(self) -> Decimal:
        return sum((h.margin for h in self.holdings), _ZERO)

    def holding(self, holding_id: str) -> Holding:
        for h in self.holdings:
            if h.id == holding_id:
                return h
        raise UnknownHoldingError(f'no holding {holding_id!r} in this book')


@dataclass(frozen=True, slots=True)
class Target:
    """The book shape being steered toward: the "aim" in Gârleanu-Pedersen terms.

    ``delta_band`` is the no-trade region (Constantinides; Davis & Norman). Inside
    it nothing is worth paying costs to fix, which is what allows frequent
    evaluation without churning on noise.
    """

    delta: Decimal = _ZERO
    delta_band: Decimal = _ZERO

    @classmethod
    def like_holding_the_underlying(
        cls,
        capital: Decimal,
        *,
        invested_ratio: Decimal,
        band_ratio: Decimal,
    ) -> 'Target':
        """The target for "maximize theta while carrying the exposure of someone
        who simply owns the underlying".

        The target is not zero. Delta-neutral is the textbook answer and the wrong
        one for a small account: a genuinely hedged book takes enough capital to
        hold both sides, and aiming at zero gives up the exposure a directional
        premium seller wants. A different mandate is a different `Target`.

        ``capital`` is the base the ratios multiply. Whichever figure the caller
        passes is the one the target commits to: net liq in a dedicated account,
        but an explicit allocation wherever the book under management is a share of
        a larger account, since scaling the target to capital the strategy does not
        control asks it for exposure it was never given.

        ``invested_ratio = 1.0`` is the dollar delta of holding the underlying
        outright with that capital. Expressed as a multiple rather than a dollar
        figure, since a fixed number drifts from its intent as the account grows
        and the underlying moves.

        ``band_ratio`` is the no-trade half-width, on the same base.

        Neither ratio has a default. They are financial policy and belong in the
        caller's configuration; a default agreeing with a threshold the caller
        already keeps would put the same number in two places.

        If the caller has a threshold for "meaningfully long", the natural wiring is
        ``band_ratio = long_ratio - invested_ratio``, which lands the band's upper
        edge where that threshold already sits.
        """
        return cls(delta=invested_ratio * capital, delta_band=band_ratio * capital)


@dataclass(frozen=True, slots=True)
class TaxTreatment:
    """Whether realizing a gain or loss is a choice, which decides whether
    unrealized P&L may enter a hold/close decision at all.

    This is the one real exception to "P&L is not an input". It depends on the
    holder's tax situation, so it is configuration rather than an assumption inside
    a term.

    ``marks_to_market=True``: the result lands in the year's income whether or not
    the position was closed, so when it closes carries no tax consequence. The
    sunk-cost rule then holds unconditionally, with no year-end harvesting motive
    and no re-entry penalty.

    ``marks_to_market=False``: realizing is a choice with a tax consequence, so
    cost basis does become relevant near the year boundary and re-entering the same
    risk may be penalised. No term here reasons about that yet; one that did would
    have to read this flag, and `assert_pnl_blind` keeps its absence enforced.
    """

    marks_to_market: bool

    @property
    def unrealized_pnl_is_decision_relevant(self) -> bool:
        return not self.marks_to_market


# ── actions ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Open:
    """A candidate new position. ``greeks`` is what it would add to the book."""

    label: str
    greeks: Greeks = Greeks()
    margin: Decimal = _ZERO
    credit: Decimal = _ZERO
    cost: Decimal = _ZERO        # fees plus expected crossing, always positive


@dataclass(frozen=True, slots=True)
class Close:
    """Closing something already held. Same decision as `Open` with the opposite
    sign: a book too long delta can be fixed by adding a delta-reducer or removing
    a delta-adder, and only a scorer that sees both picks the cheaper."""

    holding_id: str
    cost: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class Hold:
    """Doing nothing, competing on the same scale as everything else."""


Action = Open | Close | Hold


@dataclass(frozen=True, slots=True)
class Contribution:
    """One term's signed contribution and its plain-language reading.

    The rationale is required. A score without one is an assertion no reviewer can
    check."""

    term: str
    value: Decimal
    rationale: str
