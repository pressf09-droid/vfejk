import os
import re
import json
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8127267488:AAHSi0TdVqY5rrwirwrdwh83tJj8QrAD7nk")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1VIDqATnLXRSzpOj6hek9Y_BvafmydHblZCIn-pwxXqA")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))  # 629870642

# ─── МАППИНГИ ─────────────────────────────────────────────────────────────────
# Категории: ключевые слова → название листа в Google Sheets
CATEGORY_MAP = {
    "джинсы": ["джинс", "denim", "jeans"],
    "футболка": ["футболк", "t-shirt", "tee", "тишот"],
    "худи": ["худи", "hoodie", "hoody"],
    "куртка": ["куртк", "jacket", "coat"],
    "штаны": ["штан", "pants", "брюк", "trousers"],
    "кроссовки": ["кроссовк", "sneaker", "кеды"],
    "сумка": ["сумк", "bag"],
    "шапка": ["шапк", "hat", "cap", "кепк"],
    "Свитшот": ["джинс", "denim", "jeans"],
    # Добавь свои категории
}

# Цвета EN → RU
COLOR_MAP = {
    "black": "чёрный",
    "white": "белый",
    "blue": "синий",
    "red": "красный",
    "green": "зелёный",
    "grey": "серый",
    "gray": "серый",
    "beige": "бежевый",
    "brown": "коричневый",
    "yellow": "жёлтый",
    "pink": "розовый",
    "purple": "фиолетовый",
    "orange": "оранжевый",
    "navy": "тёмно-синий",
    "cream": "кремовый",
    "khaki": "хаки",
    "olive": "оливковый",
    "faded": "варёный",
    "washed": "варёный",
    "light": "светлый",
    "dark": "тёмный",
    "multicolor": "разноцветный",
}

# Состояние по умолчанию
DEFAULT_CONDITION = "9"

# Хранилище последних записей для редактирования
last_entries = {}

# ─── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_sheets_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_data = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_data:
        creds_dict = json.loads(creds_data)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
    return gspread.authorize(creds)


def get_worksheet(sheet_name: str):
    """Находит лист по названию (нечёткий поиск)."""
    client = get_sheets_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    worksheets = spreadsheet.worksheets()
    sheet_name_lower = sheet_name.lower()
    for ws in worksheets:
        if sheet_name_lower in ws.title.lower() or ws.title.lower() in sheet_name_lower:
            return ws
    return None


def get_column_map(ws) -> dict:
    """Возвращает маппинг 'название столбца' → индекс (1-based)."""
    headers = ws.row_values(1)
    return {h.lower().strip(): i + 1 for i, h in enumerate(headers) if h}


def find_next_empty_row(ws) -> int:
    """Находит первую полностью пустую строку."""
    col_a = ws.col_values(1)
    for i, val in enumerate(col_a[1:], start=2):  # пропускаем заголовок
        if not val.strip():
            return i
    return len(col_a) + 1


def write_to_sheet(sheet_name: str, data: dict) -> tuple[bool, str]:
    """
    Записывает данные в лист.
    data = {'бренд': 'LABORATORY', 'цвет': 'синий', ...}
    Возвращает (успех, сообщение).
    """
    ws = get_worksheet(sheet_name)
    if not ws:
        return False, f"Лист '{sheet_name}' не найден в таблице"

    col_map = get_column_map(ws)
    row = find_next_empty_row(ws)

    updates = []
    written = []
    for field, value in data.items():
        field_lower = field.lower().strip()
        if field_lower in col_map:
            col = col_map[field_lower]
            updates.append({"range": gspread.utils.rowcol_to_a1(row, col), "values": [[value]]})
            written.append(f"{field}: {value}")

    if not updates:
        return False, "Не найдено совпадений с заголовками таблицы"

    ws.batch_update(updates)
    return True, f"Лист: *{ws.title}*, строка {row}\n" + "\n".join(written)


def update_cell(sheet_name: str, row: int, field: str, new_value: str) -> tuple[bool, str]:
    """Обновляет конкретную ячейку."""
    ws = get_worksheet(sheet_name)
    if not ws:
        return False, f"Лист '{sheet_name}' не найден"
    col_map = get_column_map(ws)
    field_lower = field.lower().strip()
    if field_lower not in col_map:
        return False, f"Столбец '{field}' не найден"
    col = col_map[field_lower]
    ws.update_cell(row, col, new_value)
    return True, f"Обновил *{field}* → {new_value} (строка {row})"


