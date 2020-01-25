#!/usr/bin/env python3

import asyncio
import json
import re
import requests
from urllib3.util.url import parse_url
import websockets
from autologging import logged, traced


class AWLLoginError(RuntimeError):
    pass


class AWLConnectionError(RuntimeError):
    pass


@logged
@traced
class AWL:

    LOGIN_URI = 'https://symphony.mywaterfurnace.com/account/login'
    AWLCONFIG_URI = \
        'https://symphony.mywaterfurnace.com/assets/js/awlconfig.js.php'
    COMMAND_SOURCE = 'consumer dashboard'
    AWL_GATEWAY_RLIST = [
        "compressorpower",
        "fanpower",
        "auxpower",
        "looppumppower",
        "totalunitpower",
        "AWLABCType",
        "ModeOfOperation",
        "ActualCompressorSpeed",
        "AirflowCurrentSpeed",
        "AuroraOutputEH1",
        "AuroraOutputEH2",
        "AuroraOutputCC",
        "AuroraOutputCC2",
        "TStatDehumidSetpoint",
        "TStatRelativeHumidity",
        "LeavingAirTemp",
        "TStatRoomTemp",
        "EnteringWaterTemp",
        "AOCEnteringWaterTemp",
        "auroraoutputrv",
        "AWLTStatType",
        "humidity_offset_settings",
        "iz2_humidity_offset_settings",
        "dehumid_humid_sp",
        "iz2_dehumid_humid_sp",
        "lockoutstatus",
        "lastfault",
        "lastlockout",
        "homeautomationalarm1",
        "homeautomationalarm2",
        "iz2_z1_roomtemp",
        "iz2_z1_activesettings",
        "TStatActiveSetpoint",
        "TStatMode",
        "TStatHeatingSetpoint",
        "TStatCoolingSetpoint",
        "iz2_z2_roomtemp",
        "iz2_z2_activesettings",
        "iz2_z3_roomtemp",
        "iz2_z3_activesettings",
    ]

    def __init__(self, username, password):
        self.username = username
        self.password = password

        self.websockets_connection = None
        self._login_data = None
        self.receive_task = None

        self._transaction_lock = asyncio.Lock()
        self._transactions = dict()
        self._transaction_id = 0

    def __del__(self):
        self.http_session.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *excinfo):
        await self.close()

    async def __next_transaction_id(self):
        async with self._transaction_lock:
            self._transaction_id = (self._transaction_id + 1) % 100
            return self._transaction_id

    async def __reset_transaction_id(self):
        async with self._transaction_lock:
            self._transaction_id = 0

    async def __start_transaction(self, tid):
        async with self._transaction_lock:
            transaction_future = asyncio.get_running_loop().create_future()
            self.transactions[tid] = transaction_future
        return transaction_future

    async def __commit_transaction(self, tid, data):
        try:
            async with self._transaction_lock:
                self._transactions.pop(tid).set_result(data)
        except KeyError:
            self.__log.warning(
                f"< Unknown transaction id {tid}: {data!r}"
            )

    def __http_login(self):
        self.http_session = requests.Session()
        self.http_session.cookies.set(
            'legal-acknowledge', 'yes',
            domain=parse_url(self.LOGIN_URI).host,
            path='/'
        )

        try:
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
        except requests.ConnectionError:
            raise AWLLoginError(f"Could not connect to {self.LOGIN_URI}")

        try:
            login_response.raise_for_status()
        except requests.HTTPError:
            raise AWLLoginError(f"Login failed: {login_response.reason}")

    def __http_logout(self):
        try:
            logout_uri = self.LOGIN_URI + '?op=logout'
            logout_response = self.http_session.get(
                logout_uri,
                allow_redirects=False,
                timeout=2.0,
            )
        except requests.ConnectionError:
            raise AWLLoginError(f"Could not connect to {logout_uri}")

        try:
            logout_response.raise_for_status()
        except requests.HTTPError:
            raise AWLLoginError(f"Logout failed: {logout_response.reason}")

    def __get_websockets_uri(self):
        wssuri_response = self.http_session.get(self.AWLCONFIG_URI)
        try:
            wssuri_response.raise_for_status()
        except requests.HTTPError:
            raise AWLLoginError(
                f"Unable to fetch AWL websockets URI: {wssuri_response.reason}"
            )

        websockets_uri_matches = re.search(
            r'wss?://[^"\']+',
            wssuri_response.text
        )
        if websockets_uri_matches is None:
            raise AWLLoginError(
                f"Unable to find websockets URI in {self.AWLCONFIG_URI}"
            )
        return websockets_uri_matches[0]

    async def __websockets_connect(self, websockets_uri):
        try:
            self.websockets_connection = await (
                websockets.connect(websockets_uri)
            )
        except websockets.InvalidHandshake:
            raise AWLLoginError(
                "Unable to connect to AWL websockets URI"
            )
        except websockets.InvalidURI:
            raise AWLLoginError(
                f"Invalid websockets URI: {self.websockets_uri}"
            )
        self.receive_task = asyncio.create_task(self.__websockets_receive())

    async def __websockets_receive(self):
        try:
            async for message in self.websockets_connection:
                self.__log.debug(f"< {message}")
                data = json.loads(message)
                tid = data['tid']
                await self.__commit_transaction(tid, data)
        except websockets.ConnectionClosedError:
            self._login_data = None
            raise

    async def __websockets_login(self):
        # Reset transaction ID whenever logging
        # in again
        await self.__reset_transaction_id()
        self._login_data = await self._command_wait(
            'login',
            sessionid=self.session_id
        )
        return self._login_data

    async def _command(self, command, **kwargs):
        tid = await self.__next_transaction_id()

        payload = kwargs
        payload.update({
            "cmd": command,
            "tid": tid,
            "source": self.COMMAND_SOURCE,
        })
        payload_json = json.dumps(payload)
        self.__log.debug(f"> {payload_json}")
        await self.websockets_connection.send(payload_json)
        transaction_future = await self.__start_transaction(tid)
        return transaction_future

    async def _command_wait(self, command, **kwargs):
        fut = await self._command(command, **kwargs)
        ret = await fut
        return ret

    async def wait_closed(self):
        try:
            await self.receive_task
        except websockets.ConnectionClosedError:
            self.__log.info('websockets connection closed unexpectedly')
            raise AWLConnectionError()

    async def connect(self):
        self.__http_login()
        websockets_uri = self.__get_websockets_uri()

        await self.__websockets_connect(websockets_uri)
        await self.__websockets_login()

    def logout(self):
        return self.__http_logout()

    async def close(self):
        if self.websockets_connection is not None:
            await self.websockets_connection.close()

        try:
            self.__http_logout()
        except (AWLLoginError, IOError):
            self.__log.warning("Logout failed during close()")
            # Ignore any logout errors and just exit
            pass

    @property
    def session_id(self):
        return self.http_session.cookies.get('sessionid', default=None)

    @property
    def login_data(self):
        return self._login_data

    async def read(self, awlid, zone=0):
        read_data = await self._command_wait(
            'read',
            awlid=awlid,
            zone=zone,
            rlist=self.AWL_GATEWAY_RLIST
        )
        return read_data
