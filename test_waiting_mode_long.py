#!/usr/bin/env python3
"""Test waiting mode feature with LONG signal that should trigger waiting mode"""

import asyncio
import aiohttp
import json

async def test_waiting_mode():
    # Create a LONG signal when AI will likely say BEARISH (market is down)
    # This should trigger waiting mode if enabled
    signal = """BTC/USDT LONG
MARGIN: 10X
ENTRY: <105000-106000>
TARGETS:
1. [107000]
2. [108000]
3. [109000]
4. [111000]
STOPLOSS: [103000]"""

    print("=" * 60)
    print("🧪 WAITING MODE TEST - LONG SIGNAL")
    print("=" * 60)
    print(f"📤 Sending LONG signal (AI will likely say BEARISH)...")
    print(f"Signal: BTC/USDT LONG @ 105000-106000")
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
                            print(f"✅ Found {len(waiting)} signals in waiting mode:")
                            for w in waiting:
                                print(f"\n   📊 {w.get('symbol', 'Unknown')} {w.get('direction', '')}")
                                print(f"      - Current Price: {w.get('current_price', 'N/A')}")
                                print(f"      - AI Verdict: {w.get('ai_verdict', 'N/A')}")
                                print(f"      - Score: {w.get('last_score', 0):.1f}/100")
                                print(f"      - Conditions Met: {w.get('conditions_met_count', 0)}")
                                print(f"      - Total Checks: {w.get('total_checks', 0)}")
                                print(f"      - Wait Time: {w.get('wait_time_hours', 0):.1f} hours")
                        else:
                            print("ℹ️ No signals currently in waiting mode")
                            print("   This could mean waiting mode is disabled or signal was rejected")
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
                print(f"   Price Improvement Target: {config.get('waiting_mode_price_improvement', 'N/A')}%")

            return waiting_enabled

async def check_waiting_history():
    """Check waiting signal history"""
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8080/waiting-mode/history') as response:
            if response.status == 200:
                history = await response.json()
                if history:
                    print("\n📜 Recent Waiting Signal History:")
                    for h in history[:5]:  # Show last 5
                        status = h.get('status', 'UNKNOWN')
                        symbol = h.get('symbol', 'Unknown')
                        direction = h.get('direction', '')
                        print(f"   - {symbol} {direction}: {status}")
                        if h.get('executed_price'):
                            print(f"     Executed at: {h['executed_price']}")

if __name__ == "__main__":
    print("🚀 Testing Waiting Mode Feature with LONG Signal")
    print("=" * 60)

    # Check configuration
    waiting_enabled = asyncio.run(check_waiting_mode_status())

    if waiting_enabled:
        print("\n✅ Waiting mode is enabled, proceeding with test...")

        # Run the test
        asyncio.run(test_waiting_mode())

        # Check history
        asyncio.run(check_waiting_history())

        print("\n" + "=" * 60)
        print("📌 Test Summary:")
        print("   - LONG signal sent successfully")
        print("   - If AI says BEARISH, signal goes to waiting mode")
        print("   - Monitor will check every 5 minutes with indicators")
        print("   - Will execute when technical conditions are met")
        print("   - Check /waiting-mode/active endpoint to monitor")
        print("=" * 60)
    else:
        print("\n⚠️ Waiting mode is DISABLED in configuration")
        print("   Enable it in .env: WAITING_MODE_ENABLED=true")
        print("   Then restart the server")