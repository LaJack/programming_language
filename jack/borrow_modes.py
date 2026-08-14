BORROW_MODES = {'in', 'out', 'inout'}


def borrow_mode_can_read(mode: str | None) -> bool:
    return mode in {'in', 'inout'}


def borrow_mode_can_write(mode: str | None) -> bool:
    return mode in {'out', 'inout'}


def borrow_mode_compatible(expected: str | None, actual: str | None) -> bool:
    if expected not in BORROW_MODES or actual not in BORROW_MODES:
        return False
    if expected == 'in':
        return borrow_mode_can_read(actual)
    if expected == 'out':
        return borrow_mode_can_write(actual)
    return actual == 'inout'
