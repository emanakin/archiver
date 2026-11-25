"""Safe domain failures; never attach library responses or session material."""


class ExecutionError(Exception):
    code = "EXECUTION_FAILED"


class OwnershipLost(ExecutionError):
    code = "OWNERSHIP_LOST"


class BudgetExhausted(ExecutionError):
    code = "BUDGET_EXHAUSTED"


class BudgetDayChanged(BudgetExhausted):
    code = "BUDGET_DAY_CHANGED"


class OperationBlocked(ExecutionError):
    code = "OPERATION_BLOCKED"


class OutcomePending(OperationBlocked):
    code = "OUTCOME_UNKNOWN"


class WindowClosed(ExecutionError):
    code = "OPERATING_WINDOW_CLOSED"


class ManualPause(ExecutionError):
    code = "ACCOUNT_PAUSED"


class SessionRequired(ExecutionError):
    code = "SESSION_REQUIRED"


class Restriction(ExecutionError):
    code = "ACCOUNT_RESTRICTED"


class ProfileVerificationRequired(SessionRequired):
    # A missing/unsupported verification context is a client/session limitation,
    # not evidence of feedback_required, throttling or an account restriction.
    code = "PROFILE_VERIFICATION_REQUIRED"


class ProfileContinuationUnavailable(ProfileVerificationRequired):
    code = "PROFILE_CONTINUATION_UNAVAILABLE"


class UncertainWrite(ExecutionError):
    code = "OUTCOME_UNKNOWN"


class TransientRead(ExecutionError):
    code = "READ_FAILED"


class PolicySkip(ExecutionError):
    code = "POLICY_SKIP"


class MutationRejected(ExecutionError):
    code = "MUTATION_REJECTED"


class Inaccessible(ExecutionError):
    code = "SOURCE_INACCESSIBLE"
