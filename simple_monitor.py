#!/usr/bin/env python3
"""Simple monitoring script for waiting mode - no extra libraries needed"""

import asyncio
import aiohttp
import json
from datetime import datetime

async def monitor():
    """Simple monitoring function"""
    print("="*60)
    print("🤖 TRADING BOT - WAITING MODE MONITOR")
    print("="*60)
    print()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Get waiting signals
                async with session.get('http://localhost:8080/waiting-mode/active') as response:
                    if response.status == 200:
                        waiting_signals = await response.json()

                        print(f"⏰ {datetime.now().strftime('%H:%M:%S')}")
                        print("-"*60)

                        if waiting_signals:
                            print(f"📊 ACTIVE WAITING SIGNALS: {len(waiting_signals)}")
                            print()

                            for signal in waiting_signals:
                                print(f"  Symbol: {signal.get('symbol', 'Unknown')}")
                                print(f"  Direction: {signal.get('direction', '')}")
                                print(f"  Entry Range: {signal.get('original_entry_min', 0):.2f} - {signal.get('original_entry_max', 0):.2f}")
                                print(f"  Current Price: {signal.get('current_price', 0):.2f}")
                                print(f"  AI Verdict: {signal.get('ai_verdict', 'N/A')}")
                                print(f"  Score: {signal.get('last_score', 0):.1f}/100")
                                print(f"  Conditions Met: {signal.get('conditions_met_count', 0)}")
                                print(f"  Total Checks: {signal.get('total_checks', 0)}")
                                print(f"  Wait Time: {signal.get('wait_time_hours', 0):.1f} hours")
                                print()
                        else:
                            print("  ℹ️ No signals in waiting mode")
                            print()

                # Get config
                async with session.get('http://localhost:8080/config') as response:
                    if response.status == 200:
                        config = await response.json()
                        print("⚙️ WAITING MODE CONFIG:")
                        print(f"  Enabled: {'✅ YES' if config.get('waiting_mode_enabled') else '❌ NO'}")
                        print(f"  Max Positions: {config.get('waiting_mode_max_positions')}")
                        print(f"  Check Interval: {config.get('waiting_mode_check_interval_minutes')} minutes")
                        print(f"  Min Conditions: {config.get('waiting_mode_min_conditions')}")
                        print()

                print("="*60)
                print("Refreshing in 10 seconds... (Press Ctrl+C to stop)")
                print()

            except Exception as e:
                print(f"Error: {e}")

            await asyncio.sleep(10)

if __name__ == "__main__":
    print("Starting monitor... Press Ctrl+C to stop\n")
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("\nMonitor stopped!")