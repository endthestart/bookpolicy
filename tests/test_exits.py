"""Deciding whether to close, worked on real October 2026 XSP positions.

The book: dollar delta about -$20,011 against a +$7,922 target, band $11,091 — far
short of its target, because condors outnumber put spreads.
"""

from decimal import Decimal as D

import pytest

from bookpolicy import (
    Book,
    ExitEvidence,
    ExitEvidenceError,
    ExitPolicy,
    Greeks,
    Holding,
    Target,
    UnknownHoldingError,
    Verdict,
    decide_exit,
)

TARGET = Target(delta=D('7922'), delta_band=D('11091'))
POLICY = ExitPolicy(D('1'))


def book(spread_delta):
    """The rest of the book at -$20,011 less the spread, plus the spread."""
    return Book(holdings=(Holding('rest', Greeks(delta=D('-20011') - spread_delta), D('8000'), 30),
                          Holding('spread', Greeks(delta=spread_delta), D('370'), 28)))


def evidence(**changes):
    base = dict(settlement_best=D('0'), settlement_worst=D('-500'), close_mid=D('-106'),
                close_natural=D('-111'), bad_day=D('121'), worst_loss_with=D('4528'),
                worst_loss_without=D('4100'))
    return ExitEvidence.from_values(**{**base, **changes})


def test_reward_left_and_loss_left_come_from_signed_values():
    e = evidence(close_mid=D('-286'), close_natural=D('-297'))
    assert (e.reward_left, e.loss_left, e.closing_dominated) == (D('286'), D('214'), False)


def test_fourteen_days_at_twenty_percent_profit_is_an_exit_when_the_book_improves():
    """$106 left to gain, a bad day costs $121 (0.88): and removing this short-delta
    spread moves the book toward its target."""
    decision = decide_exit(book(D('-3877')), 'spread', evidence(), TARGET, POLICY)
    assert decision.verdict is Verdict.EXIT
    assert [c.test for c in decision.checks] == ['reward left against one bad day', 'delta gap',
                                                 'worst stressed loss']


def test_the_same_profit_with_thirty_days_left_holds():
    """At 30 days the same 2% day costs $78: reward covers 1.36 bad days."""
    decision = decide_exit(book(D('-3877')), 'spread', evidence(bad_day=D('78')), TARGET, POLICY)
    assert decision.verdict is Verdict.HOLD
    assert decision.reasons == ('what is left to gain covers 1.36 bad days, above 1',)


def test_a_profitable_hedge_is_kept():
    """Put spread 57 on October 6: reward no longer covers a bad day (0.86), but it is
    long delta in a book far short of its target. Closing it widens the gap from
    $27,933 to $31,810."""
    decision = decide_exit(book(D('3877')), 'spread', evidence(close_mid=D('-103'),
                                                                close_natural=D('-108'),
                                                                bad_day=D('120')),
                           TARGET, POLICY)
    assert decision.verdict is Verdict.HOLD
    assert decision.reasons == ('closing moves the book away from its goals: delta gap',)
    gap = decision.checks[1]
    assert gap.measured == D('31810') and gap.rationale.endswith('$27,933 → $31,810 (band $11,091)')


def test_near_maximum_loss_keeps_its_recovery():
    """Spot under both strikes: $451.50 to close at the midpoint, a bad day costs $30
    more. Little left to lose, much to win back."""
    decision = decide_exit(book(D('-3877')), 'spread',
                           evidence(close_mid=D('-451.50'), close_natural=D('-490'), bad_day=D('30')),
                           TARGET, POLICY)
    assert decision.verdict is Verdict.HOLD and decision.checks[0].measured == D('15.05')


def test_closing_that_costs_more_than_the_worst_settlement_is_never_proposed():
    """September 18: $516.50 to close at the midpoint, $613 at the natural, against a
    $500 maximum loss."""
    e = evidence(close_mid=D('-516.50'), close_natural=D('-613'))
    assert e.closing_dominated and e.loss_left == 0
    decision = decide_exit(book(D('-3877')), 'spread', e, TARGET, POLICY)
    assert decision.verdict is Verdict.HOLD and decision.checks == ()


def test_more_risk_without_the_holding_blocks_the_exit_and_no_loss_means_no_bad_day():
    rising = decide_exit(book(D('-3877')), 'spread', evidence(worst_loss_without=D('4600')),
                         TARGET, POLICY)
    assert rising.reasons == ('closing moves the book away from its goals: worst stressed loss',)
    calm = decide_exit(book(D('-3877')), 'spread', evidence(bad_day=D('0')), TARGET, POLICY)
    assert calm.verdict is Verdict.HOLD and 'no bad day' in calm.reasons[0]


def test_bad_inputs_are_refused():
    with pytest.raises(ExitEvidenceError):
        evidence(close_natural=D('-100'))                  # natural better than the midpoint
    with pytest.raises(ExitEvidenceError):
        ExitPolicy(D('0'))
    with pytest.raises(ExitEvidenceError):
        evidence(bad_day=D('-1'))
    with pytest.raises(UnknownHoldingError):
        decide_exit(book(D('0')), 'missing', evidence(), TARGET, POLICY)
