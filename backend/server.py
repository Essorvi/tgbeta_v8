from fastapi import FastAPI, APIRouter, HTTPException, Request, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import requests
import json
import hashlib
import secrets
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
import re

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# API Configuration
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
WEBHOOK_SECRET = os.environ['WEBHOOK_SECRET']
USERSBOX_TOKEN = os.environ['USERSBOX_TOKEN']
USERSBOX_BASE_URL = os.environ['USERSBOX_BASE_URL']
CRYPTOBOT_TOKEN = os.environ['CRYPTOBOT_TOKEN']
CRYPTOBOT_BASE_URL = os.environ['CRYPTOBOT_BASE_URL']
ADMIN_USERNAME = os.environ['ADMIN_USERNAME']
REQUIRED_CHANNEL = os.environ['REQUIRED_CHANNEL']
BOT_USERNAME = os.environ.get('BOT_USERNAME', 'search1_test_bot')

# Create the main app
app = FastAPI(title="УЗРИ - Telegram Bot API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Models
class User(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    balance: float = 0.0  # Баланс в рублях
    subscription_type: Optional[str] = None  # "day", "3days", "month", None
    subscription_expires: Optional[datetime] = None
    daily_searches_used: int = 0  # Использованные поиски за день
    daily_searches_reset: datetime = Field(default_factory=datetime.utcnow)
    referred_by: Optional[int] = None
    referral_code: str
    total_referrals: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_admin: bool = False
    last_active: datetime = Field(default_factory=datetime.utcnow)
    is_subscribed: bool = False

class Subscription(BaseModel):
    user_id: int
    subscription_type: str  # "day", "3days", "month"
    price: float
    started_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    max_daily_searches: int = 12

class Payment(BaseModel):
    user_id: int
    amount: float
    payment_type: str  # "crypto", "stars", "admin"
    payment_id: Optional[str] = None
    status: str = "pending"  # "pending", "completed", "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Search(BaseModel):
    user_id: int
    query: str
    search_type: str
    results: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cost: float = 25.0
    success: bool = True
    payment_method: str = "balance"  # "balance", "subscription"

class UserState(BaseModel):
    user_id: int
    state: str  # "waiting_custom_amount_stars", "waiting_custom_amount_crypto"
    data: Optional[Dict[str, Any]] = None  # дополнительные данные
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Referral(BaseModel):
    referrer_id: int
    referred_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    confirmed: bool = False  # Подтвержден ли реферал (подписался ли на канал)

# Helper Functions
def generate_referral_code(telegram_id: int) -> str:
    """Generate unique referral code"""
    data = f"{telegram_id}_{secrets.token_hex(8)}"
    return hashlib.md5(data.encode()).hexdigest()[:8]

def detect_search_type(query: str) -> str:
    """Detect search type based on query pattern"""
    query = query.strip()
    
    phone_patterns = [
        r'^\+?[7-8]\d{10}$',
        r'^\+?\d{10,15}$',
        r'^[7-8]\(\d{3}\)\d{3}-?\d{2}-?\d{2}$'
    ]
    
    for pattern in phone_patterns:
        if re.match(pattern, query.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')):
            return "📱 Телефон"
    
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', query):
        return "📧 Email"
    
    if re.match(r'^[АВЕКМНОРСТУХ]\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', query.upper().replace(' ', '')):
        return "🚗 Автомобиль"
    
    if query.startswith('@') or re.match(r'^[a-zA-Z0-9_]+$', query):
        return "🆔 Никнейм"
    
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
        return "🌐 IP-адрес"
    
    address_keywords = ['улица', 'ул', 'проспект', 'пр', 'переулок', 'пер', 'дом', 'д', 'квартира', 'кв']
    if any(keyword in query.lower() for keyword in address_keywords):
        return "🏠 Адрес"
    
    words = query.split()
    if 2 <= len(words) <= 3 and all(re.match(r'^[а-яА-ЯёЁa-zA-Z]+$', word) for word in words):
        return "👤 ФИО"
    
    return "🔍 Общий поиск"

def create_main_menu():
    """Create main menu keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "🔍 Поиск", "callback_data": "menu_search"},
                {"text": "👤 Профиль", "callback_data": "menu_profile"}
            ],
            [
                {"text": "💰 Баланс", "callback_data": "menu_balance"},
                {"text": "🛒 Тарифы", "callback_data": "menu_pricing"}
            ],
            [
                {"text": "🔗 Рефералы", "callback_data": "menu_referral"},
                {"text": "❓ Помощь", "callback_data": "menu_help"}
            ],
            [
                {"text": "📋 Правила", "callback_data": "menu_rules"},
                {"text": "💎 Купить поиск (25₽)", "callback_data": "buy_single_search"}
            ]
        ]
    }

def create_admin_menu():
    """Create admin menu keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "💎 Начислить баланс", "callback_data": "admin_add_balance"},
                {"text": "📊 Статистика", "callback_data": "admin_stats"}
            ],
            [
                {"text": "👥 Пользователи", "callback_data": "admin_users"},
                {"text": "💳 Платежи", "callback_data": "admin_payments"}
            ],
            [
                {"text": "◀️ Главное меню", "callback_data": "back_to_menu"}
            ]
        ]
    }

def create_balance_menu():
    """Create balance menu keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "🤖 Криптобот", "callback_data": "pay_crypto"},
                {"text": "⭐ Звезды", "callback_data": "pay_stars"}
            ],
            [
                {"text": "🛒 Купить поиск (25₽)", "callback_data": "buy_single_search"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back_to_menu"}
            ]
        ]
    }

def create_pricing_menu():
    """Create pricing menu keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "📅 1д (149₽)", "callback_data": "buy_day_sub"},
                {"text": "📅 3д (299₽)", "callback_data": "buy_3days_sub"}
            ],
            [
                {"text": "📅 1мес (1700₽)", "callback_data": "buy_month_sub"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "back_to_menu"}
            ]
        ]
    }

def create_back_keyboard():
    """Create back button keyboard"""
    return {
        "inline_keyboard": [
            [{"text": "◀️ Назад в меню", "callback_data": "back_to_menu"}]
        ]
    }

def create_subscription_keyboard():
    """Create subscription check keyboard"""
    return {
        "inline_keyboard": [
            [
                {"text": "📢 Подписаться на канал", "url": "https://t.me/uzrisebya"}
            ],
            [
                {"text": "✅ Проверить подписку", "callback_data": "check_subscription"}
            ]
        ]
    }

async def check_daily_limit_reset(user: User) -> User:
    """Check if daily search limit should be reset"""
    now = datetime.utcnow()
    if now.date() > user.daily_searches_reset.date():
        await db.users.update_one(
            {"telegram_id": user.telegram_id},
            {
                "$set": {
                    "daily_searches_used": 0,
                    "daily_searches_reset": now
                }
            }
        )
        user.daily_searches_used = 0
        user.daily_searches_reset = now
    return user

async def has_active_subscription(user: User) -> bool:
    """Check if user has active subscription"""
    if not user.subscription_expires:
        return False
    return datetime.utcnow() < user.subscription_expires

async def can_search(user: User) -> tuple[bool, str]:
    """Check if user can perform search"""
    # Admin always can search
    if user.is_admin:
        return True, ""
    
    # Check subscription first
    if await has_active_subscription(user):
        user = await check_daily_limit_reset(user)
        if user.daily_searches_used >= 12:
            return False, "превышен дневной лимит подписки (12 поисков)"
        return True, "subscription"
    
    # Check balance for single search
    if user.balance >= 25.0:
        return True, "balance"
    
    return False, "недостаточно средств"

async def usersbox_request(endpoint: str, params: Dict = None) -> Dict:
    """Make request to usersbox API"""
    headers = {"Authorization": USERSBOX_TOKEN}
    url = f"{USERSBOX_BASE_URL}{endpoint}"
    
    try:
        response = requests.get(url, headers=headers, params=params or {}, timeout=30)
        return response.json()
    except Exception as e:
        logging.error(f"Usersbox API error: {e}")
        return {"status": "error", "error": {"message": str(e)}}

