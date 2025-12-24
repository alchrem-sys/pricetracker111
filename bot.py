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

# зберігаємо задачі по юзерам і токенах: {user_id: {ticker: asyncio.Task}}
user_tasks: dict[int, dict[str, asyncio.Task]] = {}


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
        price_str = data.get("price")
        if price_str is None:
            return None
        return float(price_str)
    except Exception:
        return None


async def price_sender(user_id: int, base_ticker: str, interval_sec: int, app):
    """
    Шле ціну раз на interval_sec секунд для конкретного токена.
    """
    symbol = base_ticker.upper() + "USDT"

    while True:
        price = get_mexc_price(symbol)
        if price is None:
            await app.bot.send_message(
                chat_id=user_id,
                text=f"Не знайшов пару {symbol} на MEXC. Зупиняю розсилку для {base_ticker.upper()}."
            )
            return  # виходимо з циклу, задача завершується

        # Повна точність без округлення
        await app.bot.send_message(
            chat_id=user_id,
            text=f"{base_ticker.upper()} ({symbol}) = ${price} USDT (MEXC)"
        )

        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            return  # задача скасована /stop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Напиши /subscribe, щоб налаштувати сповіщення по токену.\n"
        "Наприклад: BTC, SOL, NOT і т.д.\n\n"
        "/stop [тікер] - зупинити конкретний токен\n"
        "/stop - зупинити всі сповіщення\n"
        "/status - переглянути активні токени"
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введи тікер токена (без /USDT, просто btc, sol, not тощо):"
    )
    return TICKER


async def set_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = update.message.text.strip().lower()
    context.user_data["ticker"] = ticker
    await update.message.reply_text(
        "Введи інтервал в хвилинах (наприклад, 1, 5, 15):"
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
    
    # ініціалізуємо словник для юзера, якщо нема
    if user_id not in user_tasks:
        user_tasks[user_id] = {}

    app = context.application

    # зупинимо стару задачу для цього токена, якщо є
    old_task = user_tasks[user_id].get(ticker)
    if old_task and not old_task.done():
        old_task.cancel()

    new_task = asyncio.create_task(price_sender(user_id, ticker, interval_sec, app))
    user_tasks[user_id][ticker] = new_task

    await update.message.reply_text(
        f"Налаштовано. Сповіщення для {ticker.upper()} кожні {minutes} хвилин.\n"
        f"Активних токенів: {len(user_tasks[user_id])}\n"
        f"/stop {ticker} - зупинити цей токен\n"
        f"/stop - зупинити всі"
    )
    return ConversationHandler.END


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # якщо аргументів нема - зупиняємо всі
    if not context.args:
        if user_id in user_tasks:
            for ticker, task in list(user_tasks[user_id].items()):
                if not task.done():
                    task.cancel()
            del user_tasks[user_id]
            await update.message.reply_text("Всі сповіщення зупинені.")
        else:
            await update.message.reply_text("У вас немає активних сповіщень.")
        return

    # зупиняємо конкретний токен
    ticker_to_stop = context.args[0].lower()
    if user_id in user_tasks and ticker_to_stop in user_tasks[user_id]:
        task = user_tasks[user_id][ticker_to_stop]
        if not task.done():
            task.cancel()
            del user_tasks[user_id][ticker_to_stop]
            remaining = len(user_tasks[user_id])
            status = f"Зупинено {ticker_to_stop.upper()}. Залишилось: {remaining}" if remaining > 0 else "Зупинено останній токен."
            await update.message.reply_text(status)
        else:
            await update.message.reply_text(f"Для {ticker_to_stop.upper()} немає активних сповіщень.")
    else:
        await update.message.reply_text(f"Сповіщення для {ticker_to_stop.upper()} не знайдено.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує активні токени"""
    user_id = update.message.from_user.id
    if user_id in user_tasks and user_tasks[user_id]:
        active = ", ".join(ticker.upper() for ticker in user_tasks[user_id])
        await update.message.reply_text(f"Активні токени ({len(user_tasks[user_id])}):\n{active}")
    else:
        await update.message.reply_text("Немає активних сповіщень.")


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
    app.add_handler(CommandHandler("status", status_cmd))

    print("Бот запущено!")
    app.run_polling()


if __name__ == "__main__":
    main()
