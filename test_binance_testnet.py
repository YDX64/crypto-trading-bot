#!/usr/bin/env python3
"""
Binance Testnet Test Script
Tests all major functionality with the new enhanced client
"""

import asyncio
import sys
from colorama import init, Fore, Style
from typing import Optional

# Initialize colorama
init(autoreset=True)


async def test_connection():
    """Test basic connection"""
    print(f"{Fore.YELLOW}🔍 Testing Binance Testnet connection...{Style.RESET_ALL}")

    try:
        from src.trading.binance_testnet_client import BinanceFuturesTestnetClient

        client = BinanceFuturesTestnetClient()

        # Test connection
        success = await client.test_connection()

        if success:
            print(f"{Fore.GREEN}✅ Connection successful{Style.RESET_ALL}")

            # Get account balance
            balance = await client.get_balance()
            print(f"{Fore.GREEN}💰 Account balance: {balance:.2f} USDT{Style.RESET_ALL}")

            # Get BTC price
            btc_price = await client.get_ticker_price("BTCUSDT")
            print(f"{Fore.GREEN}📊 BTC/USDT price: ${btc_price:,.2f}{Style.RESET_ALL}")

            await client.close()
            return True
        else:
            print(f"{Fore.RED}❌ Connection failed{Style.RESET_ALL}")
            await client.close()
            return False

    except Exception as e:
        print(f"{Fore.RED}❌ Error: {e}{Style.RESET_ALL}")
        return False


async def test_websocket():
    """Test WebSocket streaming"""
    print(f"\n{Fore.YELLOW}📡 Testing WebSocket connection...{Style.RESET_ALL}")

    try:
        from src.trading.binance_testnet_client import BinanceFuturesTestnetClient

        client = BinanceFuturesTestnetClient()

        # Create listen key
        listen_key = await client.create_listen_key()
        print(f"{Fore.GREEN}✅ Listen key created: {listen_key[:10]}...{Style.RESET_ALL}")

        # Test ticker subscription
        print(f"{Fore.YELLOW}📈 Subscribing to BTCUSDT ticker (5 seconds)...{Style.RESET_ALL}")

        ticker_count = 0

        async def ticker_callback(data):
            nonlocal ticker_count
            ticker_count += 1
            price = data.get("c", "N/A")
            print(f"\r{Fore.CYAN}BTC Price: ${price}{Style.RESET_ALL}", end="")

        # Subscribe to ticker with timeout
        ticker_task = asyncio.create_task(
            client.subscribe_ticker("BTCUSDT", ticker_callback)
        )

        await asyncio.sleep(5)
        ticker_task.cancel()

        print(f"\n{Fore.GREEN}✅ Received {ticker_count} ticker updates{Style.RESET_ALL}")

        await client.close()
        return True

    except Exception as e:
        print(f"{Fore.RED}❌ WebSocket error: {e}{Style.RESET_ALL}")
        return False


async def test_monitoring():
    """Test real-time monitoring"""
    print(f"\n{Fore.YELLOW}📊 Testing real-time monitoring...{Style.RESET_ALL}")

    try:
        from src.trading.binance_testnet_client import BinanceFuturesTestnetClient
        from src.monitoring.real_time_monitor import RealTimeMonitor

        client = BinanceFuturesTestnetClient()
        monitor = RealTimeMonitor(client)

        # Register callbacks
        alerts_received = []

        async def alert_callback(alert):
            alerts_received.append(alert)
            print(f"{Fore.YELLOW}⚠️ Alert: {alert['message']}{Style.RESET_ALL}")

        monitor.on_alert(alert_callback)

        # Start monitoring
        await monitor.start()
        print(f"{Fore.GREEN}✅ Monitor started{Style.RESET_ALL}")

        # Wait for initial snapshot
        await asyncio.sleep(2)

        # Get summary
        summary = monitor.get_summary()
        print(f"{Fore.CYAN}Account Balance: {summary['account']['total_balance']:.2f} USDT{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Open Positions: {summary['positions']['count']}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Total PNL: {summary['positions']['total_pnl']:.2f} USDT{Style.RESET_ALL}")

        # Run for 5 seconds
        await asyncio.sleep(5)

        # Stop monitoring
        await monitor.stop()
        await client.close()

        print(f"{Fore.GREEN}✅ Monitor stopped successfully{Style.RESET_ALL}")
        print(f"{Fore.GREEN}📬 Alerts received: {len(alerts_received)}{Style.RESET_ALL}")

        return True

    except Exception as e:
        print(f"{Fore.RED}❌ Monitoring error: {e}{Style.RESET_ALL}")
        return False