def format_search_results(results: Dict[str, Any], query: str, search_type: str) -> str:
    """Format usersbox API results for Telegram"""
    if results.get('status') == 'error':
        return f"❌ *Ошибка:* {results.get('error', {}).get('message', 'Неизвестная ошибка')}"

    data = results.get('data', {})
    total_count = data.get('count', 0)
    
    if total_count == 0:
        return f"🔍 *Поиск:* `{query}`\n{search_type}\n\n❌ *Результатов не найдено*\n\n💡 *Попробуйте изменить формат запроса*"
    
    formatted_text = f"🎯 *РЕЗУЛЬТАТЫ ПОИСКА*\n\n"
    formatted_text += f"🔍 *Запрос:* `{query}`\n"
    formatted_text += f"📂 *Тип:* {search_type}\n"
    formatted_text += f"📊 *Найдено:* {total_count} записей\n\n"

    if 'items' in data and isinstance(data['items'], list):
        formatted_text += "📋 *ДАННЫЕ ИЗ БАЗ:*\n\n"
        
        for i, source_data in enumerate(data['items'][:5], 1):
            if 'source' in source_data and 'hits' in source_data:
                source = source_data['source']
                hits = source_data['hits']
                hits_count = hits.get('hitsCount', hits.get('count', 0))
                
                db_names = {
                    'yandex': '🟡 Яндекс',
                    'avito': '🟢 Авито',
                    'vk': '🔵 ВКонтакте',
                    'ok': '🟠 Одноклассники',
                    'delivery_club': '🍕 Delivery Club',
                    'cdek': '📦 СДЭК'
                }
                
                db_display = db_names.get(source.get('database', ''), f"📊 {source.get('database', 'N/A')}")
                
                formatted_text += f"*{i}. {db_display}*\n"
                formatted_text += f"📁 База: {source.get('collection', 'N/A')}\n"
                formatted_text += f"🔢 Записей: {hits_count}\n"

                if 'items' in hits and hits['items']:
                    formatted_text += "💾 *Данные:*\n"
                    for item in hits['items'][:2]:
                        for key, value in item.items():
                            if key.startswith('_'):
                                continue
                            
                            if key in ['phone', 'телефон', 'tel', 'mobile']:
                                formatted_text += f"📞 {value}\n"
                            elif key in ['email', 'почта', 'mail', 'e_mail']:
                                formatted_text += f"📧 {value}\n"
                            elif key in ['full_name', 'name', 'имя', 'фио', 'first_name', 'last_name']:
                                formatted_text += f"👤 {value}\n"
                            elif key in ['birth_date', 'birthday', 'дата_рождения', 'bdate']:
                                formatted_text += f"🎂 {value}\n"
                            elif key in ['address', 'адрес', 'city', 'город']:
                                formatted_text += f"🏠 {value}\n"
                            elif key in ['sex', 'gender', 'пол']:
                                gender_map = {'1': 'Ж', '2': 'М', 'male': 'М', 'female': 'Ж'}
                                formatted_text += f"⚥ {gender_map.get(str(value), value)}\n"
                
                formatted_text += "\n"

    formatted_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    formatted_text += "🔒 *Конфиденциальность:* Используйте данные ответственно"
    
    return formatted_text

async def check_subscription(user_id: int) -> bool:
    """Check if user is subscribed to required channel"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatMember"
        params = {
            "chat_id": REQUIRED_CHANNEL,
            "user_id": user_id
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                status = data.get('result', {}).get('status')
                return status in ['member', 'administrator', 'creator']
        
        return False
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False

async def send_telegram_message(chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup: dict = None) -> bool:
    """Send message to Telegram user"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")
        return False

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None, referral_code: str = None) -> tuple[User, bool]:
    """Get existing user or create new one. Returns (user, is_new_user)"""
    user_data = await db.users.find_one({"telegram_id": telegram_id})
    
    if user_data:
        await db.users.update_one(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "last_active": datetime.utcnow(),
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name
                }
            }
        )
        return User(**user_data), False
    else:
        referral_code_generated = generate_referral_code(telegram_id)
        is_admin = username == ADMIN_USERNAME if username else False
        
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            referral_code=referral_code_generated,
            is_admin=is_admin,
            balance=0.0  # Новые пользователи без денег
        )
        
        await db.users.insert_one(user.dict())
        
        # Process referral for new user
        if referral_code:
            await process_referral(telegram_id, referral_code)
        
        return user, True

# API Routes
@api_router.get("/")
async def root():
    return {"message": "УЗРИ - Telegram Bot API", "status": "running"}

@api_router.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """Handle Telegram webhook"""
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    
    try:
        update_data = await request.json()
        await handle_telegram_update(update_data)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

async def handle_callback_query(callback_query: Dict[str, Any]):
    """Handle callback queries from inline keyboard buttons"""
    chat_id = callback_query.get('message', {}).get('chat', {}).get('id')
    user_id = callback_query.get('from', {}).get('id')
    data = callback_query.get('data')
    callback_query_id = callback_query.get('id')
    
    # Answer callback query
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_query_id}, timeout=5)
    except:
        pass
    
    user, is_new_user = await get_or_create_user(
        telegram_id=user_id,
        username=callback_query.get('from', {}).get('username'),
        first_name=callback_query.get('from', {}).get('first_name'),
        last_name=callback_query.get('from', {}).get('last_name')
    )
    
    # Handle different callback data
    if data == "check_subscription":
        await handle_subscription_check(chat_id, user_id)
    elif data == "back_to_menu":
        await show_main_menu(chat_id, user)
    elif data == "menu_search":
        await show_search_menu(chat_id, user)
    elif data == "menu_profile":
        await show_profile_menu(chat_id, user)
    elif data == "menu_balance":
        await show_balance_menu(chat_id, user)
    elif data == "menu_pricing":
        await show_pricing_menu(chat_id, user)
    elif data == "menu_referral":
        await show_referral_menu(chat_id, user)
    elif data == "menu_help":
        await show_help_menu(chat_id, user)
    elif data == "menu_rules":
        await show_rules_menu(chat_id, user)
    elif data.startswith("admin_") and user.is_admin:
        await handle_admin_callback(chat_id, user, data)
    elif data.startswith("pay_"):
        await handle_payment_callback(chat_id, user, data)
    elif data.startswith("buy_"):
        await handle_purchase_callback(chat_id, user, data)
    elif data.startswith("crypto_"):
        if "_btc" in data or "_eth" in data or "_usdt" in data or "_ltc" in data:
            if data.count("_") >= 3:  # crypto_btc_100 format
                parts = data.split("_")
                crypto_type = parts[1]
                amount = parts[2]
                if amount == "custom":
                    await handle_crypto_custom_amount(chat_id, user, crypto_type)
                else:
                    await handle_crypto_payment_amount(chat_id, user, crypto_type, amount)
            else:  # crypto_btc format
                crypto_type = data.split("_")[1]
                await handle_crypto_payment(chat_id, user, crypto_type)
    elif data.startswith("stars_"):
        amount = data.split("_")[1]
        if amount == "custom":
            await handle_stars_custom_amount(chat_id, user)
        else:
            await handle_stars_payment(chat_id, user, amount)

async def handle_subscription_check(chat_id: int, user_id: int):
    """Handle subscription check"""
    is_subscribed = await check_subscription(user_id)
    if is_subscribed:
        await db.users.update_one(
            {"telegram_id": user_id},
            {"$set": {"is_subscribed": True}}
        )
        
        # Confirm referral if exists
        await confirm_referral(user_id)
        
        await send_telegram_message(
            chat_id,
            "✅ *Подписка подтверждена!*\n\n🎉 Теперь вы можете пользоваться сервисом!"
        )
        
        # Get user and show main menu
        user_data = await db.users.find_one({"telegram_id": user_id})
        if user_data:
            user = User(**user_data)
            await show_main_menu(chat_id, user)
    else:
        await send_telegram_message(
            chat_id,
            "❌ *Подписка не найдена*\n\n📢 Подпишитесь на канал @uzrisebya и попробуйте снова",
            reply_markup=create_subscription_keyboard()
        )

