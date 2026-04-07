_VERBOSE: bool = False


def set_verbose(enabled: bool) -> None:
    global _VERBOSE
    _VERBOSE = bool(enabled)


def is_verbose() -> bool:
    return _VERBOSE


def vprint(*args, **kwargs) -> None:
    if _VERBOSE:
        print(*args, **kwargs)
