import ccxt
import time
import requests
import threading
from collections import deque

# ========= НАСТРОЙКИ =========
TELEGRAM_TOKEN = "7827921634:AAEr0whnhrDNibZxUtTXZHtr31eAqdV6gr0"
CHAT_ID = "1192605614"

EXCHANGE_NAMES = ['binance', 'bybit', 'bitget', 'mexc']  # биржи
QUOTE_FILTER = 'USDT'        # фильтр валютных пар
MIN_SPREAD = 5.0             # минимальный % спред для уведомления
MIN_VOLUME_USDT = 5000       # минимальная ликвидность в USDT
CHECK_INTERVAL = 30          # интервал проверки (в сек)
LOAD_PAUSE = 1.0             # пауза между загрузками бирж

# ========= СОСТОЯНИЕ =========
notifications_enabled = True
last_spreads = deque(maxlen=5)  # храним последние 5 спредов


# ========= TELEGRAM =========
def send_telegram(msg):
    """Отправка сообщений в Telegram"""
    global notifications_enabled
    if not notifications_enabled and not msg.startswith("🔔") and not msg.startswith("🔕"):
        print("🔕 Уведомления выключены, сообщение не отправлено.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'HTML'})
    except Exception as e:
        print("Ошибка Telegram:", e)


def check_telegram_commands():
    """Фоновая проверка команд /on /off /status /spred /setspread /setvolume /setinterval"""
    global notifications_enabled, MIN_SPREAD, MIN_VOLUME_USDT, CHECK_INTERVAL
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}"
            response = requests.get(url).json()

            if "result" in response:
                for update in response["result"]:
                    offset = update["update_id"] + 1
                    if "message" not in update:
                        continue

                    text = update["message"].get("text", "").lower()
                    chat_id = update["message"]["chat"]["id"]

                    if str(chat_id) != str(CHAT_ID):
                        continue  # игнорируем чужие чаты

                    if text == "/off":
                        notifications_enabled = False
                        send_telegram("🔕 Уведомления о спредах выключены.")
                    elif text == "/on":
                        notifications_enabled = True
                        send_telegram("🔔 Уведомления о спредах включены.")
                    elif text == "/status":
                        status_text = (
                            "📊 <b>Статус арбитражного бота:</b>\n\n"
                            f"• Уведомления: {'✅ Включены' if notifications_enabled else '❌ Выключены'}\n"
                            f"• Биржи: {', '.join(EXCHANGE_NAMES)}\n"
                            f"• Валюта: {QUOTE_FILTER}\n"
                            f"• Мин. спред: {MIN_SPREAD}%\n"
                            f"• Мин. объём: {MIN_VOLUME_USDT} USDT\n"
                            f"• Интервал проверки: {CHECK_INTERVAL} сек"
                        )
                        send_telegram(status_text)
                    elif text == "/spred":
                        if not last_spreads:
                            send_telegram("ℹ️ Пока нет найденных спредов.")
                        else:
                            msg = "💹 <b>Последние найденные спреды:</b>\n\n"
                            for s in list(last_spreads)[-5:]:
                                msg += s + "\n\n"
                            send_telegram(msg)
                    elif text.startswith("/setspread"):
                        try:
                            parts = text.split()
                            if len(parts) != 2:
                                send_telegram("⚠️ Использование: /setspread 0.8")
                                continue
                            new_spread = float(parts[1])
                            if new_spread <= 0 or new_spread > 100:
                                send_telegram("⚠️ Спред должен быть положительным числом меньше 100.")
                                continue
                            MIN_SPREAD = new_spread
                            send_telegram(f"✅ Минимальный спред изменён на {MIN_SPREAD:.2f}%")
                        except ValueError:
                            send_telegram("⚠️ Некорректное значение. Введите число, например: /setspread 0.8")
                    elif text.startswith("/setvolume"):
                        try:
                            parts = text.split()
                            if len(parts) != 2:
                                send_telegram("⚠️ Использование: /setvolume 500")
                                continue
                            new_volume = float(parts[1])
                            if new_volume <= 0:
                                send_telegram("⚠️ Ликвидность должна быть положительным числом.")
                                continue
                            MIN_VOLUME_USDT = new_volume
                            send_telegram(f"✅ Минимальная ликвидность изменена на {MIN_VOLUME_USDT:.2f} USDT")
                        except ValueError:
                            send_telegram("⚠️ Некорректное значение. Введите число, например: /setvolume 500")
                    elif text.startswith("/setinterval"):
                        try:
                            parts = text.split()
                            if len(parts) != 2:
                                send_telegram("⚠️ Использование: /setinterval 30")
                                continue
                            new_interval = float(parts[1])
                            if new_interval < 1:
                                send_telegram("⚠️ Интервал должен быть не меньше 1 секунды.")
                                continue
                            CHECK_INTERVAL = new_interval
                            send_telegram(f"✅ Интервал проверки спредов изменён на {CHECK_INTERVAL:.1f} сек")
                        except ValueError:
                            send_telegram("⚠️ Некорректное значение. Введите число, например: /setinterval 30")
        except Exception as e:
            print("Ошибка при чтении команд:", e)
        time.sleep(3)