async def confirm_referral(user_id: int):
    """Confirm referral when user subscribes to channel"""
    try:
        referral = await db.referrals.find_one({"referred_id": user_id, "confirmed": False})
        if referral:
            # Mark referral as confirmed
            await db.referrals.update_one(
                {"_id": referral["_id"]},
                {"$set": {"confirmed": True}}
            )
            
            # Give 1 search attempt (25₽ equivalent) to referrer
            await db.users.update_one(
                {"telegram_id": referral["referrer_id"]},
                {"$inc": {"balance": 25.0}}
            )
            
            # Notify referrer
            await send_telegram_message(
                referral["referrer_id"],
                f"🎉 *Подтвержденный реферал!*\n\n🔍 На ваш счет зачислена 1 попытка поиска\n💰 (эквивалент 25₽)"
            )
            
            logging.info(f"Referral confirmed: referrer {referral['referrer_id']}, referred {user_id}")
    except Exception as e:
        logging.error(f"Error confirming referral: {e}")

async def show_main_menu(chat_id: int, user: User):
    """Show main menu"""
    welcome_text = f"🎯 *СЕРВИС УЗРИ - ПОИСК ДАННЫХ*\n\n"
    welcome_text += f"👋 Добро пожаловать, {user.first_name or 'пользователь'}!\n\n"
    
    # Подробное описание возможностей бота
    welcome_text += f"🔍 *ЧТО УМЕЕТ НАШИХ БОТ:*\n\n"
    
    welcome_text += f"📊 *ПОИСК ПО 1000+ БАЗАМ ДАННЫХ:*\n"
    welcome_text += f"📱 Телефоны: +79123456789\n"
    welcome_text += f"📧 Email: user@mail.ru\n"
    welcome_text += f"👤 ФИО: Иван Петров Сергеевич\n"
    welcome_text += f"🚗 Автономера: А123ВС777\n"
    welcome_text += f"🆔 Никнеймы: @username\n"
    welcome_text += f"🌐 IP-адреса: 192.168.1.1\n"
    welcome_text += f"🏠 Адреса и геолокация\n\n"
    
    welcome_text += f"🗄️ *ИСТОЧНИКИ ДАННЫХ:*\n"
    welcome_text += f"🟡 Яндекс (Еда, Такси, Карты)\n"
    welcome_text += f"🟢 Авито (объявления, пользователи)\n"
    welcome_text += f"🔵 ВКонтакте (профили)\n"
    welcome_text += f"🟠 Одноклассники\n"
    welcome_text += f"📦 СДЭК (доставка)\n"
    welcome_text += f"🍕 Delivery Club и многие другие\n\n"
    
    # Важная информация о бесплатном пробиве
    welcome_text += f"🎁 *БЕСПЛАТНЫЕ ПРОБИВЫ:*\n"
    welcome_text += f"За каждого одобренного реферала получите 1 бесплатную попытку пробива данных!\n\n"
    
    # Show subscription status
    if await has_active_subscription(user):
        expires = user.subscription_expires.strftime('%d.%m.%Y %H:%M')
        welcome_text += f"✅ *Подписка активна до:* {expires}\n"
        user = await check_daily_limit_reset(user)
        welcome_text += f"🔍 *Поисков сегодня:* {user.daily_searches_used}/12\n\n"
    else:
        welcome_text += f"💰 *Баланс:* {user.balance:.2f} ₽\n"
        searches_available = int(user.balance // 25)
        welcome_text += f"🔍 *Доступно поисков:* {searches_available}\n\n"
    
    welcome_text += f"👥 *Рефералов:* {user.total_referrals}\n\n"
    
    # Информация о тарифах без цен
    welcome_text += f"💳 *ДОСТУПНЫЕ ТАРИФЫ:*\n"
    welcome_text += f"• Разовые поиски\n"
    welcome_text += f"• Подписка на 1 день\n"
    welcome_text += f"• Подписка на 3 дня\n"
    welcome_text += f"• Подписка на 1 месяц\n\n"
    
    welcome_text += f"🔍 *Выберите действие:*"
    
    if user.is_admin:
        # Show admin menu for eriksson_sop
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔍 Поиск", "callback_data": "menu_search"},
                    {"text": "👤 Профиль", "callback_data": "menu_profile"}
                ],
                [
                    {"text": "💰 Баланс", "callback_data": "menu_balance"},
                    {"text": "🛒 Тарифы", "callback_data": "menu_pricing"}
                ],
                [
                    {"text": "🔗 Рефералы", "callback_data": "menu_referral"},
                    {"text": "❓ Помощь", "callback_data": "menu_help"}
                ],
                [
                    {"text": "📋 Правила", "callback_data": "menu_rules"},
                    {"text": "💎 Купить поиск (25₽)", "callback_data": "buy_single_search"}
                ],
                [
                    {"text": "👑 АДМИН-ПАНЕЛЬ", "callback_data": "admin_panel"}
                ]
            ]
        }
    else:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔍 Поиск", "callback_data": "menu_search"},
                    {"text": "👤 Профиль", "callback_data": "menu_profile"}
                ],
                [
                    {"text": "💰 Баланс", "callback_data": "menu_balance"},
                    {"text": "🛒 Тарифы", "callback_data": "menu_pricing"}
                ],
                [
                    {"text": "🔗 Рефералы", "callback_data": "menu_referral"},
                    {"text": "❓ Помощь", "callback_data": "menu_help"}
                ],
                [
                    {"text": "📋 Правила", "callback_data": "menu_rules"},
                    {"text": "💎 Купить поиск (25₽)", "callback_data": "buy_single_search"}
                ]
            ]
        }
    
    await send_telegram_message(chat_id, welcome_text, reply_markup=keyboard)

