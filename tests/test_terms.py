"""The terms, against numbers worked out by hand.

Every assertion here is a claim about behaviour someone can argue with. The point
of the module is that "should I add another one of these?" gets a defensible
answer, so a test that merely re-runs the implementation is worthless.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from bookpolicy import (
    Book,
    Close,
    Contribution,
    DeltaGap,
    GammaTheta,
    Greeks,
    Hold,
    Holding,
    Open,
    PnlLeakError,
    Target,
    ThetaYield,
    TransactionCost,
    UnknownHoldingError,
    after,
    score,
)

D = Decimal


def _spread(hid='p1', *, delta='2000', theta='12', gamma='-0.8', dte=30, margin='500'):
    return Holding(id=hid, greeks=Greeks(delta=D(delta), theta=D(theta), gamma=D(gamma)),
                   margin=D(margin), days_to_expiry=dte, credit_received=D('130'))


def _book(*holdings):
    return Book(holdings=tuple(holdings), net_liq=D('28000'))


# ── the book arithmetic ──────────────────────────────────────────────────────


def test_book_greeks_sum_across_holdings():
    b = _book(_spread('a', delta='2000', theta='12'), _spread('b', delta='-500', theta='8'))
    assert b.greeks.delta == D('1500')
    assert b.greeks.theta == D('20')
    assert b.deployed_margin == D('1000')


def test_closing_removes_exactly_that_holding_s_greeks():
    b = _book(_spread('a', delta='2000'), _spread('b', delta='-500'))
    assert after(b, Close('a')).delta == D('-500')
    assert after(b, Close('b')).delta == D('2000')


def test_opening_adds_the_candidate_s_greeks():
    b = _book(_spread('a', delta='2000'))
    condor = Open('iron_condor', greeks=Greeks(delta=D('-300')), margin=D('500'))
    assert after(b, condor).delta == D('1700')


def test_holding_changes_nothing():
    b = _book(_spread('a', delta='2000'))
    assert after(b, Hold()).delta == D('2000')


def test_an_unknown_holding_is_an_error_not_a_zero():
    """A close naming a position we do not hold must not silently score as
    'removes nothing'. That is a financial unknown wearing a number."""
    with pytest.raises(UnknownHoldingError, match='nope'):
        after(_book(_spread('a')), Close('nope'))


# ── DeltaGap: the no-trade band ──────────────────────────────────────────────


def test_inside_the_band_nothing_is_worth_doing():
    """The whole point of a no-trade region: within it no move is worth its costs,
    which is what stops a 15-minute cadence churning on noise."""
    target = Target(delta=D(0), delta_band=D('5000'))
    b = _book(_spread('a', delta='1000'))
    c = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('500'))), target)
    assert c.value == D(0)
    assert 'inside the' in c.rationale


def test_closing_the_gap_scores_positive_and_proportionally():
    """Linear in distance closed: halving the gap scores half of closing it."""
    target = Target(delta=D(0), delta_band=D('1000'))
    b = _book(_spread('a', delta='4000'))
    full = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('-4000'))), target).value
    half = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('-2000'))), target).value
    assert full > half > 0
    assert half * 2 == full


def test_widening_the_gap_is_charged_for_it():
    target = Target(delta=D(0), delta_band=D('1000'))
    b = _book(_spread('a', delta='4000'))
    c = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('+2000'))), target)
    assert c.value < 0 and 'widens' in c.rationale


def test_a_close_can_score_as_well_as_an_open():
    """Closing is the same decision with the opposite sign: a book too long delta
    can be fixed by adding a reducer or removing an adder, and only a scorer that
    sees both picks the cheaper."""
    target = Target(delta=D(0), delta_band=D('1000'))
    b = _book(_spread('a', delta='4000'))
    assert DeltaGap().contribute(b, Close('a'), target).value > 0


# ── GammaTheta: legacy local-sensitivity diagnostic ─────────────────────────


def test_pressure_rises_as_expiry_approaches():
    """A smaller linear-time denominator raises the ratio, not an optimal exit claim."""
    far = _book(_spread('a', gamma='-0.8', theta='12', dte=45))
    near = _book(_spread('a', gamma='-0.8', theta='12', dte=10))
    assert (GammaTheta().contribute(near, Close('a'), Target()).value
            > GammaTheta().contribute(far, Close('a'), Target()).value)


def test_more_gamma_for_the_same_decay_means_more_pressure():
    calm = _book(_spread('a', gamma='-0.4', theta='12', dte=21))
    violent = _book(_spread('a', gamma='-2.0', theta='12', dte=21))
    assert (GammaTheta().contribute(violent, Close('a'), Target()).value
            > GammaTheta().contribute(calm, Close('a'), Target()).value)


def test_zero_theta_does_not_imply_a_free_close():
    b = _book(_spread('a', theta='0', gamma='-0.8', dte=3))
    c = GammaTheta().contribute(b, Close('a'), Target())
    assert c.value == D('0') and 'no economic close benefit' in c.rationale


def test_positive_gamma_is_not_short_gamma_exit_pressure():
    b = _book(_spread('a', theta='12', gamma='0.8', dte=3))
    assert GammaTheta().contribute(b, Close('a'), Target()).value == 0


def test_negative_theta_and_tenor_do_not_create_positive_close_pressure():
    b = _book(_spread('a', theta='-12', gamma='-0.8', dte=-3))
    assert GammaTheta().contribute(b, Close('a'), Target()).value == 0


def test_it_says_nothing_about_opening():
    b = _book(_spread('a'))
    assert GammaTheta().contribute(b, Open('x'), Target()).value == D(0)


def test_unrealized_pnl_is_not_an_input():
    """Closing because the book needs it is risk management; closing because the
    position is red is sunk cost. Two identical positions differing only in what it
    costs to close them must score identically."""
    winning = Holding('a', Greeks(theta=D('12'), gamma=D('-0.8')), D('500'), 21,
                      credit_received=D('130'), cost_to_close=D('40'))
    losing = Holding('a', Greeks(theta=D('12'), gamma=D('-0.8')), D('500'), 21,
                     credit_received=D('130'), cost_to_close=D('380'))
    t = GammaTheta()
    assert (t.contribute(_book(winning), Close('a'), Target()).value
            == t.contribute(_book(losing), Close('a'), Target()).value)


# ── cost, and the sum ────────────────────────────────────────────────────────


def test_cost_is_always_a_charge():
    b = _book(_spread('a'))
    c = TransactionCost().contribute(b, Open('x', cost=D('4.48')), Target())
    assert c.value == D('-4.48')


def test_cost_is_what_gives_the_band_its_meaning():
    """Without a real cost any non-zero improvement is worth taking and the
    no-trade band collapses to zero width. A tiny gain must not survive its fee."""
    target = Target(delta=D(0), delta_band=D('5000'))
    b = _book(_spread('a', delta='6000'))
    terms = (DeltaGap(weight=D('1')), TransactionCost(weight=D('1')))
    tiny = Open('x', greeks=Greeks(delta=D('-100')), cost=D('4.48'))
    total, parts = score(b, tiny, terms, target)
    assert total < 0, 'a 100-dollar delta improvement must not survive a $4.48 fee'
    assert [p.term for p in parts] == ['delta_gap', 'transaction_cost']


def test_every_contribution_carries_a_reading():
    """A score without a reading is an assertion nobody can check. Every term must
    hand back language a reader can disagree with, not just a number."""
    b = _book(_spread('a', delta='4000'))
    target = Target(delta=D(0), delta_band=D('1000'))
    _total, parts = score(
        b, Open('x', greeks=Greeks(delta=D('-2000')), cost=D('4.48')),
        (DeltaGap(), GammaTheta(), TransactionCost()), target)
    assert len(parts) == 3
    for p in parts:
        assert p.rationale and not p.rationale.isspace()


def test_scoring_returns_the_reasoning_not_just_the_total():
    b = _book(_spread('a'))
    total, parts = score(b, Hold(), (DeltaGap(), TransactionCost()))
    assert total == D(0) and len(parts) == 2


# ── the objective: theta, subject to a shareholder's delta ───────────────────


# A caller that already has a "meaningfully long" threshold expresses the band in
# terms of it, so one number drives both readings: band = long_ratio - invested.
LONG_RATIO, INVESTED = D('1.4'), D('1.0')
NET_LIQ = D('30000')


def _shareholder(capital):
    return Target.like_holding_the_underlying(
        capital, invested_ratio=INVESTED, band_ratio=LONG_RATIO - INVESTED)


def test_the_target_is_a_shareholder_not_delta_neutral():
    """"Maximize theta while carrying the exposure of someone who simply owns the
    underlying." Zero is technically correct and practically wrong for a small
    account, a hedged book needs enough capital to hold both sides."""
    t = _shareholder(NET_LIQ)
    assert t.delta == NET_LIQ, 'fully invested, not flat'
    assert t.delta_band == D('12000.0')


def test_the_band_is_derived_from_the_long_threshold_not_restated():
    """The band's upper edge lands exactly where the caller's own threshold already
    calls the book meaningfully long, one number, two readings that agree by
    construction rather than by someone keeping them in sync."""
    t = _shareholder(NET_LIQ)
    assert t.delta + t.delta_band == NET_LIQ * LONG_RATIO


def test_the_ratios_have_no_defaults():
    """They live in the caller's config. A default here that happened to agree with
    the caller's own long/short threshold would be the same financial number in two
    places, drifting the first time either moved."""
    with pytest.raises(TypeError):
        Target.like_holding_the_underlying(NET_LIQ)


def test_the_target_scales_with_the_account():
    """A ratio, not a dollar figure, for the same reason the long/short band is one:
    a fixed number drifts away from its own intent as the account grows."""
    assert _shareholder(D('100000')).delta == _shareholder(D('10000')).delta * 10


def test_theta_is_scored_per_dollar_of_capital_not_in_absolute_dollars():
    """Capital is the binding constraint: $9/day on $500 beats $12/day on $1,000,
    and only the ratio says so."""
    b = _book()
    cheap = Open('a', greeks=Greeks(theta=D('9')), margin=D('500'))
    dear = Open('b', greeks=Greeks(theta=D('12')), margin=D('1000'))
    t = ThetaYield()
    assert t.contribute(b, cheap, Target()).value > t.contribute(b, dear, Target()).value


def test_closing_a_productive_position_is_charged_for_the_theta_it_gives_up():
    """What should stop the exit terms unwinding a healthy book."""
    b = _book(_spread('a', theta='12', margin='500'))
    c = ThetaYield().contribute(b, Close('a'), Target())
    assert c.value < 0 and 'gives up' in c.rationale


def test_the_objective_and_the_constraint_disagree_and_that_is_the_point():
    """A book carrying far less delta than its target is *under*-invested, and the
    two halves of the objective then pull opposite ways: a put spread adds the
    exposure the target wants, while an iron condor adds more theta but leaves the
    book short of a shareholder's delta. The constraint has to win, or "maximize
    theta" quietly becomes "be delta-neutral and collect premium"."""
    b = Book(holdings=(_spread('c6', delta='-1300', theta='9'),), net_liq=NET_LIQ)
    target = _shareholder(NET_LIQ)
    terms = (DeltaGap(D('1')), ThetaYield(D('100')), TransactionCost(D('0.01')))

    spread, _ = score(b, Open('put_credit_spread', Greeks(delta=D('19200'), theta=D('7')),
                              D('500'), D('130'), D('2.24')), terms, target)
    condor, _ = score(b, Open('iron_condor', Greeks(delta=D('-250'), theta=D('9')),
                              D('500'), D('250'), D('4.48')), terms, target)
    assert spread > condor, 'the shareholder-delta target must favour the put spread'


