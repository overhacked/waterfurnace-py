#!/usr/bin/env python3

import asyncio
import json
import re
import requests
from urllib3.util.url import parse_url
import websockets
from autologging import logged, traced


@logged
@traced
class AWL:

    LOGIN_URI = 'https://symphony.mywaterfurnace.com/account/login'
    AWLCONFIG_URI = 'https://symphony.mywaterfurnace.com/assets/js/awlconfig.js.php'
    COMMAND_SOURCE = 'consumer dashboard'

    def __init__(self, username, password):
        self.username = username
        self.password = password

        self.http_session = requests.Session()
        self.http_session.cookies.set(
            'legal-acknowledge', 'yes',
            domain=parse_url(self.LOGIN_URI).host,
            path='/'
        )

        login_response = self.http_session.post(
            self.LOGIN_URI,
            allow_redirects=False,  # Just the first response
            data={
                'op': 'login',
                'redirect': '/',
                'emailaddress': self.username,
                'password': self.password,
            }
        )
        login_response.raise_for_status()

        wssuri_response = self.http_session.get(self.AWLCONFIG_URI)
        wssuri_response.raise_for_status()
        self.websockets_uri = re.search(
            r'wss?://[^"\']+',
            wssuri_response.text
        )[0]
        self.websockets_connection = None

        self.transactions = dict()
        self.next_transaction_id = None
        self.transaction_lock = asyncio.Lock()

    def __del__(self):
        self.http_session.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *excinfo):
        await self.close()

    async def connect(self):
        self.websockets_connection = await (
            websockets.connect(self.websockets_uri)
        )
        async with self.transaction_lock:
            self.next_transaction_id = 1
        asyncio.create_task(self._receive())

    async def listen(self):
        await self.websockets_connection.wait_closed()

    async def close(self):
        if self.websockets_connection is not None:
            await self.websockets_connection.close()
            async with self.transaction_lock:
                self.next_transaction_id = None

    @property
    def session_id(self):
        return self.http_session.cookies.get('sessionid', default=None)

    async def _receive(self):
        async for message in self.websockets_connection:
            self.__log.debug(f"< {message}")
            data = json.loads(message)
            tid = data['tid']
            try:
                async with self.transaction_lock:
                    self.transactions.pop(tid).set_result(data)
            except KeyError:
                self.__log.warning(f"< Unknown transaction id {tid}")

    async def _command(self, command, **kwargs):
        async with self.transaction_lock:
            tid = self.next_transaction_id
            self.next_transaction_id += 1
            transaction_future = asyncio.get_running_loop().create_future()
            self.transactions[tid] = transaction_future

        payload = kwargs
        payload.update({
            "cmd": command,
            "tid": tid,
            "source": self.COMMAND_SOURCE,
        })
        payload_json = json.dumps(payload)
        self.__log.debug(f"> {payload_json}")
        await self.websockets_connection.send(payload_json)
        return transaction_future

    async def _command_wait(self, command, **kwargs):
        fut = await self._command(command, **kwargs)
        ret = await fut
        return ret

    async def login(self):
        login_data = await self._command_wait('login', sessionid=self.session_id)
        return login_data

    async def read(self, awlid, zone=0):
        read_data = await self._command_wait('read',
            awlid=awlid,
            zone=zone,
            rlist=["compressorpower","fanpower","auxpower","looppumppower","totalunitpower","AWLABCType","ModeOfOperation","ActualCompressorSpeed","AirflowCurrentSpeed","AuroraOutputEH1","AuroraOutputEH2","AuroraOutputCC","AuroraOutputCC2","TStatDehumidSetpoint","TStatRelativeHumidity","LeavingAirTemp","TStatRoomTemp","EnteringWaterTemp","AOCEnteringWaterTemp","auroraoutputrv","AWLTStatType","humidity_offset_settings","iz2_humidity_offset_settings","dehumid_humid_sp","iz2_dehumid_humid_sp","lockoutstatus","lastfault","lastlockout","homeautomationalarm1","homeautomationalarm2","iz2_z1_roomtemp","iz2_z1_activesettings","TStatActiveSetpoint","TStatMode","TStatHeatingSetpoint","TStatCoolingSetpoint","iz2_z2_roomtemp","iz2_z2_activesettings","iz2_z3_roomtemp","iz2_z3_activesettings"]
        )
        return read_data