async def show_search_menu(chat_id: int, user: User):
    """Show search menu"""
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            await send_telegram_message(
                chat_id,
                "🔒 *Для поиска нужна подписка!*\n\n📢 Подпишитесь на @uzrisebya",
                reply_markup=create_subscription_keyboard()
            )
            return
    
    can_search_result, payment_method = await can_search(user)
    
    if not can_search_result and not user.is_admin:
        if "превышен дневной лимит" in payment_method:
            search_text = f"⏰ *ДНЕВНОЙ ЛИМИТ ИСЧЕРПАН*\n\n"
            search_text += f"📅 Вы использовали все 12 поисков по подписке на сегодня\n"
            search_text += f"🕒 Попробуйте завтра или купите отдельные поиски"
        else:
            search_text = f"💰 *НЕДОСТАТОЧНО СРЕДСТВ*\n\n"
            search_text += f"💳 Ваш баланс: {user.balance:.2f} ₽\n"
            search_text += f"💎 Нужно: 25 ₽ за поиск\n\n"
            search_text += f"💡 Пополните баланс или оформите подписку"
        
        await send_telegram_message(chat_id, search_text, reply_markup=create_back_keyboard())
        return
    
    search_text = f"🔍 *ПОИСК ПО БАЗАМ ДАННЫХ*\n\n"
    
    if user.is_admin:
        search_text += f"👑 *Режим администратора*\n"
    elif await has_active_subscription(user):
        user = await check_daily_limit_reset(user)
        search_text += f"✅ *Активная подписка*\n"
        search_text += f"🔍 *Поисков сегодня:* {user.daily_searches_used}/12\n"
    else:
        search_text += f"💰 *Баланс:* {user.balance:.2f} ₽\n"
        searches_available = int(user.balance // 25)
        search_text += f"🔍 *Доступно поисков:* {searches_available}\n"
    
    search_text += f"\n📝 *ТИПЫ ПОИСКА:*\n"
    search_text += f"📱 Телефон: +79123456789, 89123456789\n"
    search_text += f"📧 Email: user@mail.ru, admin@site.com\n"
    search_text += f"👤 ФИО: Иван Петров, Анна Сергеевна\n"
    search_text += f"🚗 Авто: А123ВС777, В456ОР199\n"
    search_text += f"🆔 Никнейм: @username, nickname\n"
    search_text += f"🌐 IP: 192.168.1.1, 8.8.8.8\n"
    search_text += f"🏠 Адрес: улица Ленина дом 5\n\n"
    
    search_text += f"🗄️ *ИСТОЧНИКИ (1000+ БАЗ):*\n"
    search_text += f"🟡 Яндекс • 🟢 Авито • 🔵 ВКонтакте\n"
    search_text += f"🟠 Одноклассники • 📦 СДЭК • 🍕 Delivery Club\n"
    search_text += f"📊 И многие другие сервисы\n\n"
    
    search_text += f"➡️ *Просто отправьте данные для поиска*"
    
    await send_telegram_message(chat_id, search_text, reply_markup=create_back_keyboard())

async def show_profile_menu(chat_id: int, user: User):
    """Show profile menu"""
    total_searches = await db.searches.count_documents({"user_id": user.telegram_id})
    successful_searches = await db.searches.count_documents({"user_id": user.telegram_id, "success": True})
    
    profile_text = f"👤 *ВАШ ПРОФИЛЬ*\n\n"
    profile_text += f"🆔 *ID:* `{user.telegram_id}`\n"
    profile_text += f"👤 *Имя:* {user.first_name or 'N/A'}\n"
    profile_text += f"🔗 *Username:* @{user.username or 'N/A'}\n\n"
    
    profile_text += f"💰 *ФИНАНСЫ:*\n"
    profile_text += f"💳 Баланс: {user.balance:.2f} ₽\n"
    
    if await has_active_subscription(user):
        sub_type_names = {"day": "1 день", "3days": "3 дня", "month": "1 месяц"}
        sub_name = sub_type_names.get(user.subscription_type, user.subscription_type)
        expires = user.subscription_expires.strftime('%d.%m.%Y %H:%M')
        profile_text += f"✅ Подписка: {sub_name} до {expires}\n"
        user = await check_daily_limit_reset(user)
        profile_text += f"🔍 Поисков сегодня: {user.daily_searches_used}/12\n"
    else:
        profile_text += f"❌ Подписка: Нет\n"
    
    profile_text += f"\n📊 *СТАТИСТИКА:*\n"
    profile_text += f"🔍 Поисков: {total_searches}\n"
    profile_text += f"✅ Успешных: {successful_searches}\n"
    profile_text += f"👥 Рефералов: {user.total_referrals}\n"
    profile_text += f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y')}\n\n"
    
    if user.is_admin:
        profile_text += f"👑 *Статус:* АДМИНИСТРАТОР\n"
    
    await send_telegram_message(chat_id, profile_text, reply_markup=create_back_keyboard())

async def show_balance_menu(chat_id: int, user: User):
    """Show balance menu"""
    balance_text = f"💰 *ВАШ БАЛАНС*\n\n"
    balance_text += f"💳 *Текущий баланс:* {user.balance:.2f} ₽\n"
    
    searches_available = int(user.balance // 25)
    balance_text += f"🔍 *Доступно поисков:* {searches_available}\n\n"
    
    balance_text += f"💡 *СПОСОБЫ ПОПОЛНЕНИЯ:*\n"
    balance_text += f"🤖 Криптобот - автоматически\n"
    balance_text += f"⭐ Звезды Telegram - мгновенно\n\n"
    balance_text += f"💎 *Минимальное пополнение:* 100 ₽\n"
    balance_text += f"🔍 *Один поиск:* 25 ₽\n\n"
    balance_text += f"💼 *Или оформите подписку для экономии!*"
    
    await send_telegram_message(chat_id, balance_text, reply_markup=create_balance_menu())

async def show_pricing_menu(chat_id: int, user: User):
    """Show pricing menu"""
    pricing_text = f"🛒 *ТАРИФЫ И ПОДПИСКИ*\n\n"
    
    pricing_text += f"💎 *РАЗОВЫЕ ПОИСКИ:*\n"
    pricing_text += f"🔍 1 поиск = 25 ₽\n"
    pricing_text += f"💡 Идеально для разового использования\n\n"
    
    pricing_text += f"⭐ *ВЫГОДНЫЕ ПОДПИСКИ*:\n\n"
    
    pricing_text += f"📅 *1 ДЕНЬ - 149 ₽*\n"
    pricing_text += f"• До 12 поисков в день\n"
    pricing_text += f"• Экономия: 151 ₽ (по сравнению с разовыми)\n"
    pricing_text += f"• Цена за поиск: ~12₽\n\n"
    
    pricing_text += f"📅 *3 ДНЯ - 299 ₽* 🔥\n"
    pricing_text += f"• До 36 поисков за 3 дня\n"
    pricing_text += f"• Экономия: 601 ₽\n"
    pricing_text += f"• Цена за поиск: ~8₽\n\n"
    
    pricing_text += f"📅 *1 МЕСЯЦ - 1700 ₽* 💎\n"
    pricing_text += f"• До 360 поисков за месяц\n"
    pricing_text += f"• Экономия: 7300 ₽\n"
    pricing_text += f"• Цена за поиск: ~5₽\n\n"
    
    pricing_text += f"🎁 *БЕСПЛАТНО:*\n"
    pricing_text += f"• Приглашайте друзей и получайте бесплатные поиски!\n"
    pricing_text += f"• 1 одобренный реферал = 1 бесплатная попытка\n\n"
    
    pricing_text += f"💡 *Чем больше тариф, тем больше экономия!*"
    
    await send_telegram_message(chat_id, pricing_text, reply_markup=create_pricing_menu())

async def show_referral_menu(chat_id: int, user: User):
    """Show referral menu"""
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user.referral_code}"
    confirmed_referrals = await db.referrals.count_documents({"referrer_id": user.telegram_id, "confirmed": True})
    
    referral_text = f"🔗 *РЕФЕРАЛЬНАЯ ПРОГРАММА*\n\n"
    referral_text += f"🔍 *За подтвержденного реферала:* +1 попытка поиска\n"
    referral_text += f"📋 *Условие:* реферал должен подписаться на @uzrisebya\n\n"
    
    referral_text += f"📊 *ВАША СТАТИСТИКА:*\n"
    referral_text += f"👥 Всего приглашено: {user.total_referrals}\n"
    referral_text += f"✅ Подтверждено: {confirmed_referrals}\n"
    referral_text += f"🔍 Получено попыток: {confirmed_referrals}\n\n"
    
    referral_text += f"🔗 *ВАША ССЫЛКА:*\n"
    referral_text += f"`{referral_link}`\n\n"
    
    referral_text += f"📱 *Как это работает:*\n"
    referral_text += f"1. Поделитесь ссылкой\n"
    referral_text += f"2. Друг переходит и регистрируется\n"
    referral_text += f"3. Друг подписывается на @uzrisebya\n"
    referral_text += f"4. Вам начисляется 1 попытка поиска"
    
    await send_telegram_message(chat_id, referral_text, reply_markup=create_back_keyboard())

async def show_help_menu(chat_id: int, user: User):
    """Show help menu"""
    help_text = f"❓ *СПРАВКА И ПОДДЕРЖКА*\n\n"
    help_text += f"🎯 *О СЕРВИСЕ:*\n"
    help_text += f"УЗРИ помогает найти информацию о людях из открытых источников интернета.\n\n"
    
    help_text += f"💰 *ТАРИФЫ:*\n"
    help_text += f"🔍 Разовый поиск: 25 ₽\n"
    help_text += f"📅 Подписки: от 149 ₽/день\n\n"
    
    help_text += f"💳 *ПОПОЛНЕНИЕ:*\n"
    help_text += f"🤖 Криптобот\n"
    help_text += f"⭐ Звезды Telegram\n"
    help_text += f"💎 Минимум: 100 ₽\n\n"
    
    help_text += f"🔗 *РЕФЕРАЛЫ:*\n"
    help_text += f"🔍 1 попытка поиска за подтвержденного реферала\n\n"
    
    help_text += f"📞 *ПОДДЕРЖКА:*\n"
    help_text += f"@Sigicara - техническая поддержка\n\n"
    
    help_text += f"⚖️ *ВАЖНО:*\n"
    help_text += f"Перед использованием изучите правила сервиса"
    
    await send_telegram_message(chat_id, help_text, reply_markup=create_back_keyboard())

async def show_rules_menu(chat_id: int, user: User):
    """Show rules menu"""
    rules_text = f"📋 *ПРАВИЛА ИСПОЛЬЗОВАНИЯ СЕРВИСА*\n\n"
    
    rules_text += f"*1. СОГЛАСИЕ С ПРАВИЛАМИ*\n"
    rules_text += f"Используя данный бот, вы полностью подтверждаете согласие со всеми правилами сервиса.\n\n"
    
    rules_text += f"*2. НАЗНАЧЕНИЕ СЕРВИСА*\n"
    rules_text += f"• Поиск информации о себе в открытых источниках\n"
    rules_text += f"• Проверка утечек персональных данных\n"
    rules_text += f"• Анализ цифрового следа\n\n"
    
    rules_text += f"*3. ЗАПРЕЩАЕТСЯ*\n"
    rules_text += f"• Поиск данных без согласия владельца\n"
    rules_text += f"• Использование для мошенничества\n"
    rules_text += f"• Нарушение законов РФ\n"
    rules_text += f"• Продажа полученной информации\n"
    rules_text += f"• Преследование и шантаж\n\n"
    
    rules_text += f"*4. ТАРИФИКАЦИЯ*\n"
    rules_text += f"• Разовый поиск: 25 ₽\n"
    rules_text += f"• Подписки с лимитом 12 поисков/день\n"
    rules_text += f"• Минимальное пополнение: 100 ₽\n"
    rules_text += f"• Возврат средств не предусмотрен\n\n"
    
    rules_text += f"*5. ОТВЕТСТВЕННОСТЬ*\n"
    rules_text += f"• Администрация не несет ответственности за использование данных\n"
    rules_text += f"• Пользователь самостоятельно отвечает за свои действия\n"
    rules_text += f"• При нарушении правил - блокировка аккаунта\n\n"
    
    rules_text += f"*6. ТЕХНИЧЕСКАЯ ПОДДЕРЖКА*\n"
    rules_text += f"@Sigicara - техническая поддержка\n\n"
    
    rules_text += f"⚖️ *Используя сервис, вы подтверждаете согласие с данными правилами.*"
    
    await send_telegram_message(chat_id, rules_text, reply_markup=create_back_keyboard())

async def handle_admin_callback(chat_id: int, user: User, data: str):
    """Handle admin callbacks"""
    if data == "admin_panel":
        admin_text = f"👑 *АДМИН-ПАНЕЛЬ*\n\n"
        admin_text += f"🔧 Управление сервисом УЗРИ\n\n"
        admin_text += f"💎 *Начислить баланс* - добавить деньги пользователю\n"
        admin_text += f"📊 *Статистика* - общая статистика сервиса\n"
        admin_text += f"👥 *Пользователи* - список активных пользователей\n"
        admin_text += f"💳 *Платежи* - история транзакций"
        
        await send_telegram_message(chat_id, admin_text, reply_markup=create_admin_menu())
    
    elif data == "admin_add_balance":
        await send_telegram_message(
            chat_id,
            "💎 *НАЧИСЛЕНИЕ БАЛАНСА*\n\nОтправьте сообщение в формате:\n`ID СУММА`\n\nПример: `123456789 100`",
            reply_markup=create_back_keyboard()
        )
    
    elif data == "admin_stats":
        total_users = await db.users.count_documents({})
        total_searches = await db.searches.count_documents({})
        total_revenue = await db.searches.aggregate([
            {"$group": {"_id": None, "total": {"$sum": "$cost"}}}
        ]).to_list(1)
        active_subs = await db.users.count_documents({"subscription_expires": {"$gt": datetime.utcnow()}})
        
        stats_text = f"📊 *СТАТИСТИКА СЕРВИСА*\n\n"
        stats_text += f"👥 Пользователей: {total_users}\n"
        stats_text += f"🔍 Поисков: {total_searches}\n"
        stats_text += f"⭐ Активных подписок: {active_subs}\n"
        revenue = total_revenue[0]['total'] if total_revenue else 0
        stats_text += f"💰 Выручка: {revenue:.2f} ₽"
        
        await send_telegram_message(chat_id, stats_text, reply_markup=create_admin_menu())

async def handle_payment_callback(chat_id: int, user: User, data: str):
    """Handle payment callbacks"""
    if data == "pay_crypto":
        # Криптобот пополнение
        crypto_text = f"🤖 *ПОПОЛНЕНИЕ ЧЕРЕЗ КРИПТОБОТ*\n\n"
        crypto_text += f"💰 *Доступные способы:*\n"
        crypto_text += f"₿ Bitcoin (BTC)\n"
        crypto_text += f"💎 Ethereum (ETH)\n"  
        crypto_text += f"💰 USDT (TRC-20/ERC-20)\n"
        crypto_text += f"🔸 Litecoin (LTC)\n\n"
        crypto_text += f"📋 *Как пополнить:*\n"
        crypto_text += f"1. Выберите сумму и валюту\n"
        crypto_text += f"2. Получите адрес кошелька\n"
        crypto_text += f"3. Переведите средства\n"
        crypto_text += f"4. Средства поступят автоматически\n\n"
        crypto_text += f"⚡ *Минимальная сумма:* 100 ₽\n"
        crypto_text += f"🚀 *Зачисление:* 1-30 минут\n\n"
        crypto_text += f"📞 *Поддержка:* @Sigicara"
        
        crypto_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "₿ Bitcoin", "callback_data": "crypto_btc"},
                    {"text": "💎 Ethereum", "callback_data": "crypto_eth"}
                ],
                [
                    {"text": "💰 USDT", "callback_data": "crypto_usdt"},
                    {"text": "🔸 Litecoin", "callback_data": "crypto_ltc"}
                ],
                [
                    {"text": "◀️ Назад", "callback_data": "menu_balance"}
                ]
            ]
        }
        
        await send_telegram_message(chat_id, crypto_text, reply_markup=crypto_keyboard)
    
    elif data == "pay_stars":
        # Telegram Stars пополнение
        stars_text = f"⭐ *ПОПОЛНЕНИЕ ЗВЕЗДАМИ TELEGRAM*\n\n"
        stars_text += f"💫 *Быстро и удобно!*\n"
        stars_text += f"Используйте звезды Telegram для мгновенного пополнения баланса\n\n"
        stars_text += f"💰 *Курс обмена:*\n"
        stars_text += f"1 ⭐ = 2 ₽\n\n"
        stars_text += f"🎯 *Варианты пополнения:*\n\n"
        
        stars_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "50⭐ = 100₽", "callback_data": "stars_100"},
                    {"text": "125⭐ = 250₽", "callback_data": "stars_250"}
                ],
                [
                    {"text": "250⭐ = 500₽", "callback_data": "stars_500"},
                    {"text": "500⭐ = 1000₽", "callback_data": "stars_1000"}
                ],
                [
                    {"text": "1000⭐ = 2000₽", "callback_data": "stars_2000"}
                ],
                [
                    {"text": "💰 Своя сумма", "callback_data": "stars_custom"}
                ],
                [
                    {"text": "◀️ Назад", "callback_data": "menu_balance"}
                ]
            ]
        }
        
        await send_telegram_message(chat_id, stars_text, reply_markup=stars_keyboard)
    
    elif data == "buy_single_search":
        if user.balance >= 25.0:
            await send_telegram_message(
                chat_id,
                "✅ *У вас уже есть средства для поиска*\n\n🔍 Перейдите в раздел 'Поиск'",
                reply_markup=create_back_keyboard()
            )
        else:
            needed = 25.0 - user.balance
            await send_telegram_message(
                chat_id,
                f"💳 *ПОКУПКА ПОИСКА*\n\n💎 Нужно доплатить: {needed:.2f} ₽\n\n💡 Пополните баланс на сумму от 100 ₽",
                reply_markup=create_balance_menu()
            )

