import asyncio
import logging
import sys

import autologging
from quart import jsonify, Quart

from awl import AWL, AWLConnectionError, AWLLoginError

app = Quart(__name__, instance_relative_config=False)
app.config.from_pyfile('awl_config.py')

logging.basicConfig(
    level=autologging.TRACE,
    stream=sys.stdout,
    format="%(levelname)s:%(name)s:%(funcName)s:%(message)s"
)
logging.getLogger('websockets').setLevel(logging.DEBUG)


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


@app.before_serving
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


@app.route('/gateways')
async def list_gateways():
    return jsonify(app.awl_connection.login_data)


@app.route('/gateways/<gwid>')
async def read_gateway(gwid):
    return jsonify(await app.awl_connection.read(gwid))


@app.route('/thermostats')
async def list_thermostats():
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

    return jsonify(thermostats)

app.run()
