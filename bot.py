import os
import asyncio
import uuid
import io
import time
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
from yoomoney import Quickpay, Client
import qrcode

# Импортируем твои модули
import database
import scheduler
from xui_api import XuiAPI

# Инициализация логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Инициализируем БД при старте
database.init_db()

# Инициализация бота
TOKEN = os.getenv("BOT_TOKEN")
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN")
YOOMONEY_RECEIVER = os.getenv("YOOMONEY_RECEIVER")

XUI_URL = os.getenv("XUI_URL", "http://127.0.0.1:60000")
XUI_INBOUND_ID = int(os.getenv("XUI_INBOUND_ID", "3"))

bot = Bot(token=TOKEN)
dp = Dispatcher()
xui = XuiAPI(url=XUI_URL)

pending_payments = {}

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ЭКРАНИРОВАНИЯ MARKDOWNV2 ---
def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2"""
    special_chars = r'_*[]()~`>#+-=|{}.!'
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

# --- СОСТОЯНИЯ ДЛЯ ВВОДА ПРОМОКОДА ---
class PromoStates(StatesGroup):
    waiting_for_promo = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И УПРАВЛЕНИЕ ЦЕНАМИ ---

def get_user_prices(user_id: int) -> tuple[int, int, bool]:
    """Возвращает (цена_1м, цена_3м, имеет_ли_скидку)"""
    user_record = database.get_user(user_id)
    
    # Базовые цены из .env (или дефолты, если забыл прописать)
    p1 = int(os.getenv("PRICE_1M", 149))
    p3 = int(os.getenv("PRICE_3M", 420))
    
    if user_record and len(user_record) >= 4 and user_record[3]: # user_record[3] — это applied_promo
        p1 = int(os.getenv("DISCOUNT_PRICE_1M", 100))
        p3 = int(os.getenv("DISCOUNT_PRICE_3M", 250))
        return p1, p3, True
        
    return p1, p3, False

def generate_qr_code(data_text: str) -> types.BufferedInputFile:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return types.BufferedInputFile(img_byte_arr.read(), filename="qrcode.png")

async def grant_subscription(user_id: int, username: str, days: int) -> tuple[str, str, int]:
    added_ms = days * 24 * 60 * 60 * 1000
    user_record = database.get_user(user_id)

    if user_record and user_record[0]: # Проверяем наличие uuid
        client_uuid, client_email, current_expiry = user_record[0], user_record[1], user_record[2]
        now_ms = int(time.time() * 1000)
        start_ms = max(current_expiry, now_ms)
        new_expiry = start_ms + added_ms
    else:
        client_uuid = str(uuid.uuid4())
        client_email = f"tg_{user_id}_{username or 'user'}_{user_record[3]}"
        new_expiry = int(time.time() * 1000) + added_ms

    database.add_or_update_user(user_id, username, client_uuid, client_email, new_expiry)

    success = await xui.add_client(
        inbound_id=XUI_INBOUND_ID,
        client_uuid=client_uuid,
        email=client_email,
        expiry_time_ms=new_expiry
    )

    if not success:
        logger.warning(f"⚠️ Не удалось добавить клиента {client_email} в панель 3x-ui.")

    return client_uuid, client_email, new_expiry

# --- СБОРКА ИНЛАЙН-КЛАВИАТУР ---

def get_main_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Купить VPN", callback_data="menu_buy")
    builder.button(text="👤 Мой кабинет", callback_data="menu_cabinet")
    builder.button(text="ℹ️ Инструкции", callback_data="menu_instructions")
    builder.button(text="🛠️ Тех. Поддержка", callback_data="menu_support")
    builder.adjust(2, 1, 1)
    return builder.as_markup()

def get_back_to_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В главное меню", callback_data="to_main")
    return builder.as_markup()

def get_tariffs_keyboard(user_id: int):
    p1, p3, has_discount = get_user_prices(user_id)
    builder = InlineKeyboardBuilder()
    
    builder.button(text=f"🔥 1 месяц — {p1}₽", callback_data="buy_1m")
    builder.button(text=f"⚡️ 3 месяца — {p3}₽", callback_data="buy_3m")
    builder.button(text="🤖 Тест 2 дня (Бесплатно)", callback_data="buy_test")
    
    if not has_discount:
        builder.button(text="🎟 Ввести промокод", callback_data="enter_promo")
        
    builder.button(text="⬅️ Назад", callback_data="to_main")
    
    # Меняем разметку кнопок в зависимости от наличия кнопки промокода
    if not has_discount:
        builder.adjust(2, 1, 1, 1)
    else:
        builder.adjust(2, 1, 1)
    return builder.as_markup()

def get_payment_methods(tariff_name, price):
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Картой (ЮMoney)", callback_data=f"pay_yoo_{tariff_name}_{price}")
    builder.button(text="⭐️ Telegram Stars", callback_data=f"pay_stars_{tariff_name}_{price}")
    builder.button(text="⬅️ Назад к тарифам", callback_data="menu_buy")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_yoomoney_keyboard(pay_url, label):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Перейти к оплате", url=pay_url)
    builder.button(text="✅ Я оплатил", callback_data=f"check_{label}")
    builder.button(text="⬅️ Отмена", callback_data="menu_buy")
    builder.adjust(1, 1, 1)
    return builder.as_markup()

def get_instructions_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🍏 iPhone / iPad / MacBook (iOS)", callback_data="inst_ios")
    builder.button(text="🤖 Android", callback_data="inst_android")
    builder.button(text="💻 ПК (Windows)", callback_data="inst_windows")
    builder.button(text="💻 ПК (Linux)", callback_data="inst_linux")
    builder.button(text="⬅️ Назад", callback_data="to_main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


# --- ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    text = escape_md(
        "👋 **Добро пожаловать в корпорацию FufelshmertsVPN\!**\n\n"
        "Мой создатель разработал лучший «Инатор» для свободного интернета без блокировок\. "
        "Управляйте подпиской с помощью кнопок ниже:"
    )
    await message.answer(
        text,
        reply_markup=get_main_inline_keyboard(),
        parse_mode="MarkdownV2"
    )

@dp.callback_query(F.data == "to_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = escape_md("👋 **FufelshmertsVPN — Главное меню:**")
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(
            text,
            reply_markup=get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
    else:
        await callback.message.edit_text(
            text,
            reply_markup=get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
    await callback.answer()

@dp.callback_query(F.data == "menu_buy")
async def show_tariffs_inline(callback: types.CallbackQuery):
    _, _, has_discount = get_user_prices(callback.from_user.id)
    
    text = escape_md("⚡️ **Выберите тарифный план:**\n\n")
    if has_discount:
        text += escape_md("🎉 **У вас активирована скидка по промокоду\!** Цены снижены\.\n\n")
    text += escape_md("Все тарифы включают высокую скорость, поддержку Discord \(режим TUN\) и безлимитный трафик\.")

    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=get_tariffs_keyboard(callback.from_user.id), parse_mode="MarkdownV2")
    else:
        await callback.message.edit_text(text, reply_markup=get_tariffs_keyboard(callback.from_user.id), parse_mode="MarkdownV2")
    await callback.answer()

# ХЕНДЛЕР НАЖАТИЯ: ВВЕСТИ ПРОМОКОД
@dp.callback_query(F.data == "enter_promo")
async def ask_for_promocode(callback: types.CallbackQuery, state: FSMContext):
    text = escape_md(
        "🎟 **Ввод промокода**\n\n"
        "Пришлите мне промокод ответным текстовым сообщением:\n"
        "\(Например: `YOUTUBER1`\)"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="MarkdownV2"
    )
    await state.set_state(PromoStates.waiting_for_promo)
    await callback.answer()

# ХЕНДЛЕР ПРИЕМА ТЕКСТА ПРОМОКОДА
@dp.message(PromoStates.waiting_for_promo, F.text)
async def process_promo_input(message: types.Message, state: FSMContext):
    user_promo = message.text.strip().upper()
    await state.clear()
    
    # Читаем список кодов из .env
    env_promos = os.getenv("VALID_PROMOCODES", "")
    valid_promos = [p.strip().upper() for p in env_promos.split(",") if p.strip()]
    
    if user_promo in valid_promos:
        database.apply_promo_to_user(message.from_user.id, user_promo)
        
        # Получаем обновленные цены для вывода
        p1, p3, _ = get_user_prices(message.from_user.id)
        
        text = escape_md(
            f"✅ **Промокод `{user_promo}` успешно применен\!**\n\n"
            f"Ваши новые цены:\n"
            f"• 1 месяц — **{p1}₽** \(вместо {os.getenv('PRICE_1M', 149)}₽\)\n"
            f"• 3 месяца — **{p3}₽** \(вместо {os.getenv('PRICE_3M', 420)}₽\)\n\n"
            f"Откройте меню покупки, чтобы оформить подписку со скидкой\!"
        )
        await message.answer(
            text,
            reply_markup=get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Попробовать еще раз", callback_data="enter_promo")
        builder.button(text="⬅️ В главное меню", callback_data="to_main")
        builder.adjust(1)
        
        await message.answer(
            "❌ **Такого промокода не существует** или срок его действия истек.\n"
            "Проверьте правильность написания.",
            reply_markup=builder.as_markup()
        )

@dp.callback_query(F.data.startswith("buy_"))
async def choose_payment_method(callback: types.CallbackQuery):
    tariff = callback.data.split("_")[1]
    user_id = callback.from_user.id
    username = callback.from_user.username

    if tariff == "test":
        user_record = database.get_user(user_id)
        if user_record and user_record[0]:
            await callback.answer("❌ Вы уже активировали тестовый период или подписку ранее!", show_alert=True)
            return

        await callback.message.edit_text("⏳ Минутку, создаю ваш профиль в Инаторе...")
        await grant_subscription(user_id, username, days=2)

        text = escape_md(
            "🎉 **Тестовый период на 2 дня успешно активирован\!**\n\n"
            "Ваш персональный ключ и QR\-код уже сгенерированы\. Зайдите в **«👤 Мой кабинет»**\!"
        )
        await callback.message.answer(
            text,
            reply_markup=get_main_inline_keyboard(),
            parse_mode="MarkdownV2"
        )
        await callback.message.delete()
        await callback.answer()
        return

    # Динамически вычисляем стоимость с учетом промокода
    p1, p3, _ = get_user_prices(user_id)
    price = p1 if tariff == "1m" else p3
    tariff_text = "1 месяц" if tariff == "1m" else "3 месяца"

    text = escape_md(
        f"🛒 Вы выбрали тариф: **{tariff_text}**\n"
        f"💵 К оплате: **{price}₽**\n\n"
        f"Выберите удобный способ оплаты:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_payment_methods(tariff, price),
        parse_mode="MarkdownV2"
    )
    await callback.answer()

# --- ОПЛАТА ЮMONEY ---

@dp.callback_query(F.data.startswith("pay_yoo_"))
async def process_yoomoney_payment(callback: types.CallbackQuery):
    _, _, tariff, price = callback.data.split("_")
    user_id = callback.from_user.id
    
    # Получаем данные пользователя из БД, чтобы проверить наличие промокода
    user_record = database.get_user(user_id)
    promo_text = ""
    
    # user_record[3] — это applied_promo в вашей структуре БД
    if user_record and len(user_record) >= 4 and user_record[3]:
        promo_text = f" (Промокод: {user_record[3]})"
        
    label = f"{user_id}_{tariff}_{uuid.uuid4().hex[:6]}"

    quickpay = Quickpay(
        receiver=YOOMONEY_RECEIVER,
        quickpay_form="shop",
        targets=f"FufelshmertsVPN: {tariff}{promo_text}",
        paymentType="SB",
        sum=int(price),
        label=label
    )

    pending_payments[label] = {
        "user_id": user_id,
        "tariff": tariff,
        "price": price
    }

    text = escape_md(
        f"💳 **Оплата через ЮMoney**\n\n"
        f"Тариф: **{'1 месяц' if tariff == '1m' else '3 месяца'}** | К оплате: **{price}₽**\n\n"
        f"Нажмите кнопку ниже для перехода к оплате\. "
        f"После проведения транзакции обязательно вернитесь сюда и нажмите **«✅ Я оплатил»**\."
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_yoomoney_keyboard(quickpay.base_url, label),
        parse_mode="MarkdownV2"
    )
    await callback.answer()

    
@dp.callback_query(F.data.startswith("check_"))
async def check_yoomoney_payment(callback: types.CallbackQuery):
    label = callback.data.split("_", 1)[1]    
    logger.info(f"=== НАЖАТА КНОПКА ПРОВЕРКИ ДЛЯ LABEL: {label} ===")

    if label not in pending_payments:
        logger.warning(f"⚠️ Сессия платежа {label} не найдена в pending_payments (возможно бот перезапускался)")
        await callback.answer("❌ Срок действия сессии платежа истек.", show_alert=True)
        return

    payment_info = pending_payments[label]
    user_id = payment_info["user_id"]
    tariff = payment_info["tariff"]
    username = callback.from_user.username

    try:
        client = Client(YOOMONEY_TOKEN)
        
        logger.info("Запрашиваю историю операций из ЮMoney...")
        history = client.operation_history(records=30)
        logger.info(f"Получено операций от API: {len(history.operations) if history.operations else 0}")

        success = False
        if history.operations:
            for operation in history.operations:
                logger.info(f"Проверяю операцию: статус={operation.status}, label в истории={operation.label}")
                
                if operation.label == label and operation.status == "success":
                    success = True
                    break

        if success:
            del pending_payments[label]
            days = 30 if tariff == "1m" else 90
            await grant_subscription(user_id, username, days=days)

            text = escape_md(
                "🎉 **Оплата успешно получена\!**\n\n"
                "Ваша подписка на VPN успешно активирована\! Перейдите в кабинет, чтобы забрать настройки\."
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_main_inline_keyboard(),
                parse_mode="MarkdownV2"
            )
        else:
            await callback.answer("⏳ Перевод еще не поступил. Попробуйте проверить через минуту.", show_alert=True)

    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА API ЮMONEY: {e}", exc_info=True)
        await callback.answer("⚠️ Ошибка платежной системы. Попробуйте еще раз.", show_alert=True)

# --- ОПЛАТА STARS ---

@dp.callback_query(F.data.startswith("pay_stars_"))
async def process_stars_payment(callback: types.CallbackQuery):
    _, _, tariff, price = callback.data.split("_")

    stars_price = int(int(price) / 2)
    prices = [types.LabeledPrice(label=f"VPN {tariff}", amount=stars_price)]

    await callback.message.delete()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Подписка FufelshmertsVPN ({tariff})",
        description=f"Оплата подписки на VPN на {tariff} звёздами Telegram.",
        payload=f"stars_{tariff}_{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    _, tariff, user_id_str = payload.split("_")
    user_id = int(user_id_str)
    username = message.from_user.username

    days = 30 if tariff == "1m" else 90
    await grant_subscription(user_id, username, days=days)

    text = escape_md(
        "🎉 **Оплата звёздами прошла успешно\!**\n\n"
        "Ваш доступ активирован\. Откройте **«👤 Мой кабинет»** ниже, чтобы забрать настройки\."
    )
    await message.answer(
        text,
        reply_markup=get_main_inline_keyboard(),
        parse_mode="MarkdownV2"
    )

# --- НАЖАТИЕ: «👤 Мой кабинет» ---

@dp.callback_query(F.data == "menu_cabinet")
async def show_cabinet_inline(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_record = database.get_user(user_id)
    
    if not user_record or user_record[0] is None:
        text = escape_md(
            "👤 **Личный кабинет**\n\n"
            "У вас пока нет активной подписки\.\n\n"
            "Нажмите кнопку **«🛍 Купить VPN»**, чтобы активировать бесплатный тест на 2 дня или оформить тариф\!"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🛍 Купить VPN", callback_data="menu_buy")
        builder.button(text="⬅️ Назад", callback_data="to_main")
        builder.adjust(1)

        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="MarkdownV2")
        await callback.answer()
        return

    client_uuid, client_email, expiry_time_ms = user_record[0], user_record[1], user_record[2]
    now_ms = int(time.time() * 1000)

    if expiry_time_ms < now_ms:
        builder = InlineKeyboardBuilder()
        builder.button(text="🛍 Продлить подписку", callback_data="menu_buy")
        builder.button(text="⬅️ Назад", callback_data="to_main")
        builder.adjust(1)

        text = escape_md(
            "👤 **Личный кабинет**\n\n"
            "❌ Срок действия вашей подписки закончился\. Пожалуйста, продлите её\."
        )
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="MarkdownV2"
        )
        await callback.answer()
        return

    expiry_date = datetime.fromtimestamp(expiry_time_ms / 1000).strftime('%d.%m.%Y %H:%M')

    await callback.message.delete()
    msg_wait = await callback.message.answer("⏳ Соединяюсь с сервером, генерирую QR-код...")

    sub_link = await xui.generate_sub_link(client_uuid)
    qr_file = generate_qr_code(sub_link)

    caption_text = (
        f"👤 **Ваш Личный Кабинет**\n\n"
        f"• **Статус подписки:** Активен ✅\n"
        f"• **Действует до:** `{expiry_date}`\n"
        f"• **Лимит устройств:** Строго 1 устройство\n\n"
        f"🔗 **Ваша персональная ссылка (нажмите для копирования):**\n"
        f"<code>{sub_link}</code>\n\n"
        f"📱 **Инструкция по быстрому подключению:**\n"
        f"1. Скопируйте ссылку.\n"
        f"2. Откройте **Hiddify** -> нажмите **«+ Новый профиль»** -> **«Импорт из буфера»** (или отсканируйте этот QR-код камерой приложения)."
    )

    await msg_wait.delete()
    await callback.message.answer_photo(
        photo=qr_file,
        caption=caption_text,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

# --- НАЖАТИЕ: «ℹ️ Инструкции» ---

@dp.callback_query(F.data == "menu_instructions")
async def show_instructions_menu(callback: types.CallbackQuery):
    text = escape_md(
        "ℹ️ **База знаний FufelshmertsVPN**\n\n"
        "Мы работаем на самом быстром и надежном протоколе **Trojan**\.\n"
        "Выберите устройство для получения подробной пошаговой инструкции:"
    )
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=get_instructions_keyboard(), parse_mode="MarkdownV2")
    else:
        await callback.message.edit_text(text, reply_markup=get_instructions_keyboard(), parse_mode="MarkdownV2")
    await callback.answer()

# --- НАЖАТИЕ ПОДДЕРЖКИ ---
@dp.callback_query(F.data == "menu_support")
async def show_support_menu(callback: types.CallbackQuery):
    text = escape_md(
        "🛠️ **Тех\. поддержка FufelshmertsVPN**\n\n"
        "Что\-бы обратить в тех\. поддержку, напиши нам на почту \- **patio\-thigh\-water@duck\.com**\n"
        "Среднее время ответа **~12 часов**\. Заранее спасибо за ожидание"
    )
    
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="MarkdownV2")
    else:
        await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="MarkdownV2")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("inst_"))
async def process_instruction_inline(callback: types.CallbackQuery):
    platform = callback.data.split("_")[1]

    img_ios = "https://images.unsplash.com/photo-1510519138101-570d1dca3d66?w=600"
    img_android = "https://images.unsplash.com/photo-1607604276583-eef5d076aa5f?w=600"
    img_pc = "https://images.unsplash.com/photo-1547082299-de196ea013d6?w=600"

    if platform == "ios":
        text = escape_md(
            f"🍏 **Инструкция для Телефона \(Hiddify, iOS\)**\n\n"
            "1\. Скачайте Hiddify \(можно найти в App Store\)\n"
            "2\. Установите Hiddify следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"🍏 **Инструкция для Телефона \(Happ, iOS\)**\n\n"
            "1\. Скачайте Happ \(можно найти в App Store\)\n"
            "2\. Установите Happ следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"🍏 **Инструкция для Телефона \(v2rayTun, iOS\)**\n\n"
            "1\. Скачайте v2rayTun \(можно найти в App Store\)\n"
            "2\. Установите v2rayTun следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!"
        )
        photo = img_ios
    elif platform == "android":
        text = escape_md(
            f"🤖 **Инструкция для Телефона \(Hiddify, Android\)**\n\n"
            "1\. Скачайте Hiddify \(можно найти в Play Market\)\n"
            "2\. Установите Hiddify следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"🤖 **Инструкция для Телефона \(Happ, Android\)**\n\n"
            "1\. Скачайте Happ \(можно найти в Play Market\)\n"
            "2\. Установите Happ следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"🤖 **Инструкция для Телефона \(v2rayTun, Android\)**\n\n"
            "1\. Скачайте v2rayTun \(можно найти в Play Market\)\n"
            "2\. Установите v2rayTun следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!"
        )
        photo = img_android
    elif platform == "windows":
        text = escape_md(
            f"💻 **Инструкция для ПК \(Hiddify, Windows\)**\n\n"
            "1\. Скачайте Hiddify:\n"
            "https://github\.com/hiddify/hiddify-app/releases/download/v4\.1\.1/Hiddify\-Windows\-Setup\-x64\.exe\n"
            "2\. Установите Hiddify следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"💻 **Инструкция для ПК \(Happ, Windows\)**\n\n"
            "1\. Скачайте Happ:\n"
            "https://github\.com/Happ\-proxy/happ\-desktop/releases/download/3\.3\.5/setup\-Happ\.x64\.exe\n"
            "2\. Установите Happ следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!\n"
            f"💻 **Инструкция для ПК \(v2rayTun, Windows\)**\n\n"
            "1\. Скачайте v2rayTun:\n"
            "https://github\.com/mdf45/v2raytun/releases/download/v3\.8\.12/v2RayTun\_Setup\.exe\n"
            "2\. Установите v2rayTun следуя инструкциям установщика\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!"
        )
        photo = img_pc
    else:
        text = escape_md(
            f"🐧 **Инструкция для ПК \(Happ, Linux\)**\n\n"
            "1\. Скачайте Happ\n"
            "Debian/Ubuntu/Mint:\n"
            "https://github\.com/Happ\-proxy/happ\-desktop/releases/download/3\.3\.5/Happ\.linux\.x64\.deb\n"
            "Fedora/AlmaLinux:\n"
            "https://github\.com/Happ\-proxy/happ\-desktop/releases/download/3\.3\.5/Happ\.linux\.x64\.rpm\n"
            "2\. Установите Happ под ваш дистрибутив\n"
            "Debian/Ubuntu/Mint:\n"
            "`sudo apt install /полный/путь/к/файлу/имя_пакета\.deb`\n"
            "Fedora/AlmaLinux:\n"
            "`sudo dnf install /полный/путь/к/файлу/имя_пакета\.rpm`\n"
            "3\. Скопируйте вашу ссылку \(можно получить в личном кабинете\)\n"
            "4\. Нажмите на кнопку '\+', что\-бы добавить ссылку\n"
            "5\. Запустите VPN\n\n"
            "Готово \!"
        )
        photo = img_pc

    await callback.message.delete()
    await callback.message.answer_photo(
        photo=photo,
        caption=text,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="MarkdownV2"
    )
    await callback.answer()

# --- ЗАПУСК БОТА ---

async def main():
    scheduler.start_scheduler(bot)
    try:
        await dp.start_polling(bot)
    finally:
        await xui.close()

if __name__ == "__main__":
    asyncio.run(main())