async def handle_crypto_payment_amount(chat_id: int, user: User, crypto_type: str, amount: str):
    """Handle crypto payment with specific amount"""
    crypto_names = {
        "btc": "Bitcoin (BTC)",
        "eth": "Ethereum (ETH)", 
        "usdt": "USDT",
        "ltc": "Litecoin (LTC)"
    }
    
    try:
        amount_float = float(amount)
        if amount_float < 100:
            await send_telegram_message(
                chat_id,
                "❌ Минимальная сумма пополнения: 100 ₽",
                reply_markup=create_back_keyboard()
            )
            return
            
        # Here you would integrate with your crypto payment processor
        # For now, we'll just show a mock wallet address
        wallet_text = f"💰 *ПОПОЛНЕНИЕ ЧЕРЕЗ {crypto_names.get(crypto_type, crypto_type.upper())}*\n\n"
        wallet_text += f"💎 Сумма: {amount_float} ₽\n\n"
        wallet_text += f"📋 *Адрес для перевода:*\n`bc1q...`\n\n"  # Replace with actual wallet
        wallet_text += f"⚡ *Статус:* Ожидание оплаты\n"
        wallet_text += f"⏱️ *Зачисление:* 1-30 минут\n\n"
        wallet_text += f"📞 *Поддержка:* @Sigicara"
        
        await send_telegram_message(chat_id, wallet_text, reply_markup=create_back_keyboard())
        
    except ValueError:
        await send_telegram_message(
            chat_id,
            "❌ Неверная сумма",
            reply_markup=create_back_keyboard()
        )