# ── tax treatment: the one real exception, and why it does not apply here ─────


def test_mark_to_market_removes_the_only_exception_to_the_sunk_cost_rule():
    """The general theory has one genuine carve-out: under realization accounting,
    cost basis becomes decision-relevant near the year boundary (harvesting), and
    re-entry may be penalised. Under mark-to-market the result lands in the year's
    income whether or not the position was closed, so *when* it closes carries no
    tax consequence and the carve-out vanishes."""
    from bookpolicy import TaxTreatment

    mtm = TaxTreatment(marks_to_market=True)
    realized = TaxTreatment(marks_to_market=False)
    assert mtm.unrealized_pnl_is_decision_relevant is False
    assert realized.unrealized_pnl_is_decision_relevant is True


def test_no_term_reads_unrealized_pnl():
    """Enforced across *every* term, so a new one cannot opt out by omission. The
    failure this catches is silent and one-directional: a term that quietly reads
    P&L reproduces the disposition effect, riding losers, cutting winners, while
    still looking like a risk model."""
    from bookpolicy import assert_pnl_blind

    book = _book(_spread('a', delta='4000', theta='12'), _spread('b', delta='-900'))
    target = _shareholder(NET_LIQ)
    terms = (DeltaGap(), GammaTheta(), ThetaYield(), TransactionCost())
    for action in (Open('x', Greeks(delta=D('19200'), theta=D('7')), D('500'),
                        D('130'), D('2.24')),
                   Close('a'), Hold()):
        assert_pnl_blind(book, action, terms, target)


