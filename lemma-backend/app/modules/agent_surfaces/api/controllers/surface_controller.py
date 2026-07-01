from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.dependencies import require_action
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.modules.agent.api.dependencies import AgentServiceDep
from app.modules.agent_surfaces.api.dependencies import (
    SurfaceEventHandlerDep,
    get_surface_service,
)
from app.modules.agent_surfaces.api.schemas import (
    AgentSurfaceListResponse,
    AgentSurfaceResponse,
    AvailableSurfaceChannelResponse,
    AvailableSurfaceChannelsResponse,
    SurfaceBehaviorConfigInput,
    SurfaceConfigResponse,
    SurfaceSendRequest,
    SurfaceSendResponse,
    SurfaceSetupResponse,
    SurfaceUpsertRequest,
    surface_config_from_input,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceNotFoundError
from app.modules.agent_surfaces.platforms.common import computed_webhook_url
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)

router = APIRouter(prefix="/pods/{pod_id}/surfaces", tags=["Agent Surfaces"])


async def _require_surface_agent_action(
    *,
    ctx,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> None:
    if agent_id is None:
        return
    await ctx.require(
        action,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=agent_id,
            pod_id=pod_id,
        ),
    )


def _surface_response(
    surface: AgentSurfaceEntity,
    *,
    agent_name: str | None = None,
) -> AgentSurfaceResponse:
    return AgentSurfaceResponse(
        id=surface.id,
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        agent_name=agent_name,
        uses_default_agent=surface.agent_id is None,
        platform=surface.surface_type,
        credential_mode=surface.credential_mode,
        account_id=surface.account_id,
        surface_identity_id=surface.surface_identity_id,
        surface_identity_username=surface.surface_identity_username,
        webhook_url=computed_webhook_url(surface),
        config=SurfaceConfigResponse.from_domain(surface.config),
        status=surface.status,
    )


def _surface_platform_from_ref(platform: str) -> SurfacePlatform:
    try:
        return SurfacePlatform(str(platform).upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported surface platform: {platform}",
        ) from exc


async def _resolve_agent_filter(
    *,
    agent_service,
    pod_id: UUID,
    agent_name: str | None,
) -> tuple[UUID | None, bool]:
    """Resolve an optional ``agent_name`` filter to ``(agent_id, match_agent)``.

    Multiple surfaces of a platform can exist in a pod (one per agent); callers
    pass ``agent_name`` to target a specific agent's surface. When omitted, the
    lookup is not agent-scoped (returns the oldest matching surface).
    """
    if not agent_name:
        return None, False
    agent = await agent_service.get_agent_by_name(pod_id=pod_id, name=agent_name)
    return agent.id, True


