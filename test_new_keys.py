#!/usr/bin/env python3
"""Test new Binance API keys"""

import asyncio
from src.trading.binance_testnet_client import BinanceFuturesTestnetClient

async def test_new_keys():
    print('🔍 Testing new API keys...')
    client = BinanceFuturesTestnetClient()

    # Test connection
    success = await client.test_connection()
    if success:
        print('✅ Connection successful!')

        # Get balance
        balance = await client.get_balance()
        print(f'💰 Balance: {balance:.2f} USDT')

        # Get BTC price
        btc_price = await client.get_ticker_price('BTCUSDT')
        print(f'📊 BTC Price: ${btc_price:,.2f}')

        # Get positions
        positions = await client.get_position_risk()
        open_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
        print(f'📈 Open Positions: {len(open_positions)}')

        # Try to create listen key for WebSocket
        try:
            listen_key = await client.create_listen_key()
            if listen_key:
                print(f'🔑 Listen Key created: {listen_key[:10]}...')
                print('✅ WebSocket ready!')
            else:
                print('⚠️ Listen Key creation failed')
        except Exception as e:
            error_msg = str(e)
            if "API-key format invalid" in error_msg:
                print('⚠️ API Key format issue for WebSocket')
            else:
                print(f'⚠️ Listen Key error: {error_msg[:100]}')
    else:
        print('❌ Connection failed!')

    await client.close()
    return success

if __name__ == "__main__":
    result = asyncio.run(test_new_keys())
    print("\n" + "="*50)
    if result:
        print("✅ API KEYS ARE WORKING!")
        print("Dashboard: http://localhost:8000")
    else:
        print("❌ API KEY ISSUES DETECTED")