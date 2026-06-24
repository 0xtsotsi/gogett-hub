"""Both agentbox clients must send the manager key via X-API-Key.

X-API-Key is a custom header that ingresses/proxies pass through untouched,
whereas Authorization is frequently stripped or rewritten in transit. The
manager key must therefore travel on X-API-Key, not only on Authorization.
"""
from __future__ import annotations

from agentbox_client import AgentBoxClient
from agentbox_client.apps.function_executor import FunctionExecutorClient


def test_agentbox_client_sends_manager_key_only_as_x_api_key() -> None:
    client = AgentBoxClient(base_url="http://agentbox", api_key="manager-key")
    headers = client.client.headers
    assert headers["x-api-key"] == "manager-key"
    # The manager key must NOT travel on Authorization — that header is reserved
    # for the function/lemma token.
    assert "authorization" not in headers


def test_function_executor_client_sends_manager_key_as_x_api_key() -> None:
    client = FunctionExecutorClient(
        manager_base_url="http://agentbox",
        manager_api_key="manager-key",
        lemma_token="user-token",
    )
    headers = client.client.headers
    assert headers["x-api-key"] == "manager-key"
    # The user's lemma token rides on Authorization, not the manager key.
    assert headers["authorization"] == "Bearer user-token"
