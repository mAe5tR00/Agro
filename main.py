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

from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command, BaseFilter
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keep_alive import keep_alive

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
        """Отправка ежечасного отчета в оба канала"""
        now = get_astana_time()
        if self.last_hourly_report is None:
            self.last_hourly_report = now
            return
            
        if now - self.last_hourly_report >= timedelta(hours=1):
            report = self.get_status_message()
            
            # Отправка в основной канал
            try:
                await self.bot.send_message(TELEGRAM_CHAT_ID, report, parse_mode=ParseMode.HTML)
                logger.info("Отправлен ежечасный отчет в основной канал")
            except Exception as e:
                logger.error(f"Ошибка отправки отчета в основной канал: {e}")
                
            # Отправка во второй канал
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
    
    # Создаем монитор и передаем его в диспетчер как зависимость
    monitor = VacancyMonitor(bot)
    dp["monitor"] = monitor
    
    dp.include_router(router)
    
    # Запускаем Flask в отдельном потоке (если нужно для Replit/KeepAlive)
    keep_alive()
    
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
