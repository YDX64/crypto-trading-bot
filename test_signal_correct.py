#!/usr/bin/env python3
"""Test signal with correct format"""

import asyncio
import aiohttp
import json

async def test_signal():
    # Correct format that the parser expects
    signal = """BTC/USDT LONG
MARGIN: 10X
ENTRY: <109500-110500>
TARGETS:
1. [111000]
2. [112000]
3. [113000]
4. [115000]
STOPLOSS: [108000]"""

    async with aiohttp.ClientSession() as session:
        async with session.post(
            'http://localhost:8080/signal',
            json={'message': signal}
        ) as response:
            result = await response.json()
            print(json.dumps(result, indent=2))
            return result

if __name__ == "__main__":
    print("📤 Sending properly formatted signal...")
    result = asyncio.run(test_signal())
    if result.get('success'):
        print("✅ Signal processed successfully!")
    else:
        print("❌ Signal failed:", result.get('message'))