def test_the_guard_actually_catches_a_pnl_reading_term():
    """A guard that cannot fail is not a guard."""
    from bookpolicy import assert_pnl_blind

    @dataclass(frozen=True, slots=True)
    class Disposition:
        name: str = 'disposition'

        def contribute(self, book, action, target):
            # The bug in its natural form: "this one is down, get out."
            held = sum((h.cost_to_close - h.credit_received for h in book.holdings), D(0))
            return Contribution(self.name, -held, 'closes losers')

    book = _book(_spread('a'))
    with pytest.raises(PnlLeakError, match='unrealized P&L'):
        assert_pnl_blind(book, Close('a'), (Disposition(),), _shareholder(NET_LIQ))


# ── the other side of the target ─────────────────────────────────────────────


def test_a_book_past_its_target_is_pulled_back_down():
    """Every other DeltaGap test has the book *below* target, where signed and
    absolute distance agree, so none of them can tell `abs(...)` from no `abs(...)`
    at all. Overshooting is the case that separates them, and it is the case where a
    Close is the right answer: a book long past its target wants *less* exposure,
    and an action that adds more must be charged, not rewarded."""
    target = Target(delta=D('10000'), delta_band=D('1000'))
    # Two holdings, $21,000 of delta against a $10,000 target, $10,000 past the top
    # of the band. Two, because closing a book's only position lands it at zero,
    # which for a non-zero target is just as wrong in the other direction.
    b = _book(_spread('a', delta='15000'), _spread('b', delta='6000'))
    reducer = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('-5000'))), target)
    adder = DeltaGap().contribute(b, Open('y', greeks=Greeks(delta=D('+5000'))), target)
    assert reducer.value > 0, 'moving back toward the target must score positive'
    assert adder.value < 0, 'moving further past the target must be charged'
    assert DeltaGap().contribute(b, Close('b'), target).value > 0


