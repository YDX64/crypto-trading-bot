#!/usr/bin/env python3
"""Test signal processing"""

import asyncio
import aiohttp
import json

async def test_signal():
    signal = """🚀 BTCUSDT LONG

Leverage: 10x

Entry: 109500 - 110500

Targets:
🎯 111000
🎯 112000
🎯 113000
🎯 115000

Stoploss: 108000"""

    async with aiohttp.ClientSession() as session:
        async with session.post(
            'http://localhost:8080/signal',
            json={'message': signal}
        ) as response:
            result = await response.json()
            print(json.dumps(result, indent=2))
            return result

if __name__ == "__main__":
    result = asyncio.run(test_signal())