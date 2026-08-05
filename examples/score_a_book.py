"""Score every action against a book, and print the reasoning.

Run it: ``python examples/score_a_book.py``, no data source, no broker, no config.

The situation is the one this package exists for. A book is holding two credit
spreads. A new candidate has been found. The question is not "is this candidate
good?" but "given what I already hold, is *taking* it better than closing
something or doing nothing?", and those three have to compete on one scale.
"""

from decimal import Decimal as D

from bookpolicy import (
    Book,
    Close,
    DeltaGap,
    GammaTheta,
    Greeks,
    Hold,
    Holding,
    Open,
    Target,
    ThetaYield,
    TransactionCost,
    assert_pnl_blind,
    score,
)

NET_LIQ = D('30000')

# Two short put spreads, one of them close to expiry and carrying real gamma.
book = Book(
    holdings=(
        Holding(id='pcs-sep', greeks=Greeks(delta=D('4100'), theta=D('11'), gamma=D('-0.6')),
                margin=D('500'), days_to_expiry=38,
                credit_received=D('130'), cost_to_close=D('55')),
        Holding(id='pcs-aug', greeks=Greeks(delta=D('1700'), theta=D('9'), gamma=D('-2.4')),
                margin=D('500'), days_to_expiry=9,
                credit_received=D('120'), cost_to_close=D('240')),
    ),
    net_liq=NET_LIQ,
    tradeable_capital=D('11000'),
)

# "Carry the exposure of someone who simply owns the underlying", with a no-trade
# band 0.4x net liq wide. Both ratios come from the caller, there are no defaults.
target = Target.like_holding_the_underlying(
    NET_LIQ, invested_ratio=D('1.0'), band_ratio=D('0.4'))

terms = (DeltaGap(D('1')), ThetaYield(D('100')), GammaTheta(D('1')), TransactionCost(D('0.01')))

candidate = Open(
    'put_credit_spread',
    greeks=Greeks(delta=D('19200'), theta=D('7'), gamma=D('-0.5')),
    margin=D('500'), credit=D('130'), cost=D('2.24'),
)

# The guarantee, checked before anything is scored: no term may read P&L.
assert_pnl_blind(book, Hold(), terms, target)

actions = [(candidate, f'open {candidate.label}')]
actions += [(Close(h.id), f'close {h.id}') for h in book.holdings]
actions.append((Hold(), 'hold'))

gap = target.delta - book.greeks.delta
print(f'book delta ${book.greeks.delta:+,.0f} against a ${target.delta:,.0f} target '
      f'(±${target.delta_band:,.0f}) → gap ${gap:+,.0f}')
print(f'book theta ${book.greeks.theta:+,.2f}/day over {len(book.holdings)} holdings\n')

ranked = sorted(
    ((label, *score(book, action, terms, target)) for action, label in actions),
    key=lambda r: -r[1],
)

for label, total, parts in ranked:
    print(f'{label:<28} {total:+8.3f}')
    for p in parts:
        if p.value:
            print(f'    {p.term:<18}{p.value:+8.3f}  {p.rationale}')

print(f'\nwould pick: {ranked[0][0]}')
print('\nNote what decided it. The book is far *under* its delta target, so the term '
      '\nthat wants more exposure outweighs the one that wants more theta, and the '
      '\nnear-expiry holding is charged for its gamma without anyone consulting a '
      '\ncalendar, or its P&L.')