def test_overshooting_and_undershooting_by_the_same_distance_score_the_same():
    """The gap is a distance, not a direction. $3,000 the wrong way is $3,000 the
    wrong way whichever side of the target it sits on."""
    target = Target(delta=D('10000'), delta_band=D('1000'))
    over = _book(_spread('a', delta='13000'))
    under = _book(_spread('a', delta='7000'))
    closes_over = DeltaGap().contribute(over, Open('x', greeks=Greeks(delta=D('-2000'))), target)
    closes_under = DeltaGap().contribute(under, Open('x', greeks=Greeks(delta=D('2000'))), target)
    assert closes_over.value == closes_under.value


def test_the_band_edge_is_inside_the_band():
    """`<=`, not `<`. A book sitting exactly on the edge is still at rest, otherwise
    the boundary itself becomes a trigger and the band has a hair-trigger rim."""
    target = Target(delta=D('10000'), delta_band=D('1000'))
    b = _book(_spread('a', delta='11000'))          # exactly on the upper edge
    c = DeltaGap().contribute(b, Open('x', greeks=Greeks(delta=D('-1000'))), target)
    assert c.value == D(0) and 'inside the' in c.rationale


# ── missing margin is not free capital ───────────────────────────────────────


def test_theta_per_dollar_refuses_to_divide_by_a_missing_margin():
    """A broker that reports no margin gives zero, not None. Scoring theta against
    it is a division by zero at best and an infinitely attractive position at worst
   , so the term declines to judge rather than inventing a ratio."""
    b = _book()
    c = ThetaYield().contribute(b, Open('x', greeks=Greeks(theta=D('9')), margin=D(0)), Target())
    assert c.value == D(0) and 'cannot judge' in c.rationale


def test_closing_a_holding_with_no_margin_figure_scores_nothing():
    b = _book(Holding('a', Greeks(theta=D('12')), D(0), 21))
    c = ThetaYield().contribute(b, Close('a'), Target())
    assert c.value == D(0) and 'no margin figure' in c.rationale
