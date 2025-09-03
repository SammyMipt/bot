class StateError(Exception):
    code = "E_STATE"


class StateNotFound(StateError):
    code = "E_STATE_NOT_FOUND"


class StateExpired(StateError):
    code = "E_STATE_EXPIRED"


class StateRoleMismatch(StateError):
    code = "E_STATE_ROLE_MISMATCH"


class EmailAlreadyBound(Exception):
    code = "E_EMAIL_ALREADY_BOUND"
