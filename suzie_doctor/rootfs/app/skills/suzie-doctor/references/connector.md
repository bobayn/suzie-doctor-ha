# Connector contract v1

App поставляет Skill вместе с кодом и `suite_manifest.json`. Один Ingress, без открытого порта и без общего remote shell.

Через Home Assistant App proxy (сначала обнаружить slug):

- GET `/api/suite`: версии, compatibility, причины блокировки.
- GET `/api/connector/capabilities`: registry; read/write/probe/notify, risk, target, checkpoint/rollback, availability/health.
- GET `/api/connector/skill`: установленная методика и metadata.
- POST `/api/connector/diagnose`: evidence object, diagnosis only. Не передавать `execute` или `explicit_confirmation`.

Лечение остаётся внутри существующего signed server-client → ProtocolEngine пути. Этот v1 не предоставляет внешнего raw primitive/write endpoint и не является автоматически подключённым к ChatGPT OAuth MCP. WebAdapter и APIAdapter вызывают общий DoctorConnectorCore; `doctor.treat` идёт тем же signed server-client путём. Права и подтверждение создаёт доверенный auth/session слой host runtime. HTTP по умолчанию разрешает только чтение, пока такой слой не подключён; не обходить его developer endpoint-ом.

Реализованные семейства: `ha`, `supervisor`, `network` (только уже существующий primary IPv4 auto DNS), `mqtt` (Mosquitto logs), `recorder` (bounded functional write probe), `workflow`. Discovery сообщает каждую операцию отдельно. Probe имеет побочный эффект тестовой записи, это не read-only.

Предусмотренные, но недоступные backend-адаптеры: `docker`, `frigate`, `z2m`, `zwave`, `esphome`, `nodered`, `storage`, `auth_handoff`, `human_action`. Наличие приложения в HA само по себе не означает реализованный adapter. Общий restart add-on не означает поддержку Frigate config patch или Zigbee NVM recovery.

Capability vocabulary: `<adapter>.<existing_engine_primitive>`. Required capabilities выводятся из diagnostics/treatment/checkpoint/fallback/rollback; неизвестный primitive блокирует лечение. Manifest фиксирует protocol schema 1, server wire API 1, а не произвольную совместимость со всеми будущими серверами.

Оба адаптера объявляют `connector_core_version`, `connector_interface_version`, `skill_core_version`, `skill_schema_version`. Несовпадение любого поля блокирует treatment до обращения к серверу. `doctor.skill` возвращает единый текст, metadata и references для всех загрузчиков.

Общие tools: `doctor.capabilities`, `doctor.skill`, `doctor.diagnose`, `doctor.treat`. Последние два принимают `evidence`, но не права или confirmation. Web envelope: `{tool, arguments}`; API envelope: `{name, arguments}`. HTTP пути: `/api/connector/web-tool`, `/api/connector/api-tool` через защищённый Ingress/App proxy. Это адаптеры общего контракта; отдельный публичный OAuth/MCP listener в App не устанавливается.
