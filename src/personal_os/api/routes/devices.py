"""Devices API endpoints (M2-B).

Implements device registration, presence reporting, revocation, and capability queries.
Enforces user ownership on all endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from personal_os.api.companion_broker import CompanionBroker
from personal_os.api.deps import (
    get_companion_broker_dep,
    get_current_user,
    get_device_service,
    parse_if_match_header,
)
from personal_os.api.schemas import (
    DeviceHeartbeatRequest,
    DeviceListResponse,
    DeviceOperationListResponse,
    DeviceOperationRequest,
    DeviceOperationResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceResponse,
    DeviceUpdateRequest,
)
from personal_os.db.models import User
from personal_os.services.devices import DeviceAuthError, DeviceService
from personal_os.services.interfaces import ServiceConflictError

router = APIRouter(prefix="/api/v1/devices", tags=["Devices"])


@router.post(
    "",
    response_model=DeviceRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register device",
    description="Register or update a local companion device for the authenticated user.",
)
async def register_device(
    payload: DeviceRegisterRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> DeviceRegisterResponse:
    device, token = await service.register_device(
        owner_id=current_user.id,
        device_id=payload.device_id,
        name=payload.name,
        platform=payload.platform,
        capabilities=payload.capabilities,
        public_key=payload.public_key,
    )
    res = DeviceRegisterResponse(
        id=device.id,
        device_id=device.device_id,
        owner_id=device.owner_id,
        name=device.name,
        platform=device.platform,
        capabilities=device.capabilities,
        status=device.status,
        last_seen_at=device.last_seen_at,
        registered_at=device.registered_at,
        revoked_at=device.revoked_at,
        revision=device.revision,
        token=token,
    )
    return res


@router.get(
    "",
    response_model=DeviceListResponse,
    summary="List devices",
    description="List all companion devices registered by the authenticated user.",
)
async def list_devices(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by status (online, offline, stale)"),
    ] = None,
    include_revoked: Annotated[bool, Query(description="Include revoked devices")] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeviceListResponse:
    devices = await service.list_devices(
        owner_id=current_user.id,
        status=status_filter,
        include_revoked=include_revoked,
        limit=limit,
        offset=offset,
    )
    items = [DeviceResponse.model_validate(d) for d in devices]
    return DeviceListResponse(items=items, total=len(items))


@router.get(
    "/{device_id}",
    response_model=DeviceResponse,
    summary="Get device",
    description="Retrieve a single device by ID for the authenticated user.",
)
async def get_device(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> DeviceResponse:
    device = await service.get_device(device_id=device_id, owner_id=current_user.id)
    return DeviceResponse.model_validate(device)


@router.patch(
    "/{device_id}",
    response_model=DeviceResponse,
    summary="Update device",
    description="Update device metadata with optimistic concurrency revision check.",
)
async def update_device(
    device_id: str,
    payload: DeviceUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
    expected_revision: Annotated[int | None, Depends(parse_if_match_header)] = None,
) -> DeviceResponse:
    device = await service.update_device(
        device_id=device_id,
        owner_id=current_user.id,
        expected_revision=expected_revision,
        name=payload.name,
        capabilities=payload.capabilities,
    )
    return DeviceResponse.model_validate(device)


@router.post(
    "/{device_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke device",
    description="Revoke a companion device, preventing future authenticated operations.",
)
async def revoke_device(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> None:
    await service.revoke_device(device_id=device_id, owner_id=current_user.id)


@router.get(
    "/{device_id}/capabilities",
    summary="Get device capabilities",
    description="Retrieve capabilities reported by a device.",
)
async def get_device_capabilities(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> dict:
    device = await service.get_device(device_id=device_id, owner_id=current_user.id)
    return {
        "device_id": device.device_id,
        "capabilities": device.capabilities,
    }


@router.post(
    "/{device_id}/heartbeat",
    response_model=DeviceResponse,
    summary="Device heartbeat",
    description="Report device presence heartbeat via HTTP fallback.",
)
async def device_heartbeat(
    device_id: str,
    payload: DeviceHeartbeatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> DeviceResponse:
    device = await service.record_heartbeat(
        device_id=device_id,
        owner_id=current_user.id,
        status=payload.status,
        timestamp=payload.timestamp,
    )
    return DeviceResponse.model_validate(device)


@router.post(
    "/{device_id}/operations",
    response_model=DeviceOperationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request device capability operation",
    description="Dispatch a bounded capability operation to a registered companion device.",
)
async def request_device_operation(
    device_id: str,
    payload: DeviceOperationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
    broker: Annotated[CompanionBroker, Depends(get_companion_broker_dep)],
) -> DeviceOperationResponse:
    try:
        op = await service.request_operation(
            owner_id=current_user.id,
            device_id=device_id,
            capability=payload.capability,
            params=payload.params,
            idempotency_key=payload.idempotency_key,
            timeout_seconds=payload.timeout_seconds,
            broker=broker,
        )
        return DeviceOperationResponse.model_validate(op)
    except ServiceConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict on {exc.entity} {exc.entity_id}: revision {exc.current_revision}",
        ) from exc
    except DeviceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/{device_id}/operations",
    response_model=DeviceOperationListResponse,
    summary="List device operations",
    description="List capability operations executed on an owned device.",
)
async def list_device_operations(
    device_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by status (pending, running, completed, failed, timeout)",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeviceOperationListResponse:
    await service.get_device(device_id=device_id, owner_id=current_user.id)
    ops = await service.list_operations(
        owner_id=current_user.id,
        device_id=device_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    items = [DeviceOperationResponse.model_validate(op) for op in ops]
    return DeviceOperationListResponse(items=items, total=len(items))


@router.get(
    "/{device_id}/operations/{operation_id}",
    response_model=DeviceOperationResponse,
    summary="Get device operation",
    description="Retrieve a specific capability operation and its structured result.",
)
async def get_device_operation(
    device_id: str,
    operation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
) -> DeviceOperationResponse:
    op = await service.get_operation(
        owner_id=current_user.id,
        device_id=device_id,
        operation_id=operation_id,
    )
    return DeviceOperationResponse.model_validate(op)


@router.post(
    "/{device_id}/operations/{operation_id}/retry",
    response_model=DeviceOperationResponse,
    summary="Retry device capability operation",
    description="Safely retry an operation in failed, timeout, or outcome_unknown state.",
)
async def retry_device_operation(
    device_id: str,
    operation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DeviceService, Depends(get_device_service)],
    broker: Annotated[CompanionBroker, Depends(get_companion_broker_dep)],
    force: Annotated[
        bool,
        Query(description="Force retry of non-idempotent operation with unknown outcome"),
    ] = False,
) -> DeviceOperationResponse:
    try:
        op = await service.retry_operation(
            owner_id=current_user.id,
            device_id=device_id,
            operation_id=operation_id,
            force=force,
            broker=broker,
        )
        return DeviceOperationResponse.model_validate(op)
    except ServiceConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conflict on {exc.entity} {exc.entity_id}: revision {exc.current_revision}",
        ) from exc
    except DeviceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