async def handle_crypto_payment(chat_id: int, user: User, crypto_type: str):
    """Handle crypto payment selection"""
    crypto_names = {
        "btc": "Bitcoin (BTC)",
        "eth": "Ethereum (ETH)", 
        "usdt": "USDT",
        "ltc": "Litecoin (LTC)"
    }
    
    crypto_text = f"💰 *ПОПОЛНЕНИЕ ЧЕРЕЗ {crypto_names.get(crypto_type, crypto_type.upper())}*\n\n"
    crypto_text += f"📝 *Выберите сумму для пополнения:*\n"
    crypto_text += f"После выбора вы получите адрес кошелька для перевода\n\n"
    crypto_text += f"⚡ *Зачисление: 1-30 минут*"
    
    crypto_amounts_keyboard = {
        "inline_keyboard": [
            [
                {"text": "100₽", "callback_data": f"crypto_{crypto_type}_100"},
                {"text": "250₽", "callback_data": f"crypto_{crypto_type}_250"}
            ],
            [
                {"text": "500₽", "callback_data": f"crypto_{crypto_type}_500"},
                {"text": "1000₽", "callback_data": f"crypto_{crypto_type}_1000"}
            ],
            [
                {"text": "2000₽", "callback_data": f"crypto_{crypto_type}_2000"},
                {"text": "5000₽", "callback_data": f"crypto_{crypto_type}_5000"}
            ],
            [
                {"text": "💰 Своя сумма", "callback_data": f"crypto_{crypto_type}_custom"}
            ],
            [
                {"text": "◀️ Назад", "callback_data": "pay_crypto"}
            ]
        ]
    }
    
    await send_telegram_message(chat_id, crypto_text, reply_markup=crypto_amounts_keyboard)

async def handle_stars_custom_amount(chat_id: int, user: User):
    """Handle custom amount for Telegram Stars payment"""
    await set_user_state(user.telegram_id, "waiting_custom_amount_stars")
    
    text = f"💰 *СВОЯ СУММА ПОПОЛНЕНИЯ*\n\n"
    text += f"⭐ *Курс:* 1 звезда = 2₽\n\n"
    text += f"📝 Введите сумму в рублях (от 100₽ до 50,000₽)\n"
    text += f"💡 Сумма должна быть кратна 50₽\n\n"
    text += f"❌ Для отмены нажмите кнопку"
    
    await send_telegram_message(
        chat_id,
        text,
        reply_markup=create_back_keyboard()
    )
async def handle_crypto_custom_amount(chat_id: int, user: User, crypto_type: str):
    """Handle custom amount for crypto payment"""
    crypto_names = {
        "btc": "Bitcoin (BTC)",
        "eth": "Ethereum (ETH)", 
        "usdt": "USDT",
        "ltc": "Litecoin (LTC)"
    }
    
    await set_user_state(user.telegram_id, "waiting_custom_amount_crypto", {"crypto_type": crypto_type})
    
    text = f"💰 *СВОЯ СУММА ПОПОЛНЕНИЯ*\n\n"
    text += f"🤖 *Валюта:* {crypto_names.get(crypto_type, crypto_type.upper())}\n\n"
    text += f"📝 Введите сумму в рублях (от 100₽ до 50,000₽)\n"
    text += f"💡 Сумма должна быть кратна 50₽\n\n"
    text += f"❌ Для отмены нажмите кнопку"
    
    await send_telegram_message(
        chat_id,
        text,
        reply_markup=create_back_keyboard()
    )

async def handle_stars_payment(chat_id: int, user: User, amount: str):
    """Handle Telegram Stars payment"""
    amounts = {
        "100": (50, 100),
        "250": (125, 250),
        "500": (250, 500),
        "1000": (500, 1000),
        "2000": (1000, 2000)
    }
    
    if amount not in amounts:
        return
        
    stars_needed, rubles = amounts[amount]
    
    # Create Telegram Stars invoice
    try:
        invoice_payload = f"stars_payment_{user.telegram_id}_{amount}"
        
        # Создаем инвойс для оплаты звездами
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendInvoice"
        invoice_data = {
            "chat_id": chat_id,
            "title": f"Пополнение баланса на {rubles}₽",
            "description": f"Пополнение баланса сервиса УЗРИ через Telegram Stars",
            "payload": invoice_payload,
            "currency": "XTR",  # Telegram Stars currency
            "prices": [{"label": f"Пополнение {rubles}₽", "amount": stars_needed}]
        }
        
        response = requests.post(url, json=invoice_data)
        if response.status_code == 200:
            await send_telegram_message(
                chat_id,
                f"⭐ *ОПЛАТА ЗВЕЗДАМИ*\n\n💰 Сумма: {rubles}₽\n⭐ К оплате: {stars_needed} звезд\n\n👆 Нажмите кнопку выше для оплаты"
            )
        else:
            await send_telegram_message(
                chat_id,
                "❌ Ошибка создания счета. Обратитесь в поддержку @Sigicara",
                reply_markup=create_back_keyboard()
            )
    except Exception as e:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при создании инвойса. Попробуйте позже или обратитесь в поддержку @Sigicara",
            reply_markup=create_back_keyboard()
        )

async def handle_purchase_callback(chat_id: int, user: User, data: str):
    """Handle subscription purchase callbacks"""
    prices = {
        "buy_day_sub": (149.0, "day", 1),
        "buy_3days_sub": (299.0, "3days", 3),
        "buy_month_sub": (1700.0, "month", 30)
    }
    
    if data in prices:
        price, sub_type, days = prices[data]
        
        if user.balance >= price:
            # Purchase subscription
            expires = datetime.utcnow() + timedelta(days=days)
            
            await db.users.update_one(
                {"telegram_id": user.telegram_id},
                {
                    "$set": {
                        "subscription_type": sub_type,
                        "subscription_expires": expires,
                        "daily_searches_used": 0,
                        "daily_searches_reset": datetime.utcnow()
                    },
                    "$inc": {"balance": -price}
                }
            )
            
            sub_names = {"day": "1 день", "3days": "3 дня", "month": "1 месяц"}
            await send_telegram_message(
                chat_id,
                f"🎉 *ПОДПИСКА ОФОРМЛЕНА!*\n\n⭐ Тариф: {sub_names[sub_type]}\n💰 Списано: {price} ₽\n📅 Действует до: {expires.strftime('%d.%m.%Y %H:%M')}\n\n🔍 Доступно до 12 поисков в день!",
                reply_markup=create_main_menu()
            )
        else:
            needed = price - user.balance
            await send_telegram_message(
                chat_id,
                f"❌ *НЕДОСТАТОЧНО СРЕДСТВ*\n\n💰 Ваш баланс: {user.balance:.2f} ₽\n💎 Нужно: {price} ₽\n📈 Доплатить: {needed:.2f} ₽",
                reply_markup=create_balance_menu()
            )

