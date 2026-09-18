from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import DOMAIN


class SuzieDoctorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: HassioServiceInfo | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id("suzie-doctor-bridge")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Suzie Doctor", data={})
        return self.async_show_form(step_id="user")

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        await self.async_set_unique_id("suzie-doctor-bridge")
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            data = {}
            if self._discovery is not None:
                data = {
                    "addon_slug": self._discovery.slug,
                    "bridge_version": self._discovery.config.get("bridge_version"),
                }
            return self.async_create_entry(title="Suzie Doctor", data=data)
        return self.async_show_form(step_id="hassio_confirm")
