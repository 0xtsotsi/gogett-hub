from __future__ import annotations


class AgentBoxError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_method: str | None = None,
        request_url: str | None = None,
        response_text: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.request_method = request_method
        self.request_url = request_url
        self.response_text = response_text
        self.retryable = retryable


class AgentBoxHTTPStatusError(AgentBoxError):
    pass


class AgentBoxTransportError(AgentBoxError):
    pass


class AgentBoxResponseValidationError(AgentBoxError):
    pass