async def test_order_workflow():
    """Test complete order workflow (simulation only)"""
    print(f"\n{Fore.YELLOW}🔄 Testing order workflow (simulation)...{Style.RESET_ALL}")

    try:
        from src.trading.binance_testnet_client import (
            BinanceFuturesTestnetClient,
            OrderSide,
            MarginType
        )

        client = BinanceFuturesTestnetClient()

        # Get current BTC price
        btc_price = await client.get_ticker_price("BTCUSDT")
        print(f"{Fore.CYAN}Current BTC price: ${btc_price:,.2f}{Style.RESET_ALL}")

        # Get account balance
        balance = await client.get_balance()
        print(f"{Fore.CYAN}Account balance: {balance:.2f} USDT{Style.RESET_ALL}")

        # Calculate position size (0.001 BTC for test)
        position_size = 0.001
        position_value = position_size * btc_price

        print(f"\n{Fore.YELLOW}📝 Order Details:{Style.RESET_ALL}")
        print(f"  Symbol: BTCUSDT")
        print(f"  Side: BUY (LONG)")
        print(f"  Quantity: {position_size} BTC")
        print(f"  Value: ${position_value:.2f}")
        print(f"  Leverage: 1x")
        print(f"  Margin Type: ISOLATED")
        print(f"  Stop Loss: ${btc_price * 0.98:,.2f} (-2%)")
        print(f"  Take Profit: ${btc_price * 1.02:,.2f} (+2%)")

        # Ask for confirmation
        print(f"\n{Fore.YELLOW}⚠️ This will place a REAL order on TESTNET.{Style.RESET_ALL}")
        response = input(f"Continue? (y/N): ")

        if response.lower() == 'y':
            print(f"{Fore.YELLOW}Executing order workflow...{Style.RESET_ALL}")

            result = await client.open_position_workflow(
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                quantity=position_size,
                leverage=1,
                margin_type=MarginType.ISOLATED,
                stop_loss_price=btc_price * 0.98,
                take_profit_prices=[btc_price * 1.02]
            )

            if result["success"]:
                print(f"{Fore.GREEN}✅ Position opened successfully!{Style.RESET_ALL}")
                print(f"{Fore.GREEN}📊 Main order: #{result['position'].get('orderId')}{Style.RESET_ALL}")
                print(f"{Fore.GREEN}📊 Additional orders: {len(result['orders'])}{Style.RESET_ALL}")

                # Check position
                await asyncio.sleep(2)
                positions = await client.get_position_risk("BTCUSDT")
                if positions:
                    pos = positions[0]
                    print(f"\n{Fore.CYAN}Position Details:{Style.RESET_ALL}")
                    print(f"  Entry Price: ${float(pos['entryPrice']):,.2f}")
                    print(f"  Quantity: {float(pos['positionAmt'])}")
                    print(f"  Unrealized PNL: ${float(pos['unRealizedProfit']):,.2f}")
            else:
                print(f"{Fore.RED}❌ Failed to open position: {result.get('error')}{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}Order workflow test skipped{Style.RESET_ALL}")

        await client.close()
        return True

    except Exception as e:
        print(f"{Fore.RED}❌ Order workflow error: {e}{Style.RESET_ALL}")
        return False


async def main():
    """Main test runner"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"🚀 BINANCE TESTNET COMPREHENSIVE TEST")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    results = []

    # Run tests
    print(f"{Fore.CYAN}[1/4] Connection Test{Style.RESET_ALL}")
    results.append(("Connection", await test_connection()))

    print(f"\n{Fore.CYAN}[2/4] WebSocket Test{Style.RESET_ALL}")
    results.append(("WebSocket", await test_websocket()))

    print(f"\n{Fore.CYAN}[3/4] Monitoring Test{Style.RESET_ALL}")
    results.append(("Monitoring", await test_monitoring()))

    print(f"\n{Fore.CYAN}[4/4] Order Workflow Test{Style.RESET_ALL}")
    results.append(("Order Workflow", await test_order_workflow()))

    # Summary
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"📊 TEST RESULTS SUMMARY")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    success_count = 0
    for name, success in results:
        if success:
            print(f"{Fore.GREEN}✅ {name}: PASSED{Style.RESET_ALL}")
            success_count += 1
        else:
            print(f"{Fore.RED}❌ {name}: FAILED{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")

    if success_count == len(results):
        print(f"{Fore.GREEN}🎉 ALL TESTS PASSED! System is ready.{Style.RESET_ALL}")
        return 0
    elif success_count >= 2:
        print(f"{Fore.YELLOW}⚠️ Some tests failed. System partially operational.{Style.RESET_ALL}")
        return 1
    else:
        print(f"{Fore.RED}❌ Critical failures. Please check configuration.{Style.RESET_ALL}")
        return 2


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Test interrupted{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}Test error: {e}{Style.RESET_ALL}")
        sys.exit(2)