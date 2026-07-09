from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.pod_provisioning_status import PodProvisioningStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pod_config import PodConfig


T = TypeVar("T", bound="PodResponse")


@_attrs_define
class PodResponse:
    """Pod response schema.

    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        name (str):
        organization_id (UUID):
        provisioning_attempts (int):
        provisioning_status (PodProvisioningStatus):
        updated_at (datetime.datetime):
        user_id (UUID):
        config (PodConfig | Unset): Typed pod-level configuration.
        description (None | str | Unset):
        icon_url (None | str | Unset):
        provisioning_completed_at (datetime.datetime | None | Unset):
        provisioning_error_code (None | str | Unset):
        provisioning_error_type (None | str | Unset):
        provisioning_started_at (datetime.datetime | None | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    name: str
    organization_id: UUID
    provisioning_attempts: int
    provisioning_status: PodProvisioningStatus
    updated_at: datetime.datetime
    user_id: UUID
    config: PodConfig | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    provisioning_completed_at: datetime.datetime | None | Unset = UNSET
    provisioning_error_code: None | str | Unset = UNSET
    provisioning_error_type: None | str | Unset = UNSET
    provisioning_started_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        name = self.name

        organization_id = str(self.organization_id)

        provisioning_attempts = self.provisioning_attempts

        provisioning_status = self.provisioning_status.value

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        provisioning_completed_at: None | str | Unset
        if isinstance(self.provisioning_completed_at, Unset):
            provisioning_completed_at = UNSET
        elif isinstance(self.provisioning_completed_at, datetime.datetime):
            provisioning_completed_at = self.provisioning_completed_at.isoformat()
        else:
            provisioning_completed_at = self.provisioning_completed_at

        provisioning_error_code: None | str | Unset
        if isinstance(self.provisioning_error_code, Unset):
            provisioning_error_code = UNSET
        else:
            provisioning_error_code = self.provisioning_error_code

        provisioning_error_type: None | str | Unset
        if isinstance(self.provisioning_error_type, Unset):
            provisioning_error_type = UNSET
        else:
            provisioning_error_type = self.provisioning_error_type

        provisioning_started_at: None | str | Unset
        if isinstance(self.provisioning_started_at, Unset):
            provisioning_started_at = UNSET
        elif isinstance(self.provisioning_started_at, datetime.datetime):
            provisioning_started_at = self.provisioning_started_at.isoformat()
        else:
            provisioning_started_at = self.provisioning_started_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "provisioning_attempts": provisioning_attempts,
                "provisioning_status": provisioning_status,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url
        if provisioning_completed_at is not UNSET:
            field_dict["provisioning_completed_at"] = provisioning_completed_at
        if provisioning_error_code is not UNSET:
            field_dict["provisioning_error_code"] = provisioning_error_code
        if provisioning_error_type is not UNSET:
            field_dict["provisioning_error_type"] = provisioning_error_type
        if provisioning_started_at is not UNSET:
            field_dict["provisioning_started_at"] = provisioning_started_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_config import PodConfig

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        organization_id = UUID(d.pop("organization_id"))

        provisioning_attempts = d.pop("provisioning_attempts")

        provisioning_status = PodProvisioningStatus(d.pop("provisioning_status"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        _config = d.pop("config", UNSET)
        config: PodConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = PodConfig.from_dict(_config)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        def _parse_provisioning_completed_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                provisioning_completed_at_type_0 = isoparse(data)

                return provisioning_completed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        provisioning_completed_at = _parse_provisioning_completed_at(
            d.pop("provisioning_completed_at", UNSET)
        )

        def _parse_provisioning_error_code(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provisioning_error_code = _parse_provisioning_error_code(
            d.pop("provisioning_error_code", UNSET)
        )

        def _parse_provisioning_error_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provisioning_error_type = _parse_provisioning_error_type(
            d.pop("provisioning_error_type", UNSET)
        )

        def _parse_provisioning_started_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                provisioning_started_at_type_0 = isoparse(data)

                return provisioning_started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        provisioning_started_at = _parse_provisioning_started_at(
            d.pop("provisioning_started_at", UNSET)
        )

        pod_response = cls(
            created_at=created_at,
            id=id,
            name=name,
            organization_id=organization_id,
            provisioning_attempts=provisioning_attempts,
            provisioning_status=provisioning_status,
            updated_at=updated_at,
            user_id=user_id,
            config=config,
            description=description,
            icon_url=icon_url,
            provisioning_completed_at=provisioning_completed_at,
            provisioning_error_code=provisioning_error_code,
            provisioning_error_type=provisioning_error_type,
            provisioning_started_at=provisioning_started_at,
        )

        pod_response.additional_properties = d
        return pod_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
