"""Exception hierarchy for bookpolicy.

Everything raised here descends from :class:`BookPolicyError`, so a caller can
catch this package's failures without catching everything else. Both concrete
errors are programming errors and both raise rather than degrade.

Absent market data is not represented here. This package is handed a book; it
never fetches one.
"""


class BookPolicyError(Exception):
    """Base class for every error raised by bookpolicy."""


class UnknownHoldingError(BookPolicyError, KeyError):
    """An action names a holding the book does not contain.

    Scoring it as "removes nothing" would report an unknown as a number. Also a
    ``KeyError``, since that is what a lookup miss normally raises.
    """


class PnlLeakError(BookPolicyError):
    """A term's score moved when only unrealized P&L changed.

    Not an ``AssertionError``. This is an invariant checked on every scoring run,
    and ``AssertionError`` reads as a debug aid. See
    :func:`bookpolicy.assert_pnl_blind`.
    """
