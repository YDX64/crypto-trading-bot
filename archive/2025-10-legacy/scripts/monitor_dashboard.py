#!/usr/bin/env python3
"""
Real-time monitoring dashboard for Trading Bot Waiting Mode
Shows active signals, system status, and historical data
"""

import asyncio
import aiohttp
import json
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.columns import Columns
import sys

console = Console()

async def fetch_data(session, endpoint):
    """Fetch data from API endpoint"""
    try:
        async with session.get(f'http://localhost:8080{endpoint}') as response:
            if response.status == 200:
                return await response.json()
    except:
        pass
    return None

async def create_dashboard():
    """Create and update dashboard"""
    async with aiohttp.ClientSession() as session:
        while True:
            # Fetch all data
            config = await fetch_data(session, '/config')
            status = await fetch_data(session, '/api/status')
            active_waiting = await fetch_data(session, '/waiting-mode/active')
            history = await fetch_data(session, '/waiting-mode/history')
            positions = await fetch_data(session, '/positions')

            # Clear screen
            console.clear()

            # Header
            console.print(Panel.fit(
                "[bold cyan]🤖 TRADING BOT MONITORING DASHBOARD[/bold cyan]\n"
                f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
                border_style="cyan"
            ))

            # System Status
            if status:
                status_table = Table(title="System Status", show_header=False, box=None)
                status_table.add_column("Field", style="cyan")
                status_table.add_column("Value", style="green")

                status_table.add_row("Bot Status", "🟢 RUNNING" if status.get('bot_active') else "🔴 STOPPED")
                status_table.add_row("Orchestrator", "🟢 ACTIVE" if status.get('orchestrator_active') else "🔴 INACTIVE")

                if status.get('account'):
                    acc = status['account']
                    status_table.add_row("Balance", f"${acc.get('balance', 0):,.2f}")
                    status_table.add_row("BTC Price", f"${acc.get('btc_price', 0):,.2f}")
                    status_table.add_row("Open Positions", str(acc.get('open_positions', 0)))

                console.print(status_table)

            # Waiting Mode Configuration
            if config:
                if config.get('waiting_mode_enabled'):
                    config_table = Table(title="Waiting Mode Configuration", show_header=False, box=None)
                    config_table.add_column("Setting", style="cyan")
                    config_table.add_column("Value", style="yellow")

                    config_table.add_row("Status", "✅ ENABLED")
                    config_table.add_row("Max Positions", str(config.get('waiting_mode_max_positions', 'N/A')))
                    config_table.add_row("Max Hours", str(config.get('waiting_mode_max_hours', 'N/A')))
                    config_table.add_row("Check Interval", f"{config.get('waiting_mode_check_interval_minutes', 'N/A')} min")
                    config_table.add_row("Min Conditions", str(config.get('waiting_mode_min_conditions', 'N/A')))
                    config_table.add_row("Price Target", f"{config.get('waiting_mode_price_improvement', 'N/A')}%")

                    console.print(config_table)
                else:
                    console.print("[red]⚠️ Waiting Mode is DISABLED[/red]")

            # Active Waiting Signals
            if active_waiting:
                waiting_table = Table(title=f"🕐 Active Waiting Signals ({len(active_waiting)})", box="rounded")
                waiting_table.add_column("Symbol", style="cyan")
                waiting_table.add_column("Direction", style="magenta")
                waiting_table.add_column("Entry Range", style="yellow")
                waiting_table.add_column("Current Price", style="green")
                waiting_table.add_column("AI Verdict", style="red")
                waiting_table.add_column("Score", style="blue")
                waiting_table.add_column("Wait Time", style="dim")

                for signal in active_waiting:
                    symbol = signal.get('symbol', 'Unknown')
                    direction = signal.get('direction', '')
                    entry_range = f"{signal.get('original_entry_min', 0):.2f}-{signal.get('original_entry_max', 0):.2f}"
                    current = f"{signal.get('current_price', 0):.2f}"
                    ai = signal.get('ai_verdict', 'N/A')
                    score = f"{signal.get('last_score', 0):.1f}/100"
                    wait_hours = signal.get('wait_time_hours', 0)
                    wait_time = f"{wait_hours:.1f}h"

                    # Color code based on direction vs AI verdict
                    dir_color = "green" if direction == "LONG" else "red"
                    ai_color = "green" if ai == "BULLISH" else "red"

                    waiting_table.add_row(
                        symbol,
                        f"[{dir_color}]{direction}[/{dir_color}]",
                        entry_range,
                        current,
                        f"[{ai_color}]{ai}[/{ai_color}]",
                        score,
                        wait_time
                    )

                console.print(waiting_table)
            else:
                console.print("[dim]No active waiting signals[/dim]")

            # Open Positions
            if positions and positions.get('positions'):
                pos_table = Table(title=f"📈 Open Positions ({positions.get('count', 0)})", box="rounded")
                pos_table.add_column("Symbol", style="cyan")
                pos_table.add_column("Side", style="magenta")
                pos_table.add_column("Entry", style="yellow")
                pos_table.add_column("Current", style="green")
                pos_table.add_column("PNL", style="red")
                pos_table.add_column("Status", style="blue")

                for pos in positions['positions']:
                    pnl = pos.get('pnl_percentage', 0)
                    pnl_color = "green" if pnl >= 0 else "red"

                    pos_table.add_row(
                        pos.get('symbol', 'Unknown'),
                        pos.get('side', ''),
                        f"{pos.get('entry_price', 0):.2f}",
                        f"{pos.get('current_price', 0):.2f}",
                        f"[{pnl_color}]{pnl:.2f}%[/{pnl_color}]",
                        pos.get('status', '')
                    )

                console.print(pos_table)

            # Recent History
            if history and len(history) > 0:
                hist_table = Table(title="📜 Recent Waiting Signal History (Last 5)", box="simple")
                hist_table.add_column("Symbol", style="cyan")
                hist_table.add_column("Direction", style="magenta")
                hist_table.add_column("Status", style="yellow")
                hist_table.add_column("Final Score", style="blue")
                hist_table.add_column("Total Checks", style="dim")

                for h in history[:5]:
                    status_val = h.get('status', 'UNKNOWN')
                    status_color = "green" if status_val == "EXECUTED" else "red" if status_val == "EXPIRED" else "yellow"

                    hist_table.add_row(
                        h.get('symbol', 'Unknown'),
                        h.get('direction', ''),
                        f"[{status_color}]{status_val}[/{status_color}]",
                        f"{h.get('last_score', 0):.1f}",
                        str(h.get('total_checks', 0))
                    )

                console.print(hist_table)

            # Footer with commands
            console.print("\n" + "="*80)
            console.print("[bold cyan]Quick Commands:[/bold cyan]")
            console.print("• Test LONG signal: [yellow]python3 test_waiting_mode_long.py[/yellow]")
            console.print("• Test SHORT signal: [yellow]python3 test_waiting_mode.py[/yellow]")
            console.print("• View logs: [yellow]tail -f trading_bot.log[/yellow]")
            console.print("• Stop monitoring: [red]Press Ctrl+C[/red]")

            # Refresh every 5 seconds
            await asyncio.sleep(5)

async def main():
    """Main function"""
    console.print("[bold green]Starting Trading Bot Monitor Dashboard...[/bold green]")
    console.print("[dim]Connecting to http://localhost:8080[/dim]\n")

    try:
        await create_dashboard()
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)