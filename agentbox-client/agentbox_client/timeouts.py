_YIELDED_COMMAND_HTTP_GRACE_SECONDS = 10.0


def exec_command_http_timeout(
    *,
    client_timeout_seconds: float,
    command_timeout_seconds: int | None,
    yield_time_ms: int | None,
) -> float:
    if yield_time_ms is None:
        return (command_timeout_seconds or client_timeout_seconds) + 5

    command_deadline = (command_timeout_seconds or client_timeout_seconds) + 5
    yield_deadline = (yield_time_ms / 1000) + _YIELDED_COMMAND_HTTP_GRACE_SECONDS
    return max(5.0, min(command_deadline, yield_deadline))


def write_stdin_http_timeout(*, yield_time_ms: int | None) -> float:
    if yield_time_ms is None:
        return 35
    return max(5.0, (yield_time_ms / 1000) + _YIELDED_COMMAND_HTTP_GRACE_SECONDS)

