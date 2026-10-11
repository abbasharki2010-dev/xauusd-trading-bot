
# XAUUSD Telegram Signal Bot

A Python Telegram bot for XAUUSD signal monitoring.

## Features

- Support and Resistance commands
- 1H breakout monitoring
- 15M candlestick confirmation
- BUY and SELL signal notifications
- Entry, Stop Loss, TP1 and TP2
- Weekly signal report
- Signal-only mode: no MT5 orders are placed

## Requirements

- Python 3.12+
- Telegram bot token
- Telegram chat ID
- Market data API access

## Environment Variables

Set these in Railway Variables:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## Run

python main.py

## Telegram Commands

/support 4275 4300
/status
/cancel
