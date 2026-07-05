from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import StringAuditBase
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)

if TYPE_CHECKING:
    from .connector import Connector


class ConnectorOperation(StringAuditBase):
    """Stored catalog entry for connector operations."""

    __tablename__ = "connector_operations"

    connector_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Set for operations discovered against a specific org auth-config (MCP server,
    # OpenAPI spec, ...). Null = catalog-static operation. Cascades on auth-config delete.
    auth_config_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"),
        default=None,
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(50), default="LEMMA", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_operation_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
    )
    output_schema: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
    )
    execution: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
    )

    connector: Mapped["Connector"] = relationship("Connector")

    __table_args__ = (
        # Catalog-static ops (GitHub, gmail, the SQL ops): unique per connector+provider+name.
        Index(
            "uq_connector_operations_catalog_name",
            "connector_id",
            "provider",
            "name",
            unique=True,
            postgresql_where=text("auth_config_id IS NULL"),
        ),
        # Per-auth-config discovered ops (MCP tools, OpenAPI-URL): unique within the auth-config.
        Index(
            "uq_connector_operations_authcfg_name",
            "connector_id",
            "provider",
            "auth_config_id",
            "name",
            unique=True,
            postgresql_where=text("auth_config_id IS NOT NULL"),
        ),
        Index(
            "ix_connector_operations_app_provider_operation",
            "connector_id",
            "provider",
            "provider_operation_name",
        ),
        Index("ix_connector_operations_auth_config_id", "auth_config_id"),
    )

    def to_entity(self) -> ConnectorOperationEntity:
        return ConnectorOperationEntity.model_validate(self)

    def __repr__(self) -> str:
        return (
            f"<ConnectorOperation(connector_id={self.connector_id}, name={self.name})>"
        )