# ─── ПАРСИНГ ПОСТА ─────────────────────────────────────────────────────────────
def normalize_size(raw: str) -> str:
    """Нормализует размер — убирает лишнее, оставляет только базовый."""
    raw = raw.strip()
    # Если содержит скобку типа "(Сидит как кроп М)" — берём последнюю букву/цифру
    match = re.search(r'\b(XXS|XS|S|M|L|XL|XXL|XXXL|\d{2,3})\b', raw, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return raw.upper()


def normalize_color(raw: str) -> str:
    """Переводит цвет EN→RU, если есть в словаре."""
    raw_lower = raw.lower().strip()
    for en, ru in COLOR_MAP.items():
        if en in raw_lower:
            return ru
    return raw.capitalize()


def clean_price(raw: str) -> str:
    """35.000r → 35000, 17 500 → 17500."""
    digits = re.sub(r'[^\d]', '', raw)
    return digits if digits else raw


def detect_category(text: str) -> str | None:
    """Определяет категорию товара из текста."""
    text_lower = text.lower()
    for sheet_name, keywords in CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return sheet_name
    return None


def parse_post(text: str) -> dict:
    """
    Парсит пост и возвращает словарь с данными.
    Пример: LABORATORY VERY RARE LOGO HEAVY BLUE FADED OVER ДЖИНСЫ...
    """
    result = {}

    # ── Категория ──
    category = detect_category(text)
    result["_category"] = category  # служебное поле

    # ── Бренд ── (всё до VERY RARE / RARE / LOGO или первого не-капс слова)
    brand_match = re.match(r'^([A-ZА-Я][A-ZА-Я\s&\-\.]+?)(?=\s+(?:VERY|RARE|LOGO|HEAVY|LIGHT|DARK|OVER|VINTAGE|WASHED|FADED|\d))', text)
    if brand_match:
        result["бренд"] = brand_match.group(1).strip()
    else:
        # Берём первое слово капсом
        first_caps = re.match(r'^([A-ZА-Я][A-ZА-Я]+)', text)
        if first_caps:
            result["бренд"] = first_caps.group(1)

    # ── Цвет ── (ищем известные цвета в тексте)
    text_upper = text.upper()
    for en_color in COLOR_MAP:
        if en_color.upper() in text_upper:
            result["цвет"] = normalize_color(en_color)
            break

    # ── Размер ── РАЗМЕР: X или SIZE: X
    size_match = re.search(r'РАЗМЕР[:\s]+([^\n,]+)', text, re.IGNORECASE)
    if not size_match:
        size_match = re.search(r'SIZE[:\s]+([^\n,]+)', text, re.IGNORECASE)
    if size_match:
        result["размер"] = normalize_size(size_match.group(1))

    # ── Цены ── ищем числа с р / r / ₽ или просто большие числа
    prices = re.findall(r'([\d][\d\s\.\,]*)\s*[rрR₽]', text)
    prices_clean = [clean_price(p) for p in prices if clean_price(p)]
    # Убираем слишком короткие (меньше 3 цифр)
    prices_clean = [p for p in prices_clean if len(p) >= 3]

    if len(prices_clean) >= 2:
        # Сортируем — большая цена = оригинал, меньшая = со скидкой
        prices_sorted = sorted(set(prices_clean), key=lambda x: int(x), reverse=True)
        result["цена"] = prices_sorted[0]
        result["цена -50%"] = prices_sorted[1]
    elif len(prices_clean) == 1:
        result["цена"] = prices_clean[0]

    # ── Состояние ── (по умолчанию 9, если не указано явно)
    cond_match = re.search(r'состояние[:\s]+(\d+[\+\-]?\/?\d*)', text, re.IGNORECASE)
    result["состояние"] = cond_match.group(1) if cond_match else DEFAULT_CONDITION

    # ── Контакт ── @username
    contact_match = re.search(r'@[\w]+', text)
    if contact_match:
        result["контакт"] = contact_match.group(0)

    # ── Адрес ── текст после "переулок/улица/проспект" и т.д.
    addr_match = re.search(
        r'((?:улиц|переулок|проспект|пер\.|ул\.|пр\.)[а-яА-Яa-zA-Z\s]+\d+)',
        text, re.IGNORECASE
    )
    if addr_match:
        result["адрес"] = addr_match.group(1).strip()

    return result


def format_parsed(data: dict) -> str:
    """Красиво форматирует распознанные данные для подтверждения."""
    lines = []
    skip = {"_category"}
    for k, v in data.items():
        if k not in skip:
            lines.append(f"• *{k.capitalize()}*: {v}")
    category = data.get("_category", "не определена")
    header = f"📋 Категория: *{category.capitalize() if category else '❓ не найдена'}*\n"
    return header + "\n".join(lines)


# ─── КОМАНДЫ РЕДАКТИРОВАНИЯ ────────────────────────────────────────────────────
def parse_edit_command(text: str) -> tuple[str | None, str | None]:
    """
    Парсит команду типа:
    "поменяй цвет на красный"
    "измени цену на 30000"
    "обнови размер на XL"
    Возвращает (поле, новое_значение).
    """
    pattern = r'(?:поменяй|измени|обнови|замени|исправь)\s+(\S+)\s+на\s+(.+)'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        field = match.group(1).lower().strip()
        value = match.group(2).strip()
        # Нормализация значений
        if field == "цвет":
            value = normalize_color(value)
        elif field in ("цена", "цена_50", "цена -50%"):
            value = clean_price(value)
        elif field == "размер":
            value = normalize_size(value)
        return field, value
    return None, None


# ─── ОБРАБОТЧИКИ TELEGRAM ──────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для записи товаров в Google Sheets.\n\n"
        "📌 *Как использовать:*\n"
        "• Просто отправь мне текст поста — я распознаю данные и запишу в нужную таблицу\n"
        "• Чтобы изменить поле: _поменяй цвет на красный_\n"
        "• `/status` — проверить подключение к Sheets\n\n"
        "Поехали! 🚀",
        parse_mode="Markdown"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheets = [ws.title for ws in spreadsheet.worksheets()]
        await update.message.reply_text(
            f"✅ Подключение работает!\n📊 Найдено листов: {len(sheets)}\n"
            + "\n".join(f"• {s}" for s in sheets)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка подключения:\n`{e}`", parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    text = update.message.text.strip()

    # ── Команда редактирования ──
    if any(text.lower().startswith(w) for w in ["поменяй", "измени", "обнови", "замени", "исправь"]):
        field, value = parse_edit_command(text)
        if field and value and user_id in last_entries:
            entry = last_entries[user_id]
            ok, msg = update_cell(entry["sheet"], entry["row"], field, value)
            symbol = "✅" if ok else "❌"
            await update.message.reply_text(f"{symbol} {msg}", parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "⚠️ Не понял команду или нет последней записи.\n"
                "Формат: _поменяй цвет на красный_",
                parse_mode="Markdown"
            )
        return

    # ── Парсинг нового поста ──
    await update.message.reply_text("🔍 Анализирую пост...")

    data = parse_post(text)
    category = data.get("_category")

    if not category:
        await update.message.reply_text(
            "❓ Не смог определить категорию товара.\n"
            "Убедись, что в тексте есть название (джинсы, футболка, куртка и т.д.)"
        )
        return

    # Показываем что распознали
    preview = format_parsed(data)
    await update.message.reply_text(
        f"📦 Распознал:\n\n{preview}\n\n⏳ Записываю...",
        parse_mode="Markdown"
    )

    # Убираем служебные поля перед записью
    write_data = {k: v for k, v in data.items() if not k.startswith("_")}

    ok, msg = write_to_sheet(category, write_data)

    if ok:
        # Сохраняем для возможного редактирования
        row_num = int(re.search(r'строка (\d+)', msg).group(1))
        last_entries[user_id] = {"sheet": category, "row": row_num}
        await update.message.reply_text(
            f"✅ Записал!\n\n{msg}\n\n"
            "💡 Хочешь что-то изменить? Напиши:\n_поменяй цвет на красный_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ Ошибка: {msg}")


# ─── ЗАПУСК ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()