async def handle_custom_stars_amount_input(chat_id: int, user: User, text: str):
    """Handle custom amount input for stars payment"""
    await clear_user_state(user.telegram_id)
    
    is_valid, error_msg, amount = validate_custom_amount(text)
    if not is_valid:
        await send_telegram_message(
            chat_id,
            f"❌ *Ошибка:* {error_msg}",
            reply_markup=create_back_keyboard()
        )
        return
    
    stars_needed = int(amount / 2)  # 1 star = 2₽
    
    # Create Telegram Stars invoice
    try:
        invoice_payload = f"stars_payment_{user.telegram_id}_{amount}"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendInvoice"
        invoice_data = {
            "chat_id": chat_id,
            "title": f"Пополнение баланса на {amount}₽",
            "description": f"Пополнение баланса сервиса УЗРИ через Telegram Stars",
            "payload": invoice_payload,
            "currency": "XTR",  # Telegram Stars currency
            "prices": [{"label": f"Пополнение {amount}₽", "amount": stars_needed}]
        }
        
        response = requests.post(url, json=invoice_data)
        if response.status_code == 200:
            await send_telegram_message(
                chat_id,
                f"⭐ *ОПЛАТА ЗВЕЗДАМИ*\n\n💰 Сумма: {amount}₽\n⭐ К оплате: {stars_needed} звезд\n\n👆 Нажмите кнопку выше для оплаты"
            )
        else:
            await send_telegram_message(
                chat_id,
                "❌ Ошибка создания счета. Обратитесь в поддержку @Sigicara",
                reply_markup=create_back_keyboard()
            )
    except Exception as e:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при создании инвойса. Попробуйте позже или обратитесь в поддержку @Sigicara",
            reply_markup=create_back_keyboard()
        )

async def handle_custom_crypto_amount_input(chat_id: int, user: User, text: str, crypto_type: str):
    """Handle custom amount input for crypto payment"""
    await clear_user_state(user.telegram_id)
    
    is_valid, error_msg, amount = validate_custom_amount(text)
    if not is_valid:
        await send_telegram_message(
            chat_id,
            f"❌ *Ошибка:* {error_msg}",
            reply_markup=create_back_keyboard()
        )
        return
    
    await handle_crypto_payment_amount(chat_id, user, crypto_type, str(amount))


async def handle_pre_checkout_query(pre_checkout_query: Dict[str, Any]):
    """Handle pre-checkout query from Telegram Stars payments"""
    try:
        query_id = pre_checkout_query.get('id')
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerPreCheckoutQuery"
        
        # Always approve the payment at this stage
        payload = {
            "pre_checkout_query_id": query_id,
            "ok": True
        }
        
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logging.error(f"Failed to answer pre-checkout query: {response.text}")
            
    except Exception as e:
        logging.error(f"Error handling pre-checkout query: {e}")

async def handle_successful_payment(message: Dict[str, Any]):
    """Handle successful payment notification"""
    try:
        payment_info = message.get('successful_payment', {})
        chat_id = message.get('chat', {}).get('id')
        
        if not payment_info or not chat_id:
            return
            
        # Extract payment details
        total_amount = float(payment_info.get('total_amount', 0)) / 100  # Convert from kopeks to rubles
        payload = payment_info.get('invoice_payload', '')
        
        # Parse payload (format: "stars_payment_USER_ID_AMOUNT")
        parts = payload.split('_')
        if len(parts) >= 4 and parts[0] == 'stars' and parts[1] == 'payment':
            user_id = int(parts[2])
            amount = float(parts[3])
            
            # Create payment record
            payment = Payment(
                user_id=user_id,
                amount=amount,
                payment_type="stars",
                payment_id=payment_info.get('telegram_payment_charge_id'),
                status="completed"
            )
            
            # Save payment to database
            await db.payments.insert_one(payment.dict())
            
            # Update user balance
            await db.users.update_one(
                {"telegram_id": user_id},
                {"$inc": {"balance": amount}}
            )
            
            # Send confirmation message
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            message_text = (
                f"✅ *Оплата успешно проведена!*\n\n"
                f"💰 Сумма: {amount} ₽\n"
                f"💫 Баланс пополнен\n\n"
                f"🔍 Теперь вы можете пользоваться поиском!"
            )
            
            payload = {
                "chat_id": chat_id,
                "text": message_text,
                "parse_mode": "Markdown"
            }
            
            requests.post(url, json=payload)
            
    except Exception as e:
        logging.error(f"Error handling successful payment: {e}")
async def handle_telegram_update(update_data: Dict[str, Any]):
    """Process incoming Telegram update"""
    # Handle pre_checkout_query for Telegram Stars payments
    pre_checkout_query = update_data.get('pre_checkout_query')
    if pre_checkout_query:
        await handle_pre_checkout_query(pre_checkout_query)
        return
    
    callback_query = update_data.get('callback_query')
    if callback_query:
        await handle_callback_query(callback_query)
        return
    
    message = update_data.get('message')
    if not message:
        return
    
    # Handle successful payment notifications
    if message.get('successful_payment'):
        await handle_successful_payment(message)
        return

    chat_id = message.get('chat', {}).get('id')
    text = message.get('text', '')
    user_info = message.get('from', {})
    
    if not chat_id:
        return

    # Get or create user for all message types
    user, is_new_user = await get_or_create_user(
        telegram_id=user_info.get('id', chat_id),
        username=user_info.get('username'),
        first_name=user_info.get('first_name'),
        last_name=user_info.get('last_name')
    )

    # Check if user is waiting for custom amount input
    user_state = await get_user_state(user.telegram_id)
    if user_state:
        if user_state.state == "waiting_custom_amount_stars":
            await handle_custom_stars_amount_input(chat_id, user, text)
            return
        elif user_state.state == "waiting_custom_amount_crypto":
            crypto_type = user_state.data.get("crypto_type", "btc")
            await handle_custom_crypto_amount_input(chat_id, user, text, crypto_type)
            return

    # Handle /start command
    if text.startswith('/start'):
        parts = text.split()
        referral_code = None
        if len(parts) > 1:
            referral_code = parts[1]
            # Process referral for existing user
            await process_referral(user.telegram_id, referral_code)
        
        if not user.is_admin:
            is_subscribed = await check_subscription(user.telegram_id)
            if not is_subscribed:
                await send_telegram_message(
                    chat_id,
                    f"🎯 *ДОБРО ПОЖАЛОВАТЬ В УЗРИ!*\n\n🔒 *Для использования сервиса подпишитесь на канал @uzrisebya*",
                    reply_markup=create_subscription_keyboard()
                )
                return
        
        await show_main_menu(chat_id, user)
    
    # Handle admin balance commands
    elif user.is_admin and ' ' in text and text.split()[0].isdigit():
        parts = text.split()
        if len(parts) == 2:
            try:
                target_id = int(parts[0])
                amount = float(parts[1])
                
                result = await db.users.update_one(
                    {"telegram_id": target_id},
                    {"$inc": {"balance": amount}}
                )
                
                if result.modified_count > 0:
                    await send_telegram_message(
                        chat_id,
                        f"✅ *Баланс начислен*\n\n👤 ID: {target_id}\n💰 Сумма: {amount} ₽"
                    )
                    
                    await send_telegram_message(
                        target_id,
                        f"🎁 *Начисление баланса*\n\n💰 На ваш счет зачислено: {amount} ₽\n\n💡 Теперь вы можете пользоваться сервисом!"
                    )
                else:
                    await send_telegram_message(chat_id, "❌ Пользователь не найден")
            except:
                await send_telegram_message(chat_id, "❌ Неверный формат команды")
    
    # Handle search queries
    else:
        await handle_search_query(chat_id, text, user)

async def handle_search_query(chat_id: int, query: str, user: User):
    """Handle search query"""
    if not user.is_admin:
        is_subscribed = await check_subscription(user.telegram_id)
        if not is_subscribed:
            await send_telegram_message(
                chat_id,
                "🔒 Для поиска нужна подписка на @uzrisebya",
                reply_markup=create_subscription_keyboard()
            )
            return
    
    can_search_result, payment_method = await can_search(user)
    
    if not can_search_result and not user.is_admin:
        if "превышен дневной лимит" in payment_method:
            await send_telegram_message(
                chat_id,
                "⏰ *Дневной лимит исчерпан*\n\nВы использовали все 12 поисков по подписке на сегодня",
                reply_markup=create_main_menu()
            )
        else:
            await send_telegram_message(
                chat_id,
                f"💰 *Недостаточно средств*\n\nДля поиска нужно 25 ₽\nВаш баланс: {user.balance:.2f} ₽",
                reply_markup=create_balance_menu()
            )
        return
    
    search_type = detect_search_type(query)
    
    await send_telegram_message(
        chat_id,
        f"🔍 *Выполняю поиск...*\n{search_type}\n⏱️ Подождите..."
    )
    
    try:
        results = await usersbox_request("/search", {"q": query})
        
        formatted_results = format_search_results(results, query, search_type)
        await send_telegram_message(chat_id, formatted_results, reply_markup=create_main_menu())
        
        # Process payment and save search
        cost = 0.0
        if not user.is_admin:
            if await has_active_subscription(user):
                await db.users.update_one(
                    {"telegram_id": user.telegram_id},
                    {"$inc": {"daily_searches_used": 1}}
                )
                payment_method = "subscription"
            else:
                cost = 25.0
                await db.users.update_one(
                    {"telegram_id": user.telegram_id},
                    {"$inc": {"balance": -25.0}}
                )
                payment_method = "balance"
        
        search = Search(
            user_id=user.telegram_id,
            query=query,
            search_type=search_type,
            results=results,
            success=results.get('status') == 'success',
            cost=cost,
            payment_method=payment_method
        )
        await db.searches.insert_one(search.dict())
    
    except Exception as e:
        await send_telegram_message(
            chat_id,
            "❌ Ошибка при выполнении поиска. Попробуйте позже.",
            reply_markup=create_main_menu()
        )

