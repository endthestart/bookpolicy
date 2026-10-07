"""Scoring terms for "given what I already hold, is this action worth taking?"

Pure, dependency-free, and deliberately ignorant of the application using it.
Nothing here reaches a broker, a database, or the network, you hand it a book
and an action, it hands back a score and the reasoning behind it.
"""

from bookpolicy.errors import BookPolicyError, PnlLeakError, UnknownHoldingError
from bookpolicy.exits import (
    Check,
    ExitDecision,
    ExitEvidence,
    ExitEvidenceError,
    ExitPolicy,
    Verdict,
    decide_exit,
)
from bookpolicy.terms import (
    DeltaGap,
    GammaTheta,
    Term,
    ThetaYield,
    TransactionCost,
    after,
    assert_pnl_blind,
    score,
)
from bookpolicy.types import (
    Action,
    Book,
    Close,
    Contribution,
    Greeks,
    Hold,
    Holding,
    Open,
    Target,
    TaxTreatment,
)

__version__ = '0.1.0'

__all__ = [
    # inputs
    'Action',
    'Book',
    # errors
    'BookPolicyError',
    'Check',
    'Close',
    'Contribution',
    # terms
    'DeltaGap',
    # deciding whether to close
    'ExitDecision',
    'ExitEvidence',
    'ExitEvidenceError',
    'ExitPolicy',
    'GammaTheta',
    'Greeks',
    'Hold',
    'Holding',
    # actions
    'Open',
    'PnlLeakError',
    'Target',
    'TaxTreatment',
    'Term',
    'ThetaYield',
    'TransactionCost',
    'UnknownHoldingError',
    'Verdict',
    '__version__',
    # scoring
    'after',
    'assert_pnl_blind',
    'decide_exit',
    'score',
]
