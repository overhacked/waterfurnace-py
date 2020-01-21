#!/usr/bin/env python3

import asyncio
import json
import re
import requests
import websockets


class AWL:

    LOGIN_URI = "https://symphony.mywaterfurnace.com/account/login"
    AWLCONFIG_URI = 'https://symphony.mywaterfurnace.com/assets/js/awlconfig.js.php'
    COMMAND_SOURCE = 'consumer dashboard'

    def __init__(self, username, password):
        self.username = username
        self.password = password

        self.http_session = requests.Session()
        self.http_session.cookies.set(
            'legal-acknowledge', 'yes',
            domain='symphony.mywaterfurnace.com',
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
        self.transaction_id = None
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
            self.transaction_id = 1

    async def close(self):
        if self.websockets_connection is not None:
            await self.websockets_connection.close()
            async with self.transaction_lock:
                self.transaction_id = None

    @property
    def session_id(self):
        return self.http_session.cookies.get('sessionid', default=None)

    async def _command(self, command, **kwargs):
        payload = kwargs
        async with self.transaction_lock:
            payload.update({
                "cmd": command,
                "tid": self.transaction_id,  # TODO: increment tid
                "source": self.COMMAND_SOURCE,
                "sessionid": self.session_id,
            })
            self.transactions[self.transaction_id] = command
            self.transaction_id += 1
        payload_json = json.dumps(payload)
        print(f"> {payload_json}")
        await self.websockets_connection.send(payload_json)

    async def login(self):
        await self._command('login')

        # TODO: move recv to transactional event loop
        login_response = await self.websockets_connection.recv()
        print(f"< {login_response}")

