import os
import time
import logging
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
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

# ============================================
# КОНЕЦ НАСТРОЕК
# ============================================

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
        self.start_time = datetime.now()
        self.check_count = 0
        self.last_check_time = None
        self.last_update_id = 0
        self.running = True
        
    def format_uptime(self) -> str:
        """Форматирование времени работы"""
        uptime = datetime.now() - self.start_time
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
                'allowed_updates': ['message']
            }
            response = requests.get(url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok') and data.get('result'):
                    return data['result']
            return []
        except Exception as e:
            logger.debug(f"Ошибка получения обновлений: {e}")
            return []

    def handle_status_command(self, chat_id: str):
        """Обработка команды /status"""
        total_vacancies = len(self.previous_vacancies)
        suspended = sum(1 for v in self.previous_vacancies.values() 
                       if v.get('status') == "Регистрация временно приостановлена")
        active = total_vacancies - suspended
        
        # Время до следующей проверки
        if self.last_check_time:
            next_check = self.last_check_time + timedelta(seconds=self.check_interval)
            time_to_next = next_check - datetime.now()
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
📅 <b>Запущен:</b> {self.start_time.strftime('%H:%M:%S %d.%m.%Y')}

📊 <b>Статистика:</b>
├ Вакансий отслеживается: <b>{total_vacancies}</b>
├ 🔴 Приостановлено: <b>{suspended}</b>
├ 🟢 Активных: <b>{active}</b>
└ #️⃣ Проверок выполнено: <b>{self.check_count}</b>

⏱ <b>Проверки:</b>
├ Последняя: {self.last_check_time.strftime('%H:%M:%S') if self.last_check_time else 'ещё не было'}
├ Следующая через: {next_check_str}
└ Интервал: {self.check_interval // 60} мин.

🌐 <b>Сайт:</b> agropraktika.eu/vacancies
"""
        self.send_telegram_message(status_message.strip(), chat_id)
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
📊 - Ежедневная статистика (9:00)
"""
        self.send_telegram_message(help_message.strip(), chat_id)

    def handle_check_command(self, chat_id: str):
        """Обработка команды /check - принудительная проверка"""
        self.send_telegram_message("🔄 <b>Запускаю проверку вакансий...</b>", chat_id)
        
        # Выполняем проверку
        current_vacancies = self.check_all_pages()
        
        if current_vacancies:
            total = len(current_vacancies)
            suspended = sum(1 for v in current_vacancies if v['status'] == "Регистрация временно приостановлена")
            active = total - suspended
            
            result_message = f"""
✅ <b>Проверка завершена!</b>

📋 Найдено вакансий: <b>{total}</b>
🔴 Приостановлено: <b>{suspended}</b>
🟢 Активных: <b>{active}</b>

Время: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}
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
                    
                    message = update.get('message', {})
                    text = message.get('text', '')
                    chat_id = str(message.get('chat', {}).get('id', ''))
                    
                    if not text or not chat_id:
                        continue
                    
                    # Обработка команд
                    if text.startswith('/status'):
                        logger.info(f"Получена команда /status от {chat_id}")
                        self.handle_status_command(chat_id)
                    elif text.startswith('/help') or text.startswith('/start'):
                        logger.info(f"Получена команда /help от {chat_id}")
                        self.handle_help_command(chat_id)
                    elif text.startswith('/check'):
                        logger.info(f"Получена команда /check от {chat_id}")
                        self.handle_check_command(chat_id)
                
                time.sleep(2)  # Проверяем команды каждые 2 секунды
                
            except Exception as e:
                logger.error(f"Ошибка обработки команд: {e}")
                time.sleep(5)

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
                # Ищем все ссылки на вакансии
                vacancy_links = soup.find_all('a', href=lambda x: x and '/vacancies/' in x and ':' in x)
                seen_links = set()
                
                for link_elem in vacancy_links:
                    href = link_elem.get('href', '')
                    if href in seen_links or 'agro-button' in (link_elem.get('class') or []):
                        continue
                    seen_links.add(href)
                    
                    # Пытаемся найти родительский контейнер
                    parent = link_elem.find_parent('li') or link_elem.find_parent('div')
                    if parent:
                        title = link_elem.get_text(strip=True) or "Без названия"
                        
                        # Ищем текст с "Регистрация"
                        full_text = parent.get_text()
                        if "Регистрация временно приостановлена" in full_text:
                            status = "Регистрация временно приостановлена"
                        else:
                            status = "Регистрация открыта"
                        
                        # Ищем дату начала
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
                            'last_checked': datetime.now().isoformat()
                        }
                        vacancies.append(vacancy_data)
                
                logger.info(f"Найдено {len(vacancies)} вакансий на странице {page} (альтернативный метод)")
                return vacancies

            # Стандартный парсинг карточек
            for card in vacancy_cards:
                try:
                    # Ищем любую ссылку на вакансию
                    link_elem = card.find('a', href=lambda x: x and '/vacancies/' in x)
                    if not link_elem:
                        continue
                        
                    link = link_elem.get('href', '')
                    if link and not link.startswith('http'):
                        link = f"{self.base_url}{link}"

                    # Название - из h4 или из первой ссылки с текстом
                    title_elem = card.find('h4')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                    else:
                        title = link_elem.get_text(strip=True) or "Без названия"

                    # Получаем весь текст карточки для анализа
                    card_text = card.get_text()
                    
                    # Статус регистрации
                    if "Регистрация временно приостановлена" in card_text:
                        status = "Регистрация временно приостановлена"
                    elif "приостановлена" in card_text.lower():
                        status = "Регистрация временно приостановлена"
                    else:
                        status = "Регистрация открыта"

                    # Дата начала
                    start_date = ""
                    date_match = re.search(r'Начинается:\s*(\d{2}/\d{2}/\d{4})', card_text)
                    if date_match:
                        start_date = date_match.group(1)

                    # Локация - ищем известные паттерны
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
                        'last_checked': datetime.now().isoformat()
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
        max_pages = 5  # Максимум 5 страниц для безопасности

        while page <= max_pages:
            vacancies = self.get_vacancies_data(page)
            if not vacancies:
                break

            all_vacancies.extend(vacancies)

            # Если на странице меньше 10 вакансий, вероятно, это последняя
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

        # Проверяем новые или изменившиеся вакансии
        for vac_id, vacancy in current_dict.items():
            if vac_id not in previous_dict:
                # Новая вакансия
                logger.info(f"Новая вакансия: {vacancy['title']}")
                changes_detected = True

                # Если новая вакансия уже открыта
                if vacancy['status'] != "Регистрация временно приостановлена":
                    new_open_vacancies.append(vacancy)

            elif vacancy['status'] != previous_dict[vac_id]['status']:
                # Изменился статус
                old_status = previous_dict[vac_id]['status']
                new_status = vacancy['status']

                logger.info(f"Изменение статуса: {vacancy['title']} - {old_status} → {new_status}")
                changes_detected = True

                # Отправляем уведомление только если регистрация ОТКРЫЛАСЬ
                if old_status == "Регистрация временно приостановлена" and new_status != "Регистрация временно приостановлена":
                    new_open_vacancies.append(vacancy)

        # Проверяем удаленные вакансии
        for vac_id in previous_dict:
            if vac_id not in current_dict:
                logger.info(f"Вакансия удалена: {previous_dict[vac_id]['title']}")
                changes_detected = True

        # Отправляем уведомления об открытых вакансиях
        for vacancy in new_open_vacancies:
            message = f"""
🟢 <b>ВАЖНО: Регистрация открылась!</b>

🏷 <b>Вакансия:</b> {vacancy['title']}
📍 <b>Место:</b> {vacancy['location']}
🚀 <b>Начинается:</b> {vacancy['start_date']}

🔗 <a href="{vacancy['link']}">Скорее переходи по ссылке!</a>

<i>ID: {vacancy['id']}</i>
"""
            self.send_telegram_message(message.strip())

        # Статистика
        total_current = len(current_vacancies)
        suspended_current = sum(1 for v in current_vacancies if v['status'] == "Регистрация временно приостановлена")
        active_current = total_current - suspended_current

        logger.info(
            f"Статистика: Всего {total_current} | Приостановлено {suspended_current} | Активных {active_current}")

        # Отправляем общую статистику только если есть изменения или в 9 утра
        current_time = datetime.now()
        if changes_detected or (current_time.hour == 9 and current_time.minute < 5):
            stats_message = f"""
📊 <b>Статистика Agropraktika</b>

Всего вакансий: {total_current}
🔴 Приостановлено: {suspended_current}
🟢 Активных: {active_current}

Последняя проверка: {current_time.strftime('%H:%M %d.%m.%Y')}
"""
            if new_open_vacancies:
                stats_message += f"\n🎯 <b>Новых открытых вакансий:</b> {len(new_open_vacancies)}"

            self.send_telegram_message(stats_message.strip())

        # Обновляем предыдущее состояние
        self.previous_vacancies = current_dict

        return len(new_open_vacancies) > 0

    def check_for_updates(self):
        """Основная функция проверки обновлений"""
        try:
            logger.info("Начинаю проверку вакансий...")
            self.last_check_time = datetime.now()
            self.check_count += 1

            # Получаем текущие вакансии
            current_vacancies = self.check_all_pages()

            if not current_vacancies:
                logger.warning("Не удалось получить данные о вакансиях")
                self.send_telegram_message("⚠️ <b>Внимание:</b> Не удалось получить данные с сайта Agropraktika")
                return

            # Анализируем изменения
            changes_found = self.analyze_changes(current_vacancies)

            if not changes_found:
                logger.info("Изменений не обнаружено")

        except Exception as e:
            logger.error(f"Ошибка при проверке: {e}")
            self.send_telegram_message(f"⚠️ <b>Ошибка мониторинга:</b>\n{str(e)[:200]}")

    def run(self):
        """Основной цикл работы бота"""
        # Запускаем обработчик команд в отдельном потоке
        command_thread = threading.Thread(target=self.process_commands, daemon=True)
        command_thread.start()
        logger.info("Обработчик команд запущен в отдельном потоке")
        
        # Начальное сообщение
        startup_message = f"""
🚀 <b>Мониторинг Agropraktika запущен!</b>

📡 Отслеживаю открытие регистрации на вакансии
⏱ Интервал проверки: {self.check_interval // 60} минут
🌐 Сайт: agropraktika.eu/vacancies

<b>Команды:</b>
/status - Статус бота
/check - Проверить сейчас
/help - Помощь

Бот будет уведомлять при открытии регистрации.
"""
        self.send_telegram_message(startup_message.strip())

        # Первоначальный сбор данных
        logger.info("Первоначальный сбор данных...")
        try:
            initial_vacancies = self.check_all_pages()
            if initial_vacancies:
                self.previous_vacancies = {v['id']: v for v in initial_vacancies}
                suspended = sum(1 for v in initial_vacancies if v['status'] == "Регистрация временно приостановлена")

                logger.info(f"Инициализация завершена. Загружено {len(initial_vacancies)} вакансий")
                self.last_check_time = datetime.now()

                init_stats = f"""
📋 <b>Начальная загрузка завершена</b>

Загружено вакансий: {len(initial_vacancies)}
🔴 С приостановленной регистрацией: {suspended}
🟢 Активных: {len(initial_vacancies) - suspended}

Мониторинг начал работу. Ожидайте уведомлений!
"""
                self.send_telegram_message(init_stats.strip())
            else:
                self.send_telegram_message(
                    "⚠️ <b>Внимание:</b> Не удалось загрузить начальные данные. Проверьте доступность сайта.")
        except Exception as e:
            logger.error(f"Ошибка при начальной загрузке: {e}")
            self.send_telegram_message(f"⚠️ <b>Ошибка начальной загрузки:</b>\n{str(e)[:200]}")

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
                time.sleep(60)  # Пауза при критической ошибке


def main():
    """Запуск мониторинга"""
    print("=" * 50)
    print("🤖 Мониторинг вакансий Agropraktika")
    print("=" * 50)
    print(f"Токен бота: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"Интервал проверки: {CHECK_INTERVAL} сек ({CHECK_INTERVAL // 60} мин)")
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
