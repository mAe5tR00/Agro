import os
import asyncio
import logging
import hashlib
import re
import json
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, BaseFilter, ChatMemberUpdatedFilter
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import LabeledPrice, PreCheckoutQuery, SuccessfulPayment, ChatJoinRequest, ChatMemberUpdated



# Загрузка переменных окружения
load_dotenv()

# ============================================
# НАСТРОЙКИ
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SECONDARY_CHAT_ID = os.getenv("SECONDARY_CHAT_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
DELAY_SECONDS = int(os.getenv("DELAY_SECONDS", 180))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", 5))

# Часовой пояс Астана
ASTANA_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))

# Путь к файлу сохранения
DATA_FILE = "vacancies.json"
SUBS_FILE = "subscriptions.json"

# Настройки подписки
MOMENTUM_PRO_CHANNEL_ID = -1003836921999
STARS_PRICE = 250
SUBSCRIPTION_DAYS = 30

# ============================================
# ЛОГИРОВАНИЕ
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_astana_time() -> datetime:
    """Получить текущее время в часовом поясе Астаны"""
    return datetime.now(ASTANA_TZ)

# ============================================
# УПРАВЛЕНИЕ ПОДПИСКАМИ
# ============================================

class SubscriptionManager:
    def __init__(self):
        self.subscriptions: Dict[str, str] = {}
        self.load_subs()

    def load_subs(self):
        """Загрузка подписок из файла"""
        if os.path.exists(SUBS_FILE):
            try:
                with open(SUBS_FILE, 'r', encoding='utf-8') as f:
                    self.subscriptions = json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки подписок: {e}")
                self.subscriptions = {}
        else:
            self.subscriptions = {}

    def save_subs(self):
        """Сохранение подписок в файл"""
        try:
            with open(SUBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.subscriptions, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Ошибка сохранения подписок: {e}")

    def add_subscription(self, user_id: int, days: int = SUBSCRIPTION_DAYS):
        """Добавить или продлить подписку"""
        user_id_str = str(user_id)
        now = get_astana_time()
        
        if user_id_str in self.subscriptions:
            current_expiry = datetime.fromisoformat(self.subscriptions[user_id_str])
            if current_expiry > now:
                new_expiry = current_expiry + timedelta(days=days)
            else:
                new_expiry = now + timedelta(days=days)
        else:
            new_expiry = now + timedelta(days=days)
            
        self.subscriptions[user_id_str] = new_expiry.isoformat()
        self.save_subs()
        return new_expiry

    def is_active(self, user_id: int) -> bool:
        """Проверить, активна ли подписка"""
        user_id_str = str(user_id)
        if user_id_str not in self.subscriptions:
            return False
            
        expiry = datetime.fromisoformat(self.subscriptions[user_id_str])
        return expiry > get_astana_time()

    def get_expiry(self, user_id: int) -> Optional[datetime]:
        """Получить дату истечения подписки"""
        user_id_str = str(user_id)
        if user_id_str in self.subscriptions:
            return datetime.fromisoformat(self.subscriptions[user_id_str])
        return None

# ============================================
# МОНИТОР ВАКАНСИЙ
# ============================================

class VacancyMonitor:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.previous_vacancies: Dict[str, Dict] = {}
        self.base_url = "https://agropraktika.eu"
        self.check_interval = CHECK_INTERVAL
        
        # Статистика
        self.start_time = get_astana_time()
        self.check_count = 0
        self.last_check_time = None
        self.running = True
        self.last_hourly_report = None
        
        # Загрузка данных
        self.load_data()

    def load_data(self):
        """Загрузка сохраненных вакансий из файла"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    self.previous_vacancies = json.load(f)
                logger.info(f"Загружено {len(self.previous_vacancies)} вакансий из {DATA_FILE}")
            except Exception as e:
                logger.error(f"Ошибка загрузки данных: {e}")
                self.previous_vacancies = {}
        else:
            self.previous_vacancies = {}

    def save_data(self):
        """Сохранение вакансий в файл"""
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.previous_vacancies, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")

    def format_uptime(self) -> str:
        """Форматирование времени работы"""
        uptime = get_astana_time() - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0: parts.append(f"{days} дн.")
        if hours > 0: parts.append(f"{hours} ч.")
        if minutes > 0: parts.append(f"{minutes} мин.")
        if not parts: parts.append(f"{seconds} сек.")
            
        return " ".join(parts)

    def get_status_message(self) -> str:
        """Генерация сообщения статуса"""
        current_time = get_astana_time()
        total_vacancies = len(self.previous_vacancies)
        suspended = sum(1 for v in self.previous_vacancies.values() 
                       if "приостановлена" in v.get('status', '').lower())
        active = total_vacancies - suspended
        
        if self.last_check_time:
            next_check = self.last_check_time + timedelta(seconds=self.check_interval)
            time_to_next = next_check - current_time
            if time_to_next.total_seconds() > 0:
                next_check_str = f"{int(time_to_next.total_seconds() // 60)} мин. {int(time_to_next.total_seconds() % 60)} сек."
            else:
                next_check_str = "скоро..."
        else:
            next_check_str = "ожидание..."
        
        return f"""
🤖 <b>Статус бота Momentum</b>

✅ <b>Статус:</b> Работает
⏰ <b>Время работы:</b> {self.format_uptime()}
📅 <b>Запущен:</b> {self.start_time.strftime('%H:%M:%S %d.%m.%Y')} (Астана)

📊 <b>Статистика:</b>
├ Вакансий отслеживается: <b>{total_vacancies}</b>
├ 🔴 Приостановлено: <b>{suspended}</b>
├ 🟢 Активных: <b>{active}</b>
└ #️⃣ Проверок выполнено: <b>{self.check_count}</b>

⏱ <b>Проверки:</b>
├ Последняя: {self.last_check_time.strftime('%H:%M:%S') if self.last_check_time else 'ещё не было'} (Астана)
├ Следующая через: {next_check_str}
└ Интервал: {self.check_interval // 60} мин.

🌐 <b>Сайт:</b> agropraktika.eu/vacancies
"""

    async def fetch_vacancies(self, session: aiohttp.ClientSession, page: int = 1) -> List[Dict]:
        """Асинхронное получение данных о вакансиях"""
        url = f"{self.base_url}/vacancies?page={page}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://agropraktika.eu/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        
        try:
            async with session.get(url, headers=headers, timeout=15) as response:
                if response.status != 200:
                    logger.error(f"Ошибка сайта {url}: {response.status}")
                    return []
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                vacancies = []
                # Поиск списка вакансий на основе "сайт.txt"
                vacancy_items = soup.select('ul.vacancies-list li.vacancy-item')
                
                for item in vacancy_items:
                    try:
                        # Ссылка и заголовок
                        link_tag = item.select_one('div.information a[href*="/vacancies/"]')
                        if not link_tag: continue
                        
                        link = link_tag['href']
                        if not link.startswith('http'):
                            link = f"{self.base_url}{link}"
                            
                        title = link_tag.get_text(strip=True)
                        
                        # Статус (на основе "сайт.txt")
                        status_tag = item.select_one('p.text-red-400')
                        status = status_tag.get_text(strip=True) if status_tag else "Регистрация открыта"
                        
                        # Местоположение
                        # В "сайт.txt" местоположение находится во втором блоке с svg
                        info_blocks = item.select('div.flex.flex-wrap div.flex.items-center')
                        location = info_blocks[1].get_text(strip=True) if len(info_blocks) > 1 else "Не указано"
                        
                        # Дата начала
                        date_tag = item.select_one('div.more-information div.italic')
                        start_date = ""
                        if date_tag:
                            date_match = re.search(r'(\d{2}/\d{2}/\d{4})', date_tag.get_text())
                            if date_match:
                                start_date = date_match.group(1)
                        
                        v_id = hashlib.md5(link.encode()).hexdigest()[:8]
                        
                        vacancies.append({
                            'id': v_id,
                            'title': title,
                            'location': location,
                            'start_date': start_date,
                            'status': status,
                            'link': link,
                            'last_checked': get_astana_time().isoformat()
                        })
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга отдельной вакансии: {e}")
                
                return vacancies
                
        except Exception as e:
            logger.error(f"Ошибка при запросе к {url}: {e}")
            return []

    async def check_all_pages(self, session: aiohttp.ClientSession) -> List[Dict]:
        """Проверка всех доступных страниц (до 10)"""
        all_vacancies = []
        for page in range(1, 11): # Проверяем до 10 страниц
            vacs = await self.fetch_vacancies(session, page)
            if not vacs: 
                break
            all_vacancies.extend(vacs)
            
            # Если вакансий мало на странице, значит это последняя страница
            if len(vacs) < 10:
                break
                
            await asyncio.sleep(1) # Небольшая задержка между страницами
        return all_vacancies

    async def monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info("Цикл мониторинга запущен")
        
        # Начальная загрузка, если список пуст
        if not self.previous_vacancies:
            async with aiohttp.ClientSession() as session:
                init_vacs = await self.check_all_pages(session)
                self.previous_vacancies = {v['id']: v for v in init_vacs}
                self.save_data()
                logger.info(f"Инициализация: сохранено {len(init_vacs)} вакансий")

        while self.running:
            try:
                self.last_check_time = get_astana_time()
                self.check_count += 1
                logger.info(f"Проверка #{self.check_count}...")
                
                async with aiohttp.ClientSession() as session:
                    current_vacancies = await self.check_all_pages(session)
                
                if not current_vacancies:
                    logger.warning("Не удалось получить данные о вакансиях")
                else:
                    await self.analyze_changes(current_vacancies)
                
                # Ежечасный отчет
                await self.check_hourly_report()
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(60)

    async def analyze_changes(self, current_vacancies: List[Dict]):
        """Анализ изменений и отправка уведомлений"""
        current_dict = {v['id']: v for v in current_vacancies}
        new_openings = []
        
        for v_id, vac in current_dict.items():
            if v_id not in self.previous_vacancies:
                # Новая вакансия
                if "приостановлена" not in vac['status'].lower():
                    new_openings.append(vac)
            else:
                # Изменение статуса
                old_status = self.previous_vacancies[v_id]['status']
                new_status = vac['status']
                
                if "приостановлена" in old_status.lower() and "приостановлена" not in new_status.lower():
                    new_openings.append(vac)
        
        # Отправляем уведомления
        for vac in new_openings:
            msg = f"""
🟢 <b>ВАЖНО: Регистрация открылась!</b>

🏷 <b>Вакансия:</b> {vac['title']}
📍 <b>Место:</b> {vac['location']}
🚀 <b>Начинается:</b> {vac['start_date']}

🔗 <a href="{vac['link']}">Скорее переходи по ссылке!</a>

🕐 Время: {get_astana_time().strftime('%H:%M:%S %d.%m.%Y')} (Астана)
"""
            # Основной канал - немедленно
            try:
                await self.bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode=ParseMode.HTML)
                logger.info(f"Отправлено уведомление в основной канал: {vac['title']}")
            except Exception as e:
                logger.error(f"Ошибка отправки в основной канал: {e}")

            # Дополнительный канал - с задержкой
            if SECONDARY_CHAT_ID:
                asyncio.create_task(self.delayed_notification(SECONDARY_CHAT_ID, msg, vac['title']))

        # Обновляем состояние
        self.previous_vacancies = current_dict
        self.save_data()

    async def delayed_notification(self, chat_id: str, message: str, vacancy_title: str):
        """Отправка уведомления с задержкой"""
        logger.info(f"Запланировано отложенное уведомление ({DELAY_SECONDS}с) для: {vacancy_title}")
        await asyncio.sleep(DELAY_SECONDS)
        try:
            await self.bot.send_message(chat_id, message, parse_mode=ParseMode.HTML)
            logger.info(f"Отправлено отложенное уведомление во второй канал: {vacancy_title}")
        except Exception as e:
            logger.error(f"Ошибка отправки отложенного уведомления: {e}")

    async def check_hourly_report(self):
        """Отправка ежечасного отчета только во второй канал (в Momentum Pro - только уведомления о вакансиях)"""
        now = get_astana_time()
        if self.last_hourly_report is None:
            self.last_hourly_report = now
            return

        if now - self.last_hourly_report >= timedelta(hours=1):
            report = self.get_status_message()

            # Отправка только во второй канал (в Momentum Pro отчеты не отправляем)
            if SECONDARY_CHAT_ID:
                try:
                    await self.bot.send_message(SECONDARY_CHAT_ID, report, parse_mode=ParseMode.HTML)
                    logger.info("Отправлен ежечасный отчет во второй канал")
                except Exception as e:
                    logger.error(f"Ошибка отправки отчета во второй канал: {e}")

            self.last_hourly_report = now

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================
router = Router()

# Фильтр для проверки ID админа
class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return str(message.from_user.id) == ADMIN_ID

@router.message(Command("start", "help"), IsAdmin())
async def cmd_help(message: types.Message):
    msg = """
🤖 <b>Бот мониторинга Momentum</b>

<b>Доступные команды:</b>
/status - Показать статус бота
/check - Запустить проверку вакансий сейчас
/help - Показать это сообщение

Бот автоматически проверяет сайт каждые 2 минуты и уведомляет об открытии регистрации.
"""
    await message.answer(msg, parse_mode=ParseMode.HTML)

# Обработчики для обычных пользователей (Momentum Pro)

@router.message(Command("start"))
async def cmd_start_public(message: types.Message, sub_manager: SubscriptionManager):
    """Публичная команда старт"""
    # Если админ, можно показать админ-панель или оставить как есть
    if str(message.from_user.id) == ADMIN_ID:
        await cmd_help(message)
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Momentum Pro", callback_data="buy_momentum_pro")
    
    status_text = ""
    if sub_manager.is_active(message.from_user.id):
        expiry = sub_manager.get_expiry(message.from_user.id)
        status_text = f"\n\n✅ <b>Ваша подписка активна до:</b> {expiry.strftime('%d.%m.%Y %H:%M')}"

    msg = f"""
👋 Приветствую в боте <b>Momentum</b>!

Здесь вы можете приобрести доступ в закрытый канал <b>Momentum Pro</b>.
{status_text}
Нажмите кнопку ниже, чтобы узнать подробности.
"""
    await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "buy_momentum_pro")
async def process_momentum_pro(callback: types.CallbackQuery):
    """Показ пользовательского соглашения"""
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Купить доступ (250 ⭐)", callback_data="pay_stars")
    kb.button(text="⬅️ Назад", callback_data="back_to_start")
    kb.adjust(1)

    agreement = """
📜 <b>Пользовательское соглашение (Momentum Pro)</b>

Покупая доступ, вы подтверждаете, что:
1. Оплата производится на добровольной основе.
2. Доступ предоставляется на 30 дней.
3. Вы ознакомлены с правилами канала.
4. Возврат средств за цифровые товары не предусмотрен политикой Telegram.

Стоимость доступа: <b>250 Telegram Stars</b>
Срок действия: <b>30 дней</b>
"""
    await callback.message.edit_text(agreement, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "back_to_start")
async def process_back_to_start(callback: types.CallbackQuery, sub_manager: SubscriptionManager):
    """Возврат в главное меню"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Momentum Pro", callback_data="buy_momentum_pro")
    
    status_text = ""
    if sub_manager.is_active(callback.from_user.id):
        expiry = sub_manager.get_expiry(callback.from_user.id)
        status_text = f"\n\n✅ <b>Ваша подписка активна до:</b> {expiry.strftime('%d.%m.%Y %H:%M')}"

    msg = f"""
👋 Приветствую в боте <b>Momentum</b>!

Здесь вы можете приобрести доступ в закрытый канал <b>Momentum Pro</b>.
{status_text}
Нажмите кнопку ниже, чтобы узнать подробности.
"""
    await callback.message.edit_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.callback_query(F.data == "pay_stars")
async def send_payment_invoice(callback: types.CallbackQuery, bot: Bot):
    """Отправка счета на оплату"""
    prices = [LabeledPrice(label="Momentum Pro (30 дней)", amount=STARS_PRICE)]
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Доступ в Momentum Pro",
        description="Подписка на закрытый канал на 30 дней",
        payload="momentum_pro_30_days",
        currency="XTR", # Код для Telegram Stars
        prices=prices,
        provider_token="" # Пусто для Telegram Stars
    )
    await callback.answer()

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Подтверждение перед оплатой"""
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: types.Message, sub_manager: SubscriptionManager, bot: Bot):
    """Обработка успешной оплаты"""
    if message.successful_payment.invoice_payload == "momentum_pro_30_days":
        expiry = sub_manager.add_subscription(message.from_user.id)
        
        kb = InlineKeyboardBuilder()
        # Ссылка на канал (нужно создать invite link если его нет, но здесь предполагается что пользователь подаст заявку)
        # В aiogram 3.x для получения ссылки на закрытый канал можно использовать bot.create_chat_invite_link
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=MOMENTUM_PRO_CHANNEL_ID,
                creates_join_request=True # Обязательно, чтобы бот видел заявку
            )
            kb.button(text="➡️ Подать заявку в канал", url=invite.invite_link)
        except Exception as e:
            logger.error(f"Ошибка создания ссылки: {e}")
            kb.button(text="➡️ Подать заявку", url="https://t.me/c/1003836921999/1") # Заглушка

        msg = f"""
