import os
import time
import logging
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import hashlib
import re
import threading

# ============================================
# НАСТРОЙКИ - МЕНЯЙТЕ ЗДЕСЬ!
# ============================================

# ВАШ ТОКЕН БОТА (получите у @BotFather)
TELEGRAM_BOT_TOKEN = "6046846295:AAFc_8p-xRxuSxEg7-3f_VGKYiKZWIFBS5w"

# ID вашего чата (куда отправлять уведомления)
TELEGRAM_CHAT_ID = "-1003526159260"

# Интервал проверки в секундах (рекомендуется 300 = 5 минут)
CHECK_INTERVAL = 300

# Часовой пояс (Астана = UTC+5)
TIMEZONE_OFFSET = 5  # часов от UTC

# ============================================
# КОНЕЦ НАСТРОЕК
# ============================================

# Часовой пояс Астана
ASTANA_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))

def get_astana_time() -> datetime:
    """Получить текущее время в часовом поясе Астаны"""
    return datetime.now(ASTANA_TZ)

# Проверка настроек
if TELEGRAM_BOT_TOKEN == "ВАШ_ТОКЕН_БОТА_ЗДЕСЬ" or TELEGRAM_CHAT_ID == "ВАШ_ID_ЧАТА_ЗДЕСЬ":
    print("❌ ОШИБКА: Замените TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID на ваши значения!")
    print("1. Получите токен у @BotFather в Telegram")
    print("2. Вставьте его в код вместо 'ВАШ_ТОКЕН_БОТА_ЗДЕСЬ'")
    print("3. Укажите ваш chat_id вместо 'ВАШ_ID_ЧАТА_ЗДЕСЬ'")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VacancyMonitor:
    def __init__(self):
        self.previous_vacancies: Dict[str, Dict] = {}
        self.base_url = "https://agropraktika.eu"
        self.check_interval = CHECK_INTERVAL
        self.telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        
        # Статистика для команды /status
        self.start_time = get_astana_time()
        self.check_count = 0
        self.last_check_time = None
        self.last_update_id = 0
        self.running = True
        self.last_hourly_report = None  # Для ежечасного отчёта
        
    def format_uptime(self) -> str:
        """Форматирование времени работы"""
        uptime = get_astana_time() - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days} дн.")
        if hours > 0:
            parts.append(f"{hours} ч.")
        if minutes > 0:
            parts.append(f"{minutes} мин.")
        if not parts:
            parts.append(f"{seconds} сек.")
            
        return " ".join(parts)

    def send_telegram_message(self, message: str, chat_id: str = None) -> bool:
        """Отправка сообщения через HTTP API (работает на любом хостинге)"""
        try:
            url = f"{self.telegram_api_url}/sendMessage"
            data = {
                'chat_id': chat_id or TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                logger.info("Сообщение отправлено")
                return True
            else:
                logger.error(f"Ошибка отправки: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False

    def get_updates(self) -> List[Dict]:
        """Получение новых сообщений от пользователей"""
        try:
            url = f"{self.telegram_api_url}/getUpdates"
            params = {
                'offset': self.last_update_id + 1,
                'timeout': 1,
                'allowed_updates': ['message', 'channel_post']
            }
            response = requests.get(url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok') and data.get('result'):
                    updates = data['result']
                    if updates:
                        logger.debug(f"Получено {len(updates)} обновлений")
                    return updates
            return []
        except Exception as e:
            logger.debug(f"Ошибка получения обновлений: {e}")
            return []

    def get_status_message(self) -> str:
        """Генерация сообщения статуса"""
        current_time = get_astana_time()
        total_vacancies = len(self.previous_vacancies)
        suspended = sum(1 for v in self.previous_vacancies.values() 
                       if v.get('status') == "Регистрация временно приостановлена")
        active = total_vacancies - suspended
        
        # Время до следующей проверки
        if self.last_check_time:
            next_check = self.last_check_time + timedelta(seconds=self.check_interval)
            time_to_next = next_check - current_time
            if time_to_next.total_seconds() > 0:
                next_check_str = f"{int(time_to_next.total_seconds() // 60)} мин. {int(time_to_next.total_seconds() % 60)} сек."
            else:
                next_check_str = "скоро..."
        else:
            next_check_str = "ожидание..."
        
        status_message = f"""
🤖 <b>Статус бота Agropraktika Monitor</b>

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
🕐 <b>Часовой пояс:</b> Астана (UTC+5)
"""
        return status_message.strip()

    def handle_status_command(self, chat_id: str):
        """Обработка команды /status"""
        status_message = self.get_status_message()
        self.send_telegram_message(status_message, chat_id)
        logger.info(f"Отправлен статус в чат {chat_id}")

    def handle_help_command(self, chat_id: str):
        """Обработка команды /help"""
        help_message = """
🤖 <b>Бот мониторинга Agropraktika</b>

<b>Доступные команды:</b>
/status - Показать статус бота
/check - Запустить проверку вакансий сейчас
/help - Показать это сообщение

<b>Как работает бот:</b>
Бот автоматически проверяет сайт каждые 5 минут и уведомляет, когда регистрация на вакансию открывается.

<b>Уведомления:</b>
🟢 - Регистрация открылась (важно!)
📊 - Ежечасный отчёт о статусе

<b>Часовой пояс:</b> Астана (UTC+5)
"""
        self.send_telegram_message(help_message.strip(), chat_id)

    def handle_check_command(self, chat_id: str):
        """Обработка команды /check - принудительная проверка"""
        self.send_telegram_message("🔄 <b>Запускаю проверку вакансий...</b>", chat_id)
        
        # Выполняем проверку
        current_vacancies = self.check_all_pages()
        current_time = get_astana_time()
        
        if current_vacancies:
            total = len(current_vacancies)
            suspended = sum(1 for v in current_vacancies if v['status'] == "Регистрация временно приостановлена")
            active = total - suspended
            
            result_message = f"""
✅ <b>Проверка завершена!</b>

📋 Найдено вакансий: <b>{total}</b>
🔴 Приостановлено: <b>{suspended}</b>
🟢 Активных: <b>{active}</b>

🕐 Время: {current_time.strftime('%H:%M:%S %d.%m.%Y')} (Астана)
"""
            self.send_telegram_message(result_message.strip(), chat_id)
        else:
            self.send_telegram_message("⚠️ Не удалось получить данные с сайта", chat_id)

    def process_commands(self):
        """Обработка входящих команд в отдельном потоке"""
        logger.info("Запущен обработчик команд")
        
        while self.running:
            try:
                updates = self.get_updates()
                
                for update in updates:
                    self.last_update_id = update.get('update_id', self.last_update_id)
                    
                    # Обрабатываем и сообщения, и посты в каналах
                    message = update.get('message') or update.get('channel_post') or {}
                    text = message.get('text', '')
                    chat = message.get('chat', {})
                    chat_id = str(chat.get('id', ''))
                    chat_type = chat.get('type', 'private')
                    
                    if not text or not chat_id:
                        continue
                    
                    # Логируем все входящие сообщения для отладки
                    logger.info(f"📨 Сообщение из {chat_type} ({chat_id}): {text[:50]}...")
                    
                    # Убираем @bot_name из команды
                    command = text.split()[0] if text else ''
                    command = command.split('@')[0]
                    
                    # Обработка команд
                    if command == '/status':
                        logger.info(f"✅ Обработка команды /status для чата {chat_id}")
                        self.handle_status_command(chat_id)
                    elif command in ['/help', '/start']:
                        logger.info(f"✅ Обработка команды /help для чата {chat_id}")
                        self.handle_help_command(chat_id)
                    elif command == '/check':
                        logger.info(f"✅ Обработка команды /check для чата {chat_id}")
                        self.handle_check_command(chat_id)
                
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Ошибка обработки команд: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(5)

    def send_hourly_report(self):
        """Отправка ежечасного отчёта"""
        current_time = get_astana_time()
        
        # Проверяем, нужно ли отправить ежечасный отчёт
        if self.last_hourly_report is None:
            self.last_hourly_report = current_time
            return
        
        # Проверяем, прошёл ли час с последнего отчёта
        time_since_last = current_time - self.last_hourly_report
        if time_since_last.total_seconds() >= 3600:  # 1 час
            logger.info("📊 Отправка ежечасного отчёта")
            status_message = self.get_status_message()
            self.send_telegram_message(status_message)
            self.last_hourly_report = current_time

    def get_vacancies_data(self, page: int = 1) -> List[Dict]:
        """Получение данных о вакансиях с указанной страницы"""
        url = f"{self.base_url}/vacancies?page={page}"
        vacancies = []

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Connection': 'keep-alive',
            }

            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')

            # Сохраняем HTML для отладки (только первый раз)
            if page == 1:
                try:
                    with open('debug_page.html', 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    logger.info("HTML сохранён в debug_page.html для отладки")
                except:
                    pass

            # Метод 1: Ищем список вакансий по классу
            vacancies_list = soup.find('ul', {'class': 'vacancies-list'})
            
            # Метод 2: Если не нашли, ищем по частичному совпадению класса
            if not vacancies_list:
                for ul in soup.find_all('ul'):
                    class_attr = ul.get('class', [])
                    if class_attr and any('vacanc' in c.lower() for c in class_attr):
                        vacancies_list = ul
                        break
            
            # Метод 3: Ищем li с class содержащим vacancy
            if not vacancies_list:
                vacancy_cards = soup.find_all('li', class_=lambda x: x and 'vacancy' in ' '.join(x).lower())
            else:
                vacancy_cards = vacancies_list.find_all('li')
            
            # Если всё ещё не нашли, парсим по ссылкам
            if not vacancy_cards:
                logger.info("Пробуем альтернативный метод парсинга по ссылкам...")
                vacancy_links = soup.find_all('a', href=lambda x: x and '/vacancies/' in x and ':' in x)
                seen_links = set()
                
                for link_elem in vacancy_links:
                    href = link_elem.get('href', '')
                    if href in seen_links or 'agro-button' in (link_elem.get('class') or []):
                        continue
                    seen_links.add(href)
                    
                    parent = link_elem.find_parent('li') or link_elem.find_parent('div')
                    if parent:
                        title = link_elem.get_text(strip=True) or "Без названия"
                        
                        full_text = parent.get_text()
                        if "Регистрация временно приостановлена" in full_text:
                            status = "Регистрация временно приостановлена"
                        else:
                            status = "Регистрация открыта"
                        
                        start_date = ""
                        date_match = re.search(r'Начинается:\s*(\d{2}/\d{2}/\d{4})', full_text)
                        if date_match:
                            start_date = date_match.group(1)
                        
                        link = href if href.startswith('http') else f"{self.base_url}{href}"
                        vacancy_id = hashlib.md5(link.encode()).hexdigest()[:8]
                        
                        vacancy_data = {
                            'id': vacancy_id,
                            'title': title,
                            'position': '',
                            'location': '',
                            'duration': '',
                            'start_date': start_date,
                            'status': status,
                            'link': link,
                            'last_checked': get_astana_time().isoformat()
                        }
                        vacancies.append(vacancy_data)
                
                logger.info(f"Найдено {len(vacancies)} вакансий на странице {page} (альтернативный метод)")
                return vacancies

            # Стандартный парсинг карточек
            for card in vacancy_cards:
                try:
                    link_elem = card.find('a', href=lambda x: x and '/vacancies/' in x)
                    if not link_elem:
                        continue
                        
                    link = link_elem.get('href', '')
                    if link and not link.startswith('http'):
                        link = f"{self.base_url}{link}"

                    title_elem = card.find('h4')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                    else:
                        title = link_elem.get_text(strip=True) or "Без названия"

                    card_text = card.get_text()
                    
                    if "Регистрация временно приостановлена" in card_text:
                        status = "Регистрация временно приостановлена"
                    elif "приостановлена" in card_text.lower():
                        status = "Регистрация временно приостановлена"
                    else:
                        status = "Регистрация открыта"

                    start_date = ""
                    date_match = re.search(r'Начинается:\s*(\d{2}/\d{2}/\d{4})', card_text)
                    if date_match:
                        start_date = date_match.group(1)

                    location = ""
                    location_patterns = [
                        r'(\w+)\s*\(Lithuania\)',
                        r'(\w+)\s*\(United Kingdom\)',
                        r'(\w+)\s*\(Norway\)',
                        r'(Lithuania)',
                        r'(United Kingdom)', 
                        r'(Norway)',
                    ]
                    for pattern in location_patterns:
                        loc_match = re.search(pattern, card_text)
                        if loc_match:
                            location = loc_match.group(0)
                            break

                    vacancy_id = hashlib.md5(link.encode()).hexdigest()[:8]

                    vacancy_data = {
                        'id': vacancy_id,
                        'title': title,
                        'position': '',
                        'location': location,
                        'duration': '',
                        'start_date': start_date,
                        'status': status,
                        'link': link,
                        'last_checked': get_astana_time().isoformat()
                    }

                    vacancies.append(vacancy_data)

                except Exception as e:
                    logger.warning(f"Ошибка парсинга карточки: {e}")
                    continue

            logger.info(f"Найдено {len(vacancies)} вакансий на странице {page}")
            return vacancies

        except requests.RequestException as e:
            logger.error(f"Ошибка запроса к {url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def check_all_pages(self) -> List[Dict]:
        """Проверка всех страниц с вакансиями"""
        all_vacancies = []
        page = 1
        max_pages = 5

        while page <= max_pages:
            vacancies = self.get_vacancies_data(page)
            if not vacancies:
                break

            all_vacancies.extend(vacancies)

            if len(vacancies) < 10:
                break

            page += 1

        logger.info(f"Всего найдено {len(all_vacancies)} вакансий на {page} страницах")
        return all_vacancies

    def analyze_changes(self, current_vacancies: List[Dict]):
        """Анализ изменений в вакансиях"""
        current_dict = {v['id']: v for v in current_vacancies}
        previous_dict = self.previous_vacancies

        changes_detected = False
        new_open_vacancies = []

        for vac_id, vacancy in current_dict.items():
            if vac_id not in previous_dict:
                logger.info(f"Новая вакансия: {vacancy['title']}")
                changes_detected = True

                if vacancy['status'] != "Регистрация временно приостановлена":
                    new_open_vacancies.append(vacancy)

            elif vacancy['status'] != previous_dict[vac_id]['status']:
                old_status = previous_dict[vac_id]['status']
                new_status = vacancy['status']

                logger.info(f"Изменение статуса: {vacancy['title']} - {old_status} → {new_status}")
                changes_detected = True

                if old_status == "Регистрация временно приостановлена" and new_status != "Регистрация временно приостановлена":
                    new_open_vacancies.append(vacancy)

        for vac_id in previous_dict:
            if vac_id not in current_dict:
                logger.info(f"Вакансия удалена: {previous_dict[vac_id]['title']}")
                changes_detected = True

        # Отправляем уведомления об открытых вакансиях
        for vacancy in new_open_vacancies:
            current_time = get_astana_time()
            message = f"""
🟢 <b>ВАЖНО: Регистрация открылась!</b>

🏷 <b>Вакансия:</b> {vacancy['title']}
📍 <b>Место:</b> {vacancy['location']}
🚀 <b>Начинается:</b> {vacancy['start_date']}

🔗 <a href="{vacancy['link']}">Скорее переходи по ссылке!</a>

🕐 Время: {current_time.strftime('%H:%M:%S %d.%m.%Y')} (Астана)
<i>ID: {vacancy['id']}</i>
"""
            self.send_telegram_message(message.strip())

        # Статистика
        total_current = len(current_vacancies)
        suspended_current = sum(1 for v in current_vacancies if v['status'] == "Регистрация временно приостановлена")
        active_current = total_current - suspended_current

        logger.info(
            f"Статистика: Всего {total_current} | Приостановлено {suspended_current} | Активных {active_current}")

        # Обновляем предыдущее состояние
        self.previous_vacancies = current_dict

        return len(new_open_vacancies) > 0

    def check_for_updates(self):
        """Основная функция проверки обновлений"""
        try:
            logger.info("Начинаю проверку вакансий...")
            self.last_check_time = get_astana_time()
            self.check_count += 1

            current_vacancies = self.check_all_pages()

            if not current_vacancies:
                logger.warning("Не удалось получить данные о вакансиях")
                self.send_telegram_message("⚠️ <b>Внимание:</b> Не удалось получить данные с сайта Agropraktika")
                return

            self.analyze_changes(current_vacancies)
            
            # Проверяем ежечасный отчёт
            self.send_hourly_report()

        except Exception as e:
            logger.error(f"Ошибка при проверке: {e}")
            self.send_telegram_message(f"⚠️ <b>Ошибка мониторинга:</b>\n{str(e)[:200]}")

    def run(self):
        """Основной цикл работы бота"""
        current_time = get_astana_time()
        
        # Запускаем обработчик команд в отдельном потоке
        command_thread = threading.Thread(target=self.process_commands, daemon=True)
        command_thread.start()
        logger.info("Обработчик команд запущен в отдельном потоке")
        
        # СНАЧАЛА парсим все вакансии
        logger.info("Первоначальный сбор данных...")
        initial_vacancies = []
        try:
            initial_vacancies = self.check_all_pages()
        except Exception as e:
            logger.error(f"Ошибка при начальной загрузке: {e}")

        # ПОТОМ отправляем ОДНО сообщение со всей информацией
        if initial_vacancies:
            self.previous_vacancies = {v['id']: v for v in initial_vacancies}
            suspended = sum(1 for v in initial_vacancies if v['status'] == "Регистрация временно приостановлена")
            active = len(initial_vacancies) - suspended
            self.last_check_time = get_astana_time()
            self.last_hourly_report = get_astana_time()

            logger.info(f"Инициализация завершена. Загружено {len(initial_vacancies)} вакансий")

            # ОДНО сообщение с полной информацией о запуске
            startup_message = f"""
� <b>Мониторинг Agropraktika запущен!</b>

📡 Отслеживаю открытие регистрации на вакансии
⏱ Интервал проверки: {self.check_interval // 60} минут
🌐 Сайт: agropraktika.eu/vacancies
🕐 Часовой пояс: Астана (UTC+5)

📋 <b>Загружено вакансий:</b> {len(initial_vacancies)}
🔴 С приостановленной регистрацией: {suspended}
🟢 Активных: {active}

<b>Команды:</b>
/status - Статус бота
/check - Проверить сейчас
/help - Помощь

<b>Отчёты:</b> каждый час автоматически

📅 Время запуска: {current_time.strftime('%H:%M:%S %d.%m.%Y')} (Астана)
"""
            self.send_telegram_message(startup_message.strip())
        else:
            startup_message = f"""
🚀 <b>Мониторинг Agropraktika запущен!</b>

⚠️ <b>Внимание:</b> Не удалось загрузить начальные данные.
Проверьте доступность сайта. Бот продолжит попытки.

📅 Время: {current_time.strftime('%H:%M:%S %d.%m.%Y')} (Астана)
"""
            self.send_telegram_message(startup_message.strip())
            self.last_hourly_report = get_astana_time()

        # Основной цикл
        while self.running:
            try:
                logger.info(f"=== Проверка #{self.check_count + 1} ===")

                self.check_for_updates()
                logger.info(f"Следующая проверка через {self.check_interval} секунд")
                time.sleep(self.check_interval)

            except KeyboardInterrupt:
                logger.info("Мониторинг остановлен пользователем")
                self.running = False
                self.send_telegram_message("🛑 <b>Мониторинг остановлен</b>")
                break
            except Exception as e:
                logger.error(f"Критическая ошибка: {e}")
                time.sleep(60)


def main():
    """Запуск мониторинга"""
    current_time = get_astana_time()
    print("=" * 50)
    print("🤖 Мониторинг вакансий Agropraktika")
    print("=" * 50)
    print(f"Токен бота: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"Интервал проверки: {CHECK_INTERVAL} сек ({CHECK_INTERVAL // 60} мин)")
    print(f"Часовой пояс: Астана (UTC+{TIMEZONE_OFFSET})")
    print(f"Текущее время: {current_time.strftime('%H:%M:%S %d.%m.%Y')}")
    print("=" * 50)
    print("Запускаю мониторинг...")
    print("Для остановки нажмите Ctrl+C")

    try:
        VacancyMonitor().run()
    except KeyboardInterrupt:
        print("\nМониторинг остановлен.")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")


if __name__ == "__main__":
    main()
