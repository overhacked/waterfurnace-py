# Known Issues

- If AWL credentials are changed while the app is running, Hypercorn hangs forever. The fix is to reconfigure credentials and restart the server. The long term fix is for Quart to hook into the `shutdown_trigger` argument of Hypercorn's `serve()` function.

# Production goals

- Log files in instance folder w/ rotation
- Settings file in instance folder
- Alerting on **ERROR** and **WARN**
- Alerting on bad data (Zabbix)
- Separate AWL library from app

# Stretch goals

- Test suite
- Optional value mapping (via `WFReading` object?)
