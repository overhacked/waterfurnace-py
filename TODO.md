# Known Issues

- If AWL credentials are changed while the app is running, Hypercorn hangs forever. The fix is to reconfigure credentials and restart the server. The long term fix is for Quart to hook into the `shutdown_trigger` argument of Hypercorn's `serve()` function.
    * Can be done without upstream modifications by using the [Hypercorn API](https://pgjones.gitlab.io/hypercorn/api_usage.html) to start the server instead of the Hypercorn CLI

# Production goals

- Log files in instance folder w/ rotation
	- See [Logging to multiple destinations](https://docs.python.org/3/howto/logging-cookbook.html#logging-to-multiple-destinations)
	- See [Using file rotation](https://docs.python.org/3/howto/logging-cookbook.html#using-file-rotation)
- Settings file in instance folder
- Alerting on **ERROR** and **WARN**
- Alerting on bad data (Zabbix)
- Separate AWL library from app

# Stretch goals

- Test suite
- Optional value mapping (via `WFReading` object?)
