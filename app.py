import asyncio

import backoff
from quart import abort, jsonify, Quart

from awl import AWL, AWLConnectionError, AWLLoginError

app = Quart(__name__, instance_relative_config=False)
app.config.update(
    websockets_warn_after_disconnected=10,
)
app.config.from_pyfile('awl_config.py')


async def awl_reconnection_handler():
    try:
        await app.awl_connection.wait_closed()
    except AWLConnectionError:
        try:
            app.awl_connection.logout()
        except AWLLoginError:
            pass

        await asyncio.sleep(1)
        await establish_awl_session()


async def backoff_handler(details):
    try:
        max_elapsed = float(app.config['websockets_warn_after_disconnected'])
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
                      (AWLConnectionError, AWLLoginError),
                      on_backoff=backoff_handler,
                      on_success=backoff_success_handler)
async def establish_awl_session():
    app.awl_connection = AWL(
        app.config['WATERFURNACE_USER'],
        app.config['WATERFURNACE_PASSWORD']
    )
    await app.awl_connection.connect()
    asyncio.create_task(awl_reconnection_handler())


@app.after_serving
async def close_awl_session():
    await app.awl_connection.close()


async def awl_read_gateway(gwid):
    return await app.awl_connection.read(gwid)


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
    return jsonify(app.awl_connection.login_data)


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
        abort(404)
    if len(gateway_zone) > 1:
        abort(500)
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
