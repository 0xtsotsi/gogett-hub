from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyFileMarkdownAttach")


@_attrs_define
class BodyFileMarkdownAttach:
    """
    Attributes:
        data (str):
        path (str):
        images (list[str] | Unset):
    """

    data: str
    path: str
    images: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = self.data

        path = self.path

        images: list[str] | Unset = UNSET
        if not isinstance(self.images, Unset):
            images = self.images

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data": data,
                "path": path,
            }
        )
        if images is not UNSET:
            field_dict["images"] = images

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("data", (None, str(self.data).encode(), "text/plain")))

        files.append(("path", (None, str(self.path).encode(), "text/plain")))

        if not isinstance(self.images, Unset):
            for images_item_element in self.images:
                files.append(
                    ("images", (None, str(images_item_element).encode(), "text/plain"))
                )

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        data = d.pop("data")

        path = d.pop("path")

        images = cast(list[str], d.pop("images", UNSET))

        body_file_markdown_attach = cls(
            data=data,
            path=path,
            images=images,
        )

        body_file_markdown_attach.additional_properties = d
        return body_file_markdown_attach

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