async def _resolve_channel_routes(
    *,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> list[SurfaceChannelRoute]:
    """Validate route agent names exist, enforcing per-agent permissions."""
    routes: list[SurfaceChannelRoute] = []
    for route in config_input.channels:
        agent_name = None
        if route.agent_name:
            agent = await agent_service.get_agent_by_name(
                pod_id=pod_id,
                name=route.agent_name,
            )
            await _require_surface_agent_action(
                ctx=ctx,
                pod_id=pod_id,
                agent_id=agent.id,
                action=Permissions.AGENT_UPDATE,
            )
            agent_name = agent.name
        routes.append(
            SurfaceChannelRoute(
                channel_id=route.channel_id,
                channel_name=route.channel_name,
                agent_name=agent_name,
            )
        )
    return routes


async def _resolve_surface_config(
    *,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
    channel_routes = await _resolve_channel_routes(
        pod_id=pod_id,
        config_input=config_input,
        agent_service=agent_service,
        ctx=ctx,
    )
    return surface_config_from_input(config_input, channel_routes=channel_routes)


async def _merge_surface_config(
    *,
    existing: SurfaceConfig,
    pod_id: UUID,
    config_input: SurfaceBehaviorConfigInput,
    agent_service,
    ctx,
) -> SurfaceConfig:
    """Apply only the fields the caller actually sent on top of the stored config."""
    updates: dict = {}
    if "identity" in config_input.model_fields_set:
        updates["identity"] = SurfaceIdentityPolicy(
            allowed_domains=config_input.identity.allowed_domains,
            allowed_email_addresses=config_input.identity.allowed_email_addresses,
        )
    if "channels" in config_input.model_fields_set:
        updates["channels"] = await _resolve_channel_routes(
            pod_id=pod_id,
            config_input=config_input,
            agent_service=agent_service,
            ctx=ctx,
        )
    if "dm_conversation_reset_after_hours" in config_input.model_fields_set:
        updates["dm_conversation_reset_after_hours"] = (
            config_input.dm_conversation_reset_after_hours
        )
    if "send_policy" in config_input.model_fields_set:
        updates["send_policy"] = SurfaceSendPolicy(
            allow_send=config_input.send_policy.allow_send
        )
    return existing.model_copy(update=updates)


@router.get(
    "",
    response_model=AgentSurfaceListResponse,
    operation_id="agent.surface.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_surfaces(
    pod_id: UUID,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    limit: int = 100,
    page_token: str | None = None,
    agent_name: str | None = None,
) -> AgentSurfaceListResponse:
    cursor = parse_uuid_page_token(page_token)

    agent_id, match_agent = await _resolve_agent_filter(
        agent_service=agent_service,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    surfaces, next_cursor = await service.list_surfaces_by_pod(
        pod_id,
        agent_id=agent_id,
        match_agent=match_agent,
        cursor=cursor,
        limit=limit,
    )
    items = []
    for surface in surfaces:
        agent_name = None
        if surface.agent_id is not None:
            allowed = await ctx.can(
                Permissions.AGENT_READ,
                ResourceRef(
                    resource_type=ResourceType.AGENT,
                    resource_id=surface.agent_id,
                    pod_id=pod_id,
                ),
            )
            if not allowed:
                continue
            try:
                agent = await agent_service.agent_repository.get(surface.agent_id)
                agent_name = agent.name if agent else None
            except Exception:
                agent_name = None
        items.append(_surface_response(surface, agent_name=agent_name))
    return AgentSurfaceListResponse(
        items=items,
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{platform}",
    operation_id="agent.surface.get",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface(
    pod_id: UUID,
    platform: str,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    agent_name: str | None = None,
):
    agent_id, match_agent = await _resolve_agent_filter(
        agent_service=agent_service,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    surface = await service.get_surface_by_platform_in_pod(
        pod_id=pod_id,
        platform=platform,
        agent_id=agent_id,
        match_agent=match_agent,
    )
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_READ,
    )
    agent_name = None
    if surface.agent_id is not None:
        try:
            agent = await agent_service.agent_repository.get(surface.agent_id)
            agent_name = agent.name if agent else None
        except Exception:
            agent_name = None
    del user
    return _surface_response(surface, agent_name=agent_name)


@router.put(
    "/{platform}",
    operation_id="agent.surface.upsert",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def upsert_surface(
    pod_id: UUID,
    platform: str,
    request: SurfaceUpsertRequest,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    agent_name: str | None = None,
):
    """Create or merge-update a surface for a platform.

    Without ``agent_name`` this targets the pod's primary surface for the
    platform: ``default_agent_name`` in the body (re)assigns which agent handles
    it. Pass the ``agent_name`` query param to address a specific agent's
    surface, which lets several agents/accounts (e.g. multiple bots) each own a
    surface on the same platform. Only fields present in the request are applied
    on update.
    """
    surface_platform = _surface_platform_from_ref(platform)

    filter_agent_id, match_agent = await _resolve_agent_filter(
        agent_service=agent_service,
        pod_id=pod_id,
        agent_name=agent_name,
    )

    update_agent_id = "default_agent_name" in request.model_fields_set
    agent = (
        await agent_service.get_agent_by_name(
            pod_id=pod_id, name=request.default_agent_name
        )
        if request.default_agent_name
        else None
    )
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=agent.id if agent else None,
        action=Permissions.AGENT_UPDATE,
    )

    try:
        # Without agent_name, target the primary surface (back-compat reassign);
        # with it, scope to that agent's surface so multiple agents/accounts can
        # each own a surface on the same platform.
        existing = await service.get_surface_by_platform_in_pod(
            pod_id=pod_id,
            platform=surface_platform.value,
            agent_id=filter_agent_id,
            match_agent=match_agent,
        )
    except AgentSurfaceNotFoundError:
        existing = None

    if existing is None:
        config = await _resolve_surface_config(
            pod_id=pod_id,
            config_input=request.config,
            agent_service=agent_service,
            ctx=ctx,
        )
        surface = await service.create_surface(
            pod_id=pod_id,
            agent_id=agent.id if agent else None,
            platform=surface_platform,
            config=config,
            credential_mode=request.credential_mode,
            account_id=request.account_id,
            ctx=ctx,
        )
        if not request.is_enabled:
            surface = await service.update_surface(
                surface_id=surface.id,
                is_active=False,
                ctx=ctx,
            )
        del user
        return _surface_response(surface, agent_name=agent.name if agent else None)

    config = await _merge_surface_config(
        existing=existing.config,
        pod_id=pod_id,
        config_input=request.config,
        agent_service=agent_service,
        ctx=ctx,
    )
    updated = await service.update_surface(
        surface_id=existing.id,
        agent_id=agent.id if agent else None,
        update_agent_id=update_agent_id,
        config=config,
        credential_mode=(
            request.credential_mode
            if "credential_mode" in request.model_fields_set
            else None
        ),
        account_id=request.account_id,
        is_active=(
            request.is_enabled
            if "is_enabled" in request.model_fields_set
            else None
        ),
        ctx=ctx,
    )
    del user
    return _surface_response(updated, agent_name=agent.name if agent else None)


@router.delete(
    "/{platform}",
    operation_id="agent.surface.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.AGENT_DELETE)],
)
async def delete_surface(
    pod_id: UUID,
    platform: str,
    user: CurrentUser,
    agent_service: AgentServiceDep,
    ctx: PodContextDep,
    service: AgentSurfaceService = Depends(get_surface_service),
    agent_name: str | None = None,
):
    agent_id, match_agent = await _resolve_agent_filter(
        agent_service=agent_service,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    surface = await service.get_surface_by_platform_in_pod(
        pod_id=pod_id,
        platform=platform,
        agent_id=agent_id,
        match_agent=match_agent,
    )
    await _require_surface_agent_action(
        ctx=ctx,
        pod_id=pod_id,
        agent_id=surface.agent_id,
        action=Permissions.AGENT_DELETE,
    )
    await service.delete_surface(surface.id)
    del user
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{platform}/send",
    response_model=SurfaceSendResponse,
    operation_id="agent.surface.send",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def send_surface_message(
    pod_id: UUID,
    platform: str,
    request: SurfaceSendRequest,
    user: CurrentUser,
    ingress: SurfaceEventHandlerDep,
) -> SurfaceSendResponse:
    """Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.
    """
    surface_platform = _surface_platform_from_ref(platform)
    sent = await ingress.send_to_member(
        pod_id=pod_id,
        platform=surface_platform,
        user_id=request.user_id,
        message=request.message,
    )
    if not sent:
        raise HTTPException(
            status_code=404,
            detail="Member has no reachable conversation on this surface.",
        )
    del user
    return SurfaceSendResponse(sent=True)


@router.get(
    "/{platform}/setup",
    response_model=SurfaceSetupResponse,
    operation_id="agent.surface.setup",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def get_surface_setup(
    pod_id: UUID,
    platform: str,
    user: CurrentUser,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> SurfaceSetupResponse:
    """Everything needed to finish setting up this platform's surface.

    Merges the static platform checklist with live webhook + admin-consent
    state. Works before the surface exists (guide only) and after (live state).
    """
    del user
    setup = await service.get_surface_setup(pod_id=pod_id, platform=platform)
    return SurfaceSetupResponse.model_validate(setup)


@router.get(
    "/{platform}/channels",
    operation_id="agent.surface.channels",
    response_model=AvailableSurfaceChannelsResponse,
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_surface_channels(
    pod_id: UUID,
    platform: str,
    service: AgentSurfaceService = Depends(get_surface_service),
) -> AvailableSurfaceChannelsResponse:
    """List the channels/groups this surface bot can be configured to respond in.

    Returns an empty list for platforms without an enumerable channel concept
    (Telegram groups, WhatsApp, email).
    """
    surface = await service.get_surface_by_platform_in_pod(
        pod_id=pod_id,
        platform=platform,
    )
    channels = await service.list_channels(surface=surface)
    return AvailableSurfaceChannelsResponse(
        channels=[
            AvailableSurfaceChannelResponse(
                id=channel.id, name=channel.name, is_member=channel.is_member
            )
            for channel in channels
        ]
    )
