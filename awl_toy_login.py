#!/usr/bin/env python3

import asyncio
import websockets
import requests
import json

import os
import re


async def login():
    login_uri = "https://symphony.mywaterfurnace.com/account/login"

    # Get sessionid cookie
    with requests.Session() as login_session:
        login_session.cookies.set(
            'legal-acknowledge', 'yes',
            domain='symphony.mywaterfurnace.com',
            path='/'
        )
        login_response = login_session.post(
            login_uri,
            allow_redirects=False, # Just the first response
            data={
                'op': 'login',
                'redirect': '/',
                'emailaddress': os.environ['WATERFURNACE_USER'],
                'password': os.environ['WATERFURNACE_PASSWORD'],
            }
        )
        login_response.raise_for_status()
        session_id = login_response.cookies['sessionid']

        wssuri_response = login_session.get(
            'https://symphony.mywaterfurnace.com/assets/js/awlconfig.js.php',
        )
        wssuri_response.raise_for_status()
        wssuri_javascript = wssuri_response.text
        websocket_uri = re.search(r'wss?://[^"\']+', wssuri_javascript)[0]
        print(websocket_uri)


    # TODO: Get wss URI from .js
    #websocket_uri = "wss://awlclientproxy.mywaterfurnace.com:443"

    login_request = {
        "cmd": "login",
        "tid": 2, # TODO: increment tid
        "source": "consumer dashboard",
        "sessionid": session_id,
    }

    async with websockets.connect(websocket_uri, ssl=True) as ws:
        login_json = json.dumps(login_request)
        await ws.send(login_json)
        print(f"> {login_json}")

        login_response = await ws.recv()
        print(f"< {login_response}")

asyncio.get_event_loop().run_until_complete(login())
