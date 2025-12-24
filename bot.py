import os
import asyncio
import logging
import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)

MEXC_TICKER_URL = "https://api.mexc.com/api/v3/ticker/price"

# стейти діалогу
TICKER, INTERVAL = range(2)

# зберігаємо задачі по юзерам, щоб можна було стопнути
user_tasks: dict[int, asyncio.Task] = {}


def get_mexc_price(symbol: str) -> float | None:
    """
    symbol: наприклад BTCUSDT, SOLUSDT і т.д.
    Повертає float або None, якщо символу нема.
    """
    try:
        r = requests.get(MEXC_TICKER_URL, params={"symbol": symbol}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        # формат відповіді: {"symbol": "BTCUSDT", "price": "46263.71"} [web:46]
        price_str = data.get("price")
        if price_str is None:
            return None
        return float(price_str)
    except Exception:
        return None


async def price_sender(user_id: int, base_ticker: str, interval_sec: int, app):
    """
    Після запуску шле ціну раз на interval_sec секунд, поки таску не скасують.
    """
    symbol = base_ticker.upper() + "USDT"

    while True:
        price = get_mexc_price(symbol)
        if price is None:
            await app.bot.send_message(
                chat_id=user_id,
                text=f"Не знайшов пару {symbol} на MEXC. Зупиняю розсилку."
            )
            break

        await app.bot.send_message(
            chat_id=user_id,
            text=f"{base_ticker.upper()} ({symbol}) = {price} USDT (MEXC)"
        )

        await asyncio.sleep(interval_sec)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Напиши /subscribe, щоб налаштувати сповіщення по токену.\n"
        "Наприклад: BTC, SOL, NOT і т.д."
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введи тікер токена (без /USDT, просто btc, sol, not тощо):"
    )
    return TICKER


async def set_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip()
    context.user_data["ticker"] = ticker
    await update.message.reply_text(
        "Ок. Введи інтервал в хвилинах (наприклад, 1, 5, 15):"
    )
    return INTERVAL


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    try:
        minutes = int(update.message.text.strip())
        if minutes < 1:
            await update.message.reply_text("Мінімум 1 хвилина. Введи ще раз.")
            return INTERVAL
    except ValueError:
        await update.message.reply_text("Напиши число в хвилинах.")
        return INTERVAL

    interval_sec = minutes * 60
    ticker = context.user_data["ticker"]
    app = context.application

    # зупинимо стару задачу, якщо вона є
    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()

    new_task = asyncio.create_task(price_sender(user_id, ticker, interval_sec, app))
    user_tasks[user_id] = new_task

    await update.message.reply_text(
        f"Готово! Буду слати {ticker.upper()} кожні {minutes} хвилин.\n"
        f"Напиши /stop, щоб зупинити."
    )
    return ConversationHandler.END


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        await update.message.reply_text("Сповіщення зупинені.")
    else:
        await update.message.reply_text("У тебе немає активних сповіщень.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операцію скасовано.")
    return ConversationHandler.END


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN env var is required")

    app = ApplicationBuilder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("subscribe", subscribe)],
        states={
            TICKER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_ticker)],
            INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_interval)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("stop", stop_cmd))

    app.run_polling()


if __name__ == "__main__":
    main()