async def set_user_state(user_id: int, state: str, data: Dict[str, Any] = None):
    """Set user state for custom input"""
    user_state = UserState(
        user_id=user_id,
        state=state,
        data=data or {}
    )
    # Удаляем старое состояние если есть
    await db.user_states.delete_many({"user_id": user_id})
    # Добавляем новое состояние
    await db.user_states.insert_one(user_state.dict())

async def get_user_state(user_id: int) -> Optional[UserState]:
    """Get user state"""
    state_data = await db.user_states.find_one({"user_id": user_id})
    if state_data:
        return UserState(**state_data)
    return None

async def clear_user_state(user_id: int):
    """Clear user state"""
    await db.user_states.delete_many({"user_id": user_id})

def validate_custom_amount(amount_str: str) -> tuple[bool, str, float]:
    """Validate custom amount input"""
    try:
        amount = float(amount_str)
        
        if amount < 100:
            return False, "Минимальная сумма: 100₽", 0
        
        if amount % 50 != 0:
            return False, "Сумма должна быть кратна 50₽", 0
            
        if amount > 50000:
            return False, "Максимальная сумма: 50,000₽", 0
            
        return True, "", amount
        
    except ValueError:
        return False, "Введите корректную сумму (только цифры)", 0

async def create_cryptobot_invoice(amount: float, currency: str = "RUB") -> Dict[str, Any]:
    """Create CryptoBot invoice"""
    try:
        url = f"{CRYPTOBOT_BASE_URL}/createInvoice"
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        
        payload = {
            "currency_type": "fiat",
            "fiat": currency,
            "amount": str(amount),
            "description": f"Пополнение баланса УЗРИ на {amount}₽",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/search1_test_bot"
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        return response.json()
        
    except Exception as e:
        logging.error(f"CryptoBot API error: {e}")
        return {"ok": False, "error": {"message": str(e)}}

async def handle_pre_checkout_query(pre_checkout_query: Dict[str, Any]):
    """Handle pre-checkout query for Telegram Stars payments"""
    query_id = pre_checkout_query.get('id')
    user_id = pre_checkout_query.get('from', {}).get('id')
    invoice_payload = pre_checkout_query.get('invoice_payload', '')
    
    try:
        # Always approve the pre-checkout query for valid Stars payments
        if invoice_payload.startswith('stars_payment_'):
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerPreCheckoutQuery"
            data = {
                "pre_checkout_query_id": query_id,
                "ok": True
            }
            requests.post(url, json=data, timeout=10)
            logging.info(f"Pre-checkout approved for user {user_id}")
        else:
            # Reject invalid payments
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerPreCheckoutQuery"
            data = {
                "pre_checkout_query_id": query_id,
                "ok": False,
                "error_message": "Неверный платеж"
            }
            requests.post(url, json=data, timeout=10)
            logging.warning(f"Pre-checkout rejected for user {user_id}: invalid payload")
    except Exception as e:
        logging.error(f"Error handling pre-checkout query: {e}")

async def handle_successful_payment(message: Dict[str, Any]):
    """Handle successful payment notification"""
    payment_info = message.get('successful_payment', {})
    user_id = message.get('from', {}).get('id')
    chat_id = message.get('chat', {}).get('id')
    
    # Extract payment details
    total_amount = payment_info.get('total_amount', 0)  # In stars
    currency = payment_info.get('currency', 'XTR')
    invoice_payload = payment_info.get('invoice_payload', '')
    
    try:
        # Process Stars payment
        if currency == 'XTR' and invoice_payload.startswith('stars_payment_'):
            # Extract amount from payload: stars_payment_{user_id}_{amount}
            payload_parts = invoice_payload.split('_')
            if len(payload_parts) >= 3:
                ruble_amount = float(payload_parts[2])
            else:
                ruble_amount = total_amount * 2  # 1 star = 2 rubles
            
            # Update user balance
            result = await db.users.update_one(
                {"telegram_id": user_id},
                {"$inc": {"balance": ruble_amount}}
            )
            
            if result.modified_count > 0:
                # Save payment record
                payment = Payment(
                    user_id=user_id,
                    amount=ruble_amount,
                    payment_type="stars",
                    payment_id=payment_info.get('telegram_payment_charge_id'),
                    status="completed"
                )
                await db.payments.insert_one(payment.dict())
                
                # Send notification to user
                notification_text = f"🎉 *ПОПОЛНЕНИЕ УСПЕШНО!*\n\n"
                notification_text += f"⭐ *Способ:* Telegram Stars\n"
                notification_text += f"💰 *Сумма:* {ruble_amount}₽\n"
                notification_text += f"⭐ *Звезд потрачено:* {total_amount}\n\n"
                notification_text += f"✅ *Средства зачислены на баланс*\n"
                notification_text += f"🔍 *Теперь вы можете пользоваться сервисом!*"
                
                await send_telegram_message(
                    chat_id,
                    notification_text,
                    reply_markup=create_main_menu()
                )
                
                logging.info(f"Stars payment processed: {ruble_amount}₽ for user {user_id}")
            else:
                logging.error(f"Failed to update balance for user {user_id}")
        else:
            logging.warning(f"Unknown payment type: currency={currency}, payload={invoice_payload}")
            
    except Exception as e:
        logging.error(f"Error processing successful payment: {e}")
        
        # Send error notification to user
        await send_telegram_message(
            chat_id,
            "❌ *Ошибка обработки платежа*\n\nОбратитесь в поддержку @Sigicara",
            reply_markup=create_back_keyboard()
        )

async def process_referral(referred_user_id: int, referral_code: str) -> bool:
    """Process referral"""
    try:
        referrer = await db.users.find_one({"referral_code": referral_code})
        if not referrer or referrer['telegram_id'] == referred_user_id:
            return False

        existing_referral = await db.referrals.find_one({
            "referrer_id": referrer['telegram_id'],
            "referred_id": referred_user_id
        })
        if existing_referral:
            return False

        referral = Referral(
            referrer_id=referrer['telegram_id'],
            referred_id=referred_user_id,
            confirmed=False
        )
        await db.referrals.insert_one(referral.dict())

        await db.users.update_one(
            {"telegram_id": referrer['telegram_id']},
            {"$inc": {"total_referrals": 1}}
        )

        await send_telegram_message(
            referrer['telegram_id'],
            f"👥 *Новый реферал!*\n\nПользователь перешел по вашей ссылке\n🔍 1 попытка поиска будет начислена после подписки на канал"
        )

        return True
    except Exception as e:
        logging.error(f"Referral processing error: {e}")
        return False

# API endpoints
@api_router.get("/users")
async def get_users():
    """Get all users"""
    users = await db.users.find().to_list(1000)
    for user in users:
        user["_id"] = str(user["_id"])
    return users

@api_router.get("/stats")
async def get_stats():
    """Get bot statistics"""
    total_users = await db.users.count_documents({})
    total_searches = await db.searches.count_documents({})
    total_referrals = await db.referrals.count_documents({})
    active_subs = await db.users.count_documents({"subscription_expires": {"$gt": datetime.utcnow()}})

    return {
        "total_users": total_users,
        "total_searches": total_searches,
        "total_referrals": total_referrals,
        "active_subscriptions": active_subs
    }

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()