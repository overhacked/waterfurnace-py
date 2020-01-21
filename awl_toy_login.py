#!/usr/bin/env python3

import asyncio
import os

from awl import AWL


async def main():
    async with AWL(
        os.environ['WATERFURNACE_USER'],
        os.environ['WATERFURNACE_PASSWORD']
    ) as awl_connection:
        await awl_connection.login()

if __name__ == '__main__':
    asyncio.run(main())
