from __future__ import annotations

from typing import Any

from .connector import ConnectorCore


class ConnectorSurfaceAdapter:
    def __init__(self, connector: ConnectorCore, *, surface: str) -> None:
        if surface not in {"web", "api"}:
            raise ValueError("surface must be web or api")
        self.connector = connector
        self.surface = surface

    def tool_catalog(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "connector_version": self.connector.contract.get("version"),
            "interface_version": self.connector.contract.get("interface_version"),
            "tools": self.connector.tool_catalog(),
        }

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        trusted_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "tool": tool_name,
            "result": await self.connector.invoke(
                tool_name,
                arguments,
                trusted_context=trusted_context,
            ),
        }


class WebConnectorAdapter(ConnectorSurfaceAdapter):
    def __init__(self, connector: ConnectorCore) -> None:
        super().__init__(connector, surface="web")


class ApiConnectorAdapter(ConnectorSurfaceAdapter):
    def __init__(self, connector: ConnectorCore) -> None:
        super().__init__(connector, surface="api")
