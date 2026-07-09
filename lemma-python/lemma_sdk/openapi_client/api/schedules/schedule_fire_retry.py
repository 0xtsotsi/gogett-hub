from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.schedule_fire_response import ScheduleFireResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    schedule_id: UUID,
    fire_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/schedules/{schedule_id}/fires/{fire_id}/retry".format(
            pod_id=quote(str(pod_id), safe=""),
            schedule_id=quote(str(schedule_id), safe=""),
            fire_id=quote(str(fire_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ScheduleFireResponse | None:
    if response.status_code == 202:
        response_202 = ScheduleFireResponse.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ScheduleFireResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    fire_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ScheduleFireResponse]:
    """Retry Schedule Fire

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        fire_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleFireResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        fire_id=fire_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    schedule_id: UUID,
    fire_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ScheduleFireResponse | None:
    """Retry Schedule Fire

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        fire_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleFireResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        schedule_id=schedule_id,
        fire_id=fire_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    fire_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ScheduleFireResponse]:
    """Retry Schedule Fire

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        fire_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleFireResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        fire_id=fire_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    schedule_id: UUID,
    fire_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ScheduleFireResponse | None:
    """Retry Schedule Fire

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        fire_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleFireResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            schedule_id=schedule_id,
            fire_id=fire_id,
            client=client,
        )
    ).parsed
