import asyncio
import functools
import logging

import backoff
import quart
from quart import abort, jsonify, request

from awl import (
    AWL,
    AWLConnectionError,
    AWLNotConnectedError,
    AWLLoginError,
    AWLTransactionError,
    AWLTransactionTimeout
)
from timed_cache import timed_cache


# Monkeypatch Quart's logging functions so
# they don't force their own handlers too far down
# the logging hierarchy
quart.app.create_logger = (
    lambda app: logging.getLogger('quart.app')
)
quart.app.create_serving_logger = (
    lambda: logging.getLogger('quart.serving')
)

app = quart.Quart(__name__)


def get_runtime_config(key, default=None):
    return functools.partial(app.config.get, key, default)


async def awl_reconnection_handler():
    try:
        await app.awl_connection.wait_closed()
        app.logger.debug('app.awl_connection.wait_closed() finished')
    except AWLConnectionError:
        try:
            app.logger.info('Closing AWL connection')
            await app.awl_connection.close()
            app.logger.info('Closed AWL connection')
        except (AWLConnectionError, AWLLoginError):
            app.logger.warning('AWL logout failed; ignoring')
            pass
    except AWLLoginError:
        # Login failed during session renewal
        app.logger.warning('AWL login failed during session renewal')

    # Re-establish session whenever wait_closed returns,
    # whether with an exception or not
    await asyncio.sleep(1)
    app.logger.info('Reconnecting to AWL')
    del app.awl_connection
    await establish_awl_session()


async def backoff_handler(details):
    try:
        max_elapsed = float(app.config['WEBSOCKETS_WARN_AFTER_DISCONNECTED'])
    except ValueError:
        max_elapsed = 0.0

    if details['elapsed'] > max_elapsed:
        app.logger.critical("Cannot reconnect to AWL after {tries} tries "
                            "over {elapsed:0.1f} seconds. "
                            "Retrying in {wait:0.1f} "
                            "seconds.".format(**details))


async def backoff_success_handler(details):
    if details['tries'] > 1:
        app.logger.warning("Reconnected to AWL after {elapsed:0.1f} "
                           "seconds ({tries} tries)".format(**details))


@app.before_serving
@backoff.on_exception(backoff.expo,
                      AWLConnectionError,
                      on_backoff=backoff_handler,
                      on_success=backoff_success_handler,
                      max_time=get_runtime_config('AWL_CONNECT_TIMEOUT'))
@backoff.on_exception(backoff.expo,
                      AWLLoginError,
                      on_backoff=backoff_handler,
                      on_success=backoff_success_handler,
                      max_time=get_runtime_config('AWL_LOGIN_TIMEOUT'))
async def establish_awl_session():
    app.awl_connection = AWL(
        app.config['WATERFURNACE_USER'],
        app.config['WATERFURNACE_PASSWORD']
    )
    await app.awl_connection.connect()
    asyncio.create_task(
        awl_reconnection_handler(),
        name='reconnection_loop'
    )


@app.after_serving
async def close_awl_session():
    await app.awl_connection.close()


# Cache reads for 10 seconds to keep from hammering
# the Symphony API
@timed_cache(seconds=10)
async def awl_read_gateway(gwid):
    try:
        return await awl_read_gateway_retry_wrapper(gwid)
    except AWLTransactionTimeout:
        abort(504, "AWL read timed out")
    except AWLTransactionError as e:
        abort(503, f"AWL transaction error: {e!s}")
    except AWLNotConnectedError:
        abort(504, "AWL API not connected")


@backoff.on_exception(backoff.constant,
                      (AWLConnectionError, AWLTransactionTimeout),
                      max_time=get_runtime_config('AWL_API_TIMEOUT', 0))
async def awl_read_gateway_retry_wrapper(gwid):
    return await app.awl_connection.read(gwid)


def awl_enumerate_gateways():
    awl_login_data = app.awl_connection.login_data
    gateways = list()
    for location in awl_login_data['locations']:
        for gateway in location['gateways']:
            try:
                gateways.append({
                    'location': location.get('description'),
                    'gwid': gateway['gwid'],
                    'system_name': gateway.get('description'),
                })
            except KeyError:
                app.logger.error("Couldn't get gwid")

    return gateways


def awl_enumerate_zones():
    awl_login_data = app.awl_connection.login_data
    thermostats = list()
    for location in awl_login_data['locations']:
        for gateway in location['gateways']:
            for key, zone_name in gateway['tstat_names'].items():
                if zone_name is not None:
                    try:
                        thermostats.append({
                            'location': location.get('description'),
                            'gwid': gateway['gwid'],
                            'system_name': gateway.get('description'),
                            'zoneid': int(key[1:]),
                            'zone_name': zone_name,
                        })
                    except ValueError:
                        app.logger.error(
                            "Couldn't convert zone key \"{key[1:]}\" to int"
                        )
                    except KeyError:
                        app.logger.error("Couldn't get gwid")

    return thermostats


@app.route('/zones')
async def list_thermostats():
    return jsonify(awl_enumerate_zones())


@app.route('/gateways')
async def list_gateways():
    if 'raw' in request.args:
        return jsonify(app.awl_connection.login_data)
    return jsonify(awl_enumerate_gateways())


@app.route('/gateways/<gwid>')
async def read_gateway(gwid):
    gateway_data = await awl_read_gateway(gwid)
    return jsonify(gateway_data)


@app.route('/gateways/<gwid>/zones')
async def list_gateway_zones(gwid):
    if gwid == '*':
        return await list_thermostats()

    gateway_zones = [
        zone for
        zone in awl_enumerate_zones()
        if zone['gwid'] == gwid
    ]
    return jsonify(gateway_zones)


@app.route('/gateways/<gwid>/zones/<int:zoneid>')
async def view_gateway_zone(gwid, zoneid):
    gateway_zone = [
        zone for
        zone in awl_enumerate_zones()
        if zone['gwid'] == gwid and zone['zoneid'] == zoneid
    ]
    if len(gateway_zone) == 0:
        abort(404,
              f"The gateway {gwid} does not have a zone {zoneid}",
              'Zone Not Found')
    if len(gateway_zone) > 1:
        abort(500,
              'More than one zone was returned '
              'for the gateway/zone ID specified')
    return jsonify(gateway_zone[0])


@app.route('/gateways/<gwid>/zones/<int:zoneid>/details')
async def read_zone(gwid, zoneid):
    gateway_data = await awl_read_gateway(gwid)

    # Find all zone-specific data
    # in the gateway
    zone_prefix = f"iz2_z{zoneid}_"
    zone_raw_data = {
        key: value for
        (key, value) in gateway_data.items()
        if key.startswith(zone_prefix)
    }
    if len(zone_raw_data) == 0:
        abort(404,
              f"The gateway {gwid} does not have a zone {zoneid}",
              'Zone Not Found')

    # Pull e.g. $.iz2_z1_activesettings.* up
    # to the top level
    zone_data = dict()
    zone_data.update(
        zone_raw_data.pop(f"{zone_prefix}activesettings", dict())
    )
    zone_data.update(zone_raw_data)

    # Strip the prefix
    response_data = {
        key.replace(zone_prefix, '', 1): value
        for (key, value)
        in zone_data.items()
    }

    return jsonify(response_data)
