#!/usr/bin/env python3

import asyncio
import copy
import json
import logging
import re
import requests
from requests.packages.urllib3.util.url import parse_url
from typing import Any, Dict, Optional, Final
import websockets
from autologging import logged, traced


# Default 30 second timeout
AWL_DEFAULT_TRANSACTION_TIMEOUT: Final = 30


class AWLLoginError(RuntimeError):
    pass


class AWLConnectionError(RuntimeError):
    pass


class AWLTransactionError(RuntimeError):
    pass


class AWLTransactionTimeout(AWLTransactionError):
    pass


@logged
@traced
class AWL:
    __log: logging.Logger

    # Taken from setTimeout(1500000, ...) in Symphony JavaScript
    SESSION_TIMEOUT: Final = 1500

    LOGIN_URI: Final = 'https://symphony.mywaterfurnace.com/account/login'
    AWLCONFIG_URI: Final = \
        'https://symphony.mywaterfurnace.com/assets/js/awlconfig.js.php'
    COMMAND_SOURCE: Final = 'consumer dashboard'
    AWL_GATEWAY_RLIST: Final = [
        "ActualCompressorSpeed",
        "AirflowCurrentSpeed",
        "AOCEnteringWaterTemp",
        "AuroraOutputCC",
        "AuroraOutputCC2",
        "AuroraOutputEH1",
        "AuroraOutputEH2",
        "auroraoutputrv",
        "auxpower",
        "AWLABCType",
        "AWLTStatType",
        "compressorpower",
        "dehumid_humid_sp",
        "EnteringWaterTemp",
        "fanpower",
        "homeautomationalarm1",
        "homeautomationalarm2",
        "humidity_offset_settings",
        "iz2_dehumid_humid_sp",
        "iz2_humidity_offset_settings",
        "lastfault",
        "lastlockout",
        "LeavingAirTemp",
        "lockoutstatus",
        "looppumppower",
        "ModeOfOperation",
        "totalunitpower",
        "TStatActiveSetpoint",
        "TStatCoolingSetpoint",
        "TStatDehumidSetpoint",
        "TStatHeatingSetpoint",
        "TStatMode",
        "TStatRelativeHumidity",
        "TStatRoomTemp",
    ]

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password

        self.websockets_connection: Optional[websockets.client.WebSocketClientProtocol] = None
        self._login_data: Optional[dict] = None
        self._websockets_task: Optional[asyncio.Task] = None

        self._transaction_lock: Final[asyncio.Lock] = asyncio.Lock()
        self._transactions: Final[Dict[int, asyncio.Future]] = dict()
        self._transaction_id: int = 0

    def __del__(self):
        self.http_session.close()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *excinfo):
        await self.close()

    async def __next_transaction_id(self) -> int:
        async with self._transaction_lock:
            # Reset to 1 when next tid would be larger
            # than an 8-bit integer
            initial_transaction_id = self._transaction_id or 1
            while True:
                self._transaction_id = (self._transaction_id + 1) % 256 or 1
                if (
                    self._transaction_id not in self._transactions
                    or self._transactions[self._transaction_id].done()
                   ):
                    break
                elif self._transaction_id == initial_transaction_id:
                    # This would be true after reset_transaction_id, but
                    # self._transactions will be empty, so the previous
                    # condition will never fall through
                    raise AWLTransactionError(
                        'Maximum 255 transactions in progress'
                    )
            return self._transaction_id

    async def __reset_transaction_id(self):
        async with self._transaction_lock:
            # Drain the transactions dict and
            # cancel any pending futures
            while len(self._transactions) > 0:
                tid, fut = self._transactions.popitem()
                if fut.cancel():
                    self.__log.debug(f"Cancelled transaction tid={tid}")
            # Reset the transaction id
            self._transaction_id = 0
            # Clear the login_data
            self._login_data = None

    async def __start_transaction(self,
                                  tid: int,
                                  timeout: int
                                  ) -> asyncio.Task:
        async with self._transaction_lock:
            transaction_future = asyncio.get_running_loop().create_future()
            self._transactions[tid] = transaction_future

        # Cancel the future if the timeout expires
        async def __await_transaction_result():
            try:
                return await asyncio.wait_for(transaction_future, timeout)
            except asyncio.TimeoutError:
                raise AWLTransactionTimeout('Transaction timed out')
            except asyncio.CancelledError:
                raise AWLTransactionError('Transaction cancelled')

        return asyncio.create_task(__await_transaction_result())

    async def __commit_transaction(self, tid: int, data: Any):
        try:
            async with self._transaction_lock:
                self._transactions.pop(tid).set_result(data)
        except KeyError:
            self.__log.warning(
                f"< Unknown transaction id {tid}: {data!r}"
            )

    async def __abort_transaction(self, tid: int, err: Optional[str] = None):
        try:
            async with self._transaction_lock:
                self._transactions.pop(tid).set_exception(
                    AWLTransactionError(err)
                )
        except KeyError:
            self.__log.debug(
                f"Tried to abort non-existent transaction (tid={tid})"
            )
            pass

    async def __http_login(self):
        return await asyncio.get_running_loop().run_in_executor(
            None,
            self.__http_login_sync
        )

    def __http_login_sync(self):
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
            raise AWLConnectionError(f"Could not connect to {self.LOGIN_URI}")

        try:
            login_response.raise_for_status()
        except requests.HTTPError:
            raise AWLLoginError(f"Login failed: {login_response.reason}")

        if self.session_id is None:
            raise AWLLoginError("Login failed; could not establish session. "
                                "Check credentials.")

    async def __http_logout(self):
        return await asyncio.get_running_loop().run_in_executor(
            None,
            self.__http_logout_sync
        )

    def __http_logout_sync(self):
        if not self.session_id:
            # Idempotent logout if not logged in
            return

        try:
            logout_uri = self.LOGIN_URI + '?op=logout'
            logout_response = self.http_session.get(
                logout_uri,
                allow_redirects=False,
                timeout=2.0,
            )
        except requests.ConnectionError:
            raise AWLConnectionError(f"Could not connect to {logout_uri}")

        try:
            logout_response.raise_for_status()
        except requests.HTTPError:
            raise AWLLoginError(f"Logout failed: {logout_response.reason}")

    async def __get_websockets_uri(self):
        return await asyncio.get_running_loop().run_in_executor(
            None,
            self.__get_websockets_uri_sync
        )

    def __get_websockets_uri_sync(self) -> str:
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

    async def __websockets_connect(self, websockets_uri: str):
        try:
            self.websockets_connection = await (
                websockets.connect(websockets_uri)
            )
            receive_task = asyncio.create_task(
                self.__websockets_receive()
            )
            await self.__websockets_login()
            return receive_task
        except websockets.InvalidHandshake:
            raise AWLConnectionError(
                "Unable to connect to AWL websockets URI"
            )
        except websockets.InvalidURI:
            raise AWLLoginError(
                f"Invalid websockets URI: {websockets_uri}"
            )
        except websockets.ConnectionClosed:
            raise AWLLoginError(
                f"Websockets connection was closed while logging in"
            )

    async def __websockets_close(self):
        if self.websockets_connection is not None:
            await self.websockets_connection.close()

    async def __renew_session(self,
                              websockets_uri: str
                              ) -> (asyncio.Task, asyncio.Task):
        async def __session_timeout():
            await asyncio.sleep(self.SESSION_TIMEOUT)
            self.__log.info("Reconnecting due to session timeout")

        await self.__websockets_close()
        await self.__http_logout()
        await self.__http_login()
        receive_task = await self.__websockets_connect(websockets_uri)
        timeout_task = asyncio.create_task(
            __session_timeout()
        )

        return (receive_task, timeout_task,)

    async def __websockets_handler(self, websockets_uri: str) -> None:
        receive_task, timeout_task = await self.__renew_session(websockets_uri)
        pending = {receive_task, timeout_task}
        while self.websockets_connection.open:
            self.__log.debug('Awaiting timeout or receive loop exit')
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED
            )
            if timeout_task in done:
                # Reconnect session
                self.__log.debug('Timeout, renewing session')
                receive_task.cancel()
                try:
                    await asyncio.wait_for(receive_task, timeout=1.0)
                except asyncio.CancelledError:
                    self.__log.debug('Cancelled receive task')
                except asyncio.TimeoutError:
                    self.__log.debug('receive_task.cancel() timed out')
                    raise AWLConnectionError('Could not cancel receive task '
                                             'during session renewal')

                receive_task, timeout_task = await (
                    self.__renew_session(websockets_uri)
                )
                pending = {receive_task, timeout_task}
            if receive_task in done:
                self.__log.debug('Receive task finished')
                if receive_task.exception():
                    self.__log.debug('Receive task returned exception')
                    raise receive_task.exception()
                return

    async def __websockets_receive(self) -> None:
        async for message in self.websockets_connection:
            self.__log.debug(f"< {message}")
            try:
                data = json.loads(message)
            except ValueError:
                self.__log.error(f"JSON decoding error on message: {message}")
                return

            try:
                tid = data['tid']
            except KeyError:
                self.__log.error(f"Message came in without tid: {message}")
                return

            if data.get('err'):
                await self.__abort_transaction(tid, data['err'])
                return

            await self.__commit_transaction(tid, data)

    async def __websockets_login(self):
        # Reset transaction ID whenever logging
        # in again
        await self.__reset_transaction_id()
        self._login_data = await self._command_wait(
            'login',
            sessionid=self.session_id
        )
        return self._login_data

    async def _command(self, command: str,
                       transaction_timeout: int = AWL_DEFAULT_TRANSACTION_TIMEOUT,
                       **kwargs) -> asyncio.Future:
        if (
            (command != 'login' and self._login_data is None)
            or self.websockets_connection is None
            or not self.websockets_connection.open
           ):
            raise AWLConnectionError(f"Call {__name__}.connect() before making requests")

        tid = await self.__next_transaction_id()

        payload = kwargs
        payload.update({
            "cmd": command,
            "tid": tid,
            "source": self.COMMAND_SOURCE,
        })
        payload_json = json.dumps(payload)
        self.__log.debug(f"> {payload_json}")
        # Start transaction before call to send() in case
        # receive comes back really quickly
        transaction_future = await (
            self.__start_transaction(tid, transaction_timeout)
        )
        try:
            await self.websockets_connection.send(payload_json)
        except websockets.ConnectionClosed:
            await self.__reset_transaction_id()
            raise AWLConnectionError(f"Websockets connection closed")
        return transaction_future

    async def _command_wait(self, command: str, **kwargs):
        fut = await self._command(command, **kwargs)
        try:
            ret = await fut
        except AWLTransactionError as e:
            self.__log.error(f"Transaction error: {e!s}")
            raise

        return ret

    async def wait_closed(self):
        try:
            await self._websockets_task
        except websockets.ConnectionClosedOK:
            self.__log.info(f"websockets connection closed: "
                            f"{self._websockets_task.exception()!s}")
            return
        except websockets.ConnectionClosedError:
            self.__log.error('websockets connection closed unexpectedly')
            raise AWLConnectionError() from self._websockets_task.exception()

    async def connect(self):
        await self.__http_login()
        websockets_uri = await self.__get_websockets_uri()

        self._websockets_task = asyncio.create_task(
            self.__websockets_handler(websockets_uri)
        )

    async def close(self):
        await self.__websockets_close()

        try:
            await self.__http_logout()
        except Exception:
            self.__log.warning("Logout failed while closing AWL session")
            # Ignore any logout errors and just exit
            pass

    @property
    def session_id(self) -> Optional[str]:
        return self.http_session.cookies.get('sessionid', default=None)

    @property
    def login_data(self):
        return self._login_data

    def get_gwid_param(self, gwid: str, param: str) -> Any:
        if self._login_data is None:
            return

        for location in self._login_data.get('locations', list()):
            for gateway in location.get('gateways', list()):
                if gateway.get('gwid') == gwid:
                    return gateway.get(param)

    async def read(self, awlid: str, zone: int = 0,
                   timeout: int = AWL_DEFAULT_TRANSACTION_TIMEOUT) -> Any:
        read_rlist = copy.deepcopy(self.AWL_GATEWAY_RLIST)

        max_zones = self.get_gwid_param(awlid, 'iz2_max_zones')
        if max_zones:
            for zoneid in range(1, max_zones + 1):
                read_rlist.extend([
                    f"iz2_z{zoneid}_roomtemp",
                    f"iz2_z{zoneid}_activesettings"
                ])
        read_data = await self._command_wait(
            'read',
            awlid=awlid,
            zone=zone,
            rlist=read_rlist
        )
        return read_data
