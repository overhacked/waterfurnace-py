#!/usr/bin/env python3
import asyncio
import logging
import os
import signal
import sys
from typing import Any

import autologging
from hypercorn import Config as HyperConfig
from hypercorn.asyncio import serve as hypercorn_serve
import quart
from quart.logging import _setup_logging_queue as setup_logging_queue

import waterfurnace

DEFAULT_LOGGING_FORMATTER = logging.Formatter(
    "%(asctime)s:%(levelname)s:%(name)s:%(funcName)s:%(message)s",
)

waterfurnace.app.shutdown_trigger = asyncio.Event()


def _signal_handler(*_: Any) -> None:
    waterfurnace.app.shutdown_trigger.set()


def _loop_exception_handler(loop, context):
    waterfurnace.app.logger.critical(
        f"Unhandled exception: {context['message']}, shutting down",
        exc_info=context.get('exception')
    )
    waterfurnace.app.shutdown_trigger.set()


def configure_default_logging() -> None:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(DEFAULT_LOGGING_FORMATTER)
    syslog_handler = logging.handlers.SysLogHandler(facility='local1')
    syslog_handler.setLevel(logging.INFO)
    syslog_handler.setFormatter(DEFAULT_LOGGING_FORMATTER)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(setup_logging_queue(console_handler))
    root_logger.addHandler(setup_logging_queue(syslog_handler))

    # Set default levels
    logging.getLogger('awl.AWL').setLevel(logging.ERROR)
    logging.getLogger('quart.app').setLevel(logging.INFO)
    # Suppress access logs by default
    logging.getLogger('quart.serving').setLevel(logging.ERROR)


def configure_app(app: quart.Quart):
    # Different defaults based on development vs production
    if app.env in ('development', 'testing',):
        app.config.from_mapping(
            AWL_API_TIMEOUT=2.0,
            WEBSOCKETS_WARN_AFTER_DISCONNECTED=0,
            LISTEN='localhost:5000',
        )
    elif app.env == 'production':
        app.config.from_mapping(
            AWL_API_TIMEOUT=10.0,
            WEBSOCKETS_WARN_AFTER_DISCONNECTED=10,
            LISTEN='localhost:8000'
        )

    # environment common defaults
    app.config.from_mapping(
        LOG_DIRECTORY=app.instance_path,
        TRACE_LOG=None,
        ACCESS_LOG='access.log',
    )

    # Load configuration file, if present
    app.config.from_envvar('WATERFURNACE_CONFIG', silent=True)

    # Validate configuration
    required_config_keys = [
        'WATERFURNACE_USER',
        'WATERFURNACE_PASSWORD',
        'LOG_DIRECTORY',
        'LISTEN',
    ]
    for name in required_config_keys:
        if name not in app.config:
            print(f"{name} is a required configuration variable")
            sys.exit(255)


def configure_app_logging(app: quart.Quart):
    if app.config.get('ACCESS_LOG') is not None:
        access_handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(
                app.config['LOG_DIRECTORY'],
                app.config['ACCESS_LOG']
            ),
            when='midnight'
        )
        access_handler.setFormatter(
            logging.Formatter('%(asctime)s %(message)s')
        )
        access_handler.setLevel(logging.INFO)
        access_logger = logging.getLogger('quart.serving')
        access_logger.setLevel(logging.INFO)
        # Disable propagation so access lines don't show
        # up in any other logs
        access_logger.propagate = False
        access_logger.addHandler(setup_logging_queue(access_handler))

    if app.config.get('TRACE_LOG') is not None:
        trace_handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(app.config['LOG_DIRECTORY'], app.config['TRACE_LOG']),
            when='midnight'
        )
        trace_handler.setLevel(autologging.TRACE)
        trace_handler.setFormatter(logging.Formatter(
            "%(asctime)s:%(process)s:%(levelname)s:%(filename)s:"
            "%(lineno)s:%(name)s:%(funcName)s:%(message)s"
        ))
        logging.getLogger().addHandler(setup_logging_queue(trace_handler))
        logging.getLogger().setLevel(autologging.TRACE)

        logging.getLogger("awl.AWL").setLevel(autologging.TRACE)
        logging.getLogger("websockets").setLevel(logging.DEBUG)
        logging.getLogger("quart").setLevel(logging.DEBUG)


def run_hypercorn(app: quart.Quart):
    config = HyperConfig()
    config.access_log_format = (
        "%(h)s %(l)s %(u)s [%(asctime)s] \"%(r)s\" %(s)s %(b)s %(D)s"
    )
    config.accesslog = logging.getLogger('quart.serving')
    config.bind = [app.config.get('LISTEN', 'localhost:5000')]
    config.errorlog = config.accesslog
    config.use_reloader = (app.env == 'development')

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    loop.add_signal_handler(signal.SIGINT, _signal_handler)
    loop.set_exception_handler(_loop_exception_handler)
    loop.run_until_complete(
        hypercorn_serve(
            waterfurnace.app,
            config,
            shutdown_trigger=waterfurnace.app.shutdown_trigger.wait
        )
    )


if __name__ == '__main__':
    configure_default_logging()
    configure_app(waterfurnace.app)
    configure_app_logging(waterfurnace.app)
    run_hypercorn(waterfurnace.app)
