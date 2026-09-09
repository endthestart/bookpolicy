"""Current liquidation value is information; historical entry cost is sunk."""

from decimal import Decimal as D

import pytest

from bookpolicy import (
    Book,
    Close,
    Contribution,
    Greeks,
    Hold,
    Holding,
    Open,
    Target,
    assert_pnl_blind,
)


@pytest.mark.parametrize('action', [Hold(), Close('p'), Open('new')])
def test_guard_allows_present_value_dependency(action):
    class PresentValue:
        name = 'present_value'

        def contribute(self, book, action, target):
            return Contribution(self.name, book.holding('p').cost_to_close, 'current value')

    book = Book((Holding('p', Greeks(), D('100'), 30, D('1'), D('2')),), D('1000'))
    assert_pnl_blind(book, action, (PresentValue(),), Target())