🎉 <b>Оплата прошла успешно!</b>

Ваша подписка активирована до: <b>{expiry.strftime('%d.%m.%Y %H:%M')}</b>.
Теперь вы можете подать заявку на вступление в канал. Бот одобрит её автоматически.
"""
        await message.answer(msg, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@router.chat_join_request(F.chat.id == MOMENTUM_PRO_CHANNEL_ID)
async def handle_join_request(update: ChatJoinRequest, sub_manager: SubscriptionManager):
    """Автоматическое одобрение заявок"""
    user_id = update.from_user.id
    if sub_manager.is_active(user_id):
        try:
            await update.approve()
            logger.info(f"Одобрена заявка пользователя {user_id} в Momentum Pro")

            # Можно отправить приветственное сообщение в ЛС
            await update.bot.send_message(
                user_id,
                "✅ Ваша заявка в <b>Momentum Pro</b> одобрена! Добро пожаловать.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка одобрения заявки: {e}")
    else:
        # Если подписки нет — отклоняем заявку
        try:
            await update.decline()
            logger.warning(f"Отклонена заявка пользователя {user_id} (нет активной подписки)")
            
            # Уведомляем пользователя
            await update.bot.send_message(
                user_id,
                "❌ Ваша заявка отклонена.\n\n"
                "Возможно, ваша подписка истекла. Приобретите подписку заново через бота.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка при отклонении заявки {user_id}: {e}")

@router.my_chat_member(F.chat.id.in_([TELEGRAM_CHAT_ID, SECONDARY_CHAT_ID]))
async def handle_chat_member_update(event: ChatMemberUpdated, bot: Bot):
    """Удаление системных сообщений о вступлении/выходе пользователей"""
    try:
        chat_id = event.chat.id
        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status
        
        # Удаляем системные сообщения о вступлении, выходе, приглашении
        if old_status == ChatMemberStatus.LEFT and new_status == ChatMemberStatus.MEMBER:
            # Пользователь вступил
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"Удалено системное сообщение о вступлении в чате {chat_id}")
        elif old_status == ChatMemberStatus.MEMBER and new_status == ChatMemberStatus.LEFT:
            # Пользователь вышел
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"Удалено системное сообщение о выходе из чата {chat_id}")
        elif old_status == ChatMemberStatus.RESTRICTED and new_status == ChatMemberStatus.MEMBER:
            # Пользователь был разблокирован/возвращен
            await bot.delete_message(chat_id, event.message.message_id)
            logger.info(f"Удалено системное сообщение о возвращении в чат {chat_id}")
    except Exception as e:
        # Игнорируем ошибки (например, если сообщение уже удалено или нет прав)
        logger.debug(f"Не удалось удалить системное сообщение: {e}")

@router.message(Command("status"), IsAdmin())
async def cmd_status(message: types.Message, monitor: VacancyMonitor):
    await message.answer(monitor.get_status_message(), parse_mode=ParseMode.HTML)

@router.message(Command("check"), IsAdmin())
async def cmd_check(message: types.Message, monitor: VacancyMonitor):
    await message.answer("🔄 <b>Запускаю внеплановую проверку...</b>", parse_mode=ParseMode.HTML)
    
    async with aiohttp.ClientSession() as session:
        current = await monitor.check_all_pages(session)
    
    if current:
        total = len(current)
        suspended = sum(1 for v in current if "приостановлена" in v['status'].lower())
        active = total - suspended
        
        # Обновляем данные, чтобы учесть изменения если они есть
        await monitor.analyze_changes(current)
        
        await message.answer(f"✅ <b>Проверка завершена!</b>\n\nВсего: {total}\n🔴 Приостановлено: {suspended}\n🟢 Активных: {active}", parse_mode=ParseMode.HTML)
    else:
        await message.answer("⚠️ Не удалось получить данные с сайта", parse_mode=ParseMode.HTML)

# ============================================
# ЗАПУСК
# ============================================

async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ ОШИБКА: Проверьте .env файл на наличие TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
        return

    # Инициализация бота
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    
    # Создаем монитор и менеджер подписок
    monitor = VacancyMonitor(bot)
    sub_manager = SubscriptionManager()
    
    dp["monitor"] = monitor
    dp["sub_manager"] = sub_manager
    
    dp.include_router(router)
    

    logger.info("Бот запущен!")
    
    # Запуск монитора и бота одновременно
    await asyncio.gather(
        dp.start_polling(bot),
        monitor.monitor_loop()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено.")
