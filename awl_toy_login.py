#!/usr/bin/env python3

import asyncio
import logging
import os
from pprint import pprint
import sys

from awl import AWL

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout,
    format="%(levelname)s:%(name)s:%(funcName)s:%(message)s")

async def main():
    async with AWL(
        os.environ['WATERFURNACE_USER'],
        os.environ['WATERFURNACE_PASSWORD']
    ) as awl_connection:
        login_data = await awl_connection.login()
        pprint(login_data)
        for unit in login_data['locations'][0]['gateways']:
            pprint(await awl_connection.read(unit['gwid']))

if __name__ == '__main__':
    asyncio.run(main())
