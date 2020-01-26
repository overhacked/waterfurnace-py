# Bugs

- Login hangs on invalid credentials
- Presence of sessionid cookie not validated
- Read request template should be `copy.deepcopy()`
- Catch JSON decode errors

# Production goals

- Log files in instance folder w/ rotation
- Settings file in instance folder
- Alerting on **ERROR** and **WARN**
- Alerting on bad data (Zabbix)
- Separate AWL library from app

# Stretch goals

- Test suite
- Optional value mapping (via `WFReading` object?)
- Type annotations, especially `Final`
