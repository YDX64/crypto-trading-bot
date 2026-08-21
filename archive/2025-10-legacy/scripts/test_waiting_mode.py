#!/usr/bin/env python3
"""Test waiting mode feature with SHORT signal"""

import asyncio
import aiohttp
import json

async def test_waiting_mode():
    # Create a SHORT signal that will get opposite AI verdict (BULLISH)
    # This will trigger waiting mode if enabled
    signal = """BTC/USDT SHORT
MARGIN: 10X
ENTRY: <111000-112000>
TARGETS:
1. [110000]
2. [109000]
3. [108000]
4. [106000]
STOPLOSS: [113000]"""

    print("=" * 60)
    print("🧪 WAITING MODE TEST")
    print("=" * 60)
    print(f"📤 Sending SHORT signal (AI will likely say BULLISH)...")
    print(f"Signal: BTC/USDT SHORT @ 111000-112000")
    print()

    async with aiohttp.ClientSession() as session:
        # Send signal
        async with session.post(
            'http://localhost:8080/signal',
            json={'message': signal}
        ) as response:
            result = await response.json()

            print("📥 Response:")
            print(json.dumps(result, indent=2))

            if not result.get('success'):
                print("\n✅ Signal correctly sent to waiting mode (or rejected)")
                print("   This is expected when AI verdict contradicts signal direction")

                # Check waiting signals
                await asyncio.sleep(2)
                print("\n🔍 Checking waiting signals...")
                async with session.get('http://localhost:8080/waiting-mode/active') as resp:
                    if resp.status == 200:
                        waiting = await resp.json()
                        if waiting:
                            print(f"✅ Found {len(waiting)} signals in waiting mode")
                            for w in waiting:
                                print(f"   - {w.get('symbol', 'Unknown')} {w.get('direction', '')} (Score: {w.get('last_score', 0):.1f})")
                        else:
                            print("ℹ️ No signals currently in waiting mode")
            else:
                print("\n⚠️ Signal was executed immediately (trend was aligned)")

            return result

async def check_waiting_mode_status():
    """Check if waiting mode is enabled"""
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8080/config') as response:
            config = await response.json()
            waiting_enabled = config.get('waiting_mode_enabled', False)

            print("\n📊 Configuration:")
            print(f"   Waiting Mode: {'✅ ENABLED' if waiting_enabled else '❌ DISABLED'}")

            if waiting_enabled:
                print(f"   Max Waiting Positions: {config.get('waiting_mode_max_positions', 'N/A')}")
                print(f"   Max Wait Hours: {config.get('waiting_mode_max_hours', 'N/A')}")
                print(f"   Check Interval: {config.get('waiting_mode_check_interval_minutes', 'N/A')} minutes")
                print(f"   Min Conditions Required: {config.get('waiting_mode_min_conditions', 'N/A')}")

            return waiting_enabled

if __name__ == "__main__":
    print("🚀 Testing Waiting Mode Feature")
    print("=" * 60)

    # Check configuration
    waiting_enabled = asyncio.run(check_waiting_mode_status())

    if waiting_enabled:
        print("\n✅ Waiting mode is enabled, proceeding with test...")
        asyncio.run(test_waiting_mode())

        print("\n" + "=" * 60)
        print("📌 Test Summary:")
        print("   - SHORT signal sent successfully")
        print("   - If AI says BULLISH, signal goes to waiting mode")
        print("   - Monitor will check every 5 minutes with indicators")
        print("   - Will execute when technical conditions are met")
        print("=" * 60)
    else:
        print("\n⚠️ Waiting mode is DISABLED in configuration")
        print("   Enable it in .env: WAITING_MODE_ENABLED=true")
        print("   Then restart the server")