# ========= ФУНКЦИИ АРБИТРАЖА =========
def create_exchanges(names):
    exs = {}
    for n in names:
        try:
            ex = getattr(ccxt, n)({'enableRateLimit': True})
            exs[n] = ex
        except Exception as e:
            print(f"Ошибка инициализации {n}: {e}")
    return exs


def market_is_active(market):
    """Проверка активности рынка"""
    if market.get('active') is False:
        return False
    info = market.get('info', {})
    status = str(info.get('status') or info.get('state') or '').lower()
    if status and status not in ('trading', 'online', 'active', 'ok'):
        return False
    if str(info.get('isFrozen', '0')).lower() in ('1', 'true'):
        return False
    if str(info.get('isTrading', 'true')).lower() in ('0', 'false'):
        return False
    return True


def load_tradeable_markets(exchanges, quote_filter):
    result = {}
    for name, ex in exchanges.items():
        try:
            print(f"Загружаю рынки {name}...")
            ex.load_markets()
            symbols = [
                sym for sym, m in ex.markets.items()
                if sym.endswith(f"/{quote_filter}") and market_is_active(m)
            ]
            result[name] = {'instance': ex, 'symbols': symbols}
            print(f"  {name}: {len(symbols)} активных пар")
            time.sleep(LOAD_PAUSE)
        except Exception as e:
            print(f"Ошибка загрузки {name}: {e}")
    return result


def get_best_prices(ex, symbol):
    """Возвращает цены bid/ask и объёмы"""
    try:
        ob = ex.fetch_order_book(symbol, 5)
        bids, asks = ob.get('bids', []), ob.get('asks', [])
        if not bids or not asks:
            return None, None, None, None
        bid_p, bid_v = bids[0]
        ask_p, ask_v = asks[0]
        return bid_p, bid_v, ask_p, ask_v
    except:
        return None, None, None, None


def check_all_pairs(exchanges_info):
    """Основная проверка арбитражных возможностей"""
    all_symbols = set()
    for info in exchanges_info.values():
        all_symbols.update(info['symbols'])

    for symbol in sorted(all_symbols):
        prices = {}
        liquidity = {}

        for name, info in exchanges_info.items():
            ex = info['instance']
            if symbol not in info['symbols']:
                continue
            bid_p, bid_v, ask_p, ask_v = get_best_prices(ex, symbol)
            if not bid_p or not ask_p:
                continue
            if (bid_p * bid_v < MIN_VOLUME_USDT) or (ask_p * ask_v < MIN_VOLUME_USDT):
                continue

            mid_price = (bid_p + ask_p) / 2
            prices[name] = mid_price
            liquidity[name] = int((bid_p * bid_v + ask_p * ask_v) / 2)

        if len(prices) < 2:
            continue

        min_ex = min(prices, key=prices.get)
        max_ex = max(prices, key=prices.get)
        min_price = prices[min_ex]
        max_price = prices[max_ex]
        spread = ((max_price - min_price) / min_price) * 100

        if spread >= MIN_SPREAD:
            msg = (
                f"💸 <b>{symbol}</b>\n"
                f"Купить на <b>{min_ex}</b> — <code>{min_price:.4f}</code>\n"
                f"Продать на <b>{max_ex}</b> — <code>{max_price:.4f}</code>\n"
                f"📈 Спред: <b>+{spread:.2f}%</b>\n"
                f"💧 Ликвидность: {liquidity[min_ex]}–{liquidity[max_ex]} USDT"
            )
            last_spreads.append(msg)
            print(msg)
            send_telegram(msg)


# ========= ЗАПУСК =========
if __name__ == "__main__":
    print("Запуск арбитражного бота...")
    send_telegram("🤖 Бот запущен. Используй /on, /off, /status, /spred и /setspread для управления.")
    threading.Thread(target=check_telegram_commands, daemon=True).start()

    exchanges = create_exchanges(EXCHANGE_NAMES)
    exchanges_info = load_tradeable_markets(exchanges, QUOTE_FILTER)
    print("Биржи и пары загружены. Проверка спредов...")

    while True:
        try:
            check_all_pairs(exchanges_info)
        except Exception as e:
            print("Ошибка во время работы:", e)
        time.sleep(CHECK_INTERVAL)



