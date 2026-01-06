import os
import asyncio
import logging
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
import hashlib

# ============================================
# НАСТРОЙКИ - МЕНЯЙТЕ ЗДЕСЬ!
# ============================================

# ВАШ ТОКЕН БОТА (получите у @BotFather)
TELEGRAM_BOT_TOKEN = ""  # Пример: "60468фыв46295:AAFcфывфRxuSxEg7фвфYiKZWфффыIFBS5w"

# ID вашего чата (куда отправлять уведомления)
TELEGRAM_CHAT_ID = ""  # Пример: "-id канала" или "@канал"

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
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.previous_vacancies: Dict[str, Dict] = {}
        self.base_url = "https://agropraktika.eu"
        self.check_interval = CHECK_INTERVAL
    
    async def send_telegram_message(self, message: str) -> bool:
        """Отправка сообщения с обработкой ошибок"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, 
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=False
            )
            logger.info("Сообщение отправлено")
            return True
        except TelegramError as e:
            logger.error(f"Ошибка отправки: {e}")
            return False
    
    def get_vacancies_data(self, page: int = 1) -> List[Dict]:
        """Получение данных о вакансиях с указанной страницы"""
        url = f"{self.base_url}/vacancies?page={page}"
        vacancies = []
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Поиск всех карточек вакансий
            vacancy_cards = soup.find_all('div', class_='space-y-4')
            
            for card in vacancy_cards:
                try:
                    # Название вакансии
                    title_elem = card.find('h3', class_='text-xl')
                    title = title_elem.text.strip() if title_elem else "Без названия"
                    
                    # Ссылка на вакансию
                    link_elem = card.find('a', href=True)
                    link = f"{self.base_url}{link_elem['href']}" if link_elem else ""
                    
                    # Детали: должность, местоположение, срок
                    details = card.find_all('p', class_='text-sm text-gray-600')
                    position = details[0].text.strip() if len(details) > 0 else ""
                    location = details[1].text.strip() if len(details) > 1 else ""
                    duration = details[2].text.strip() if len(details) > 2 else ""
                    
                    # Дата начала
                    start_date_elem = card.find('p', class_='text-sm text-green-500')
                    start_date = start_date_elem.text.strip().replace('Начинается: ', '') if start_date_elem else ""
                    
                    # Статус регистрации
                    status_elem = card.find('p', class_='text-sm text-red-400')
                    status = status_elem.text.strip() if status_elem else "Статус неизвестен"
                    
                    # Создаем уникальный ID вакансии
                    vacancy_id = hashlib.md5(f"{title}{position}{start_date}".encode()).hexdigest()[:8]
                    
                    vacancy_data = {
                        'id': vacancy_id,
                        'title': title,
                        'position': position,
                        'location': location,
                        'duration': duration,
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
            return []
    
    async def check_all_pages(self) -> List[Dict]:
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
        
        logger.info(f"Всего найдено {len(all_vacancies)} вакансий на {page-1} страницах")
        return all_vacancies
    
    async def analyze_changes(self, current_vacancies: List[Dict]):
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
👨‍💼 <b>Должность:</b> {vacancy['position']}
📍 <b>Место:</b> {vacancy['location']}
📅 <b>Срок:</b> {vacancy['duration']}
🚀 <b>Начинается:</b> {vacancy['start_date']}

🔗 <a href="{vacancy['link']}">Скорее переходи по ссылке!</a>

<i>ID: {vacancy['id']}</i>
"""
            await self.send_telegram_message(message.strip())
        
        # Статистика
        total_current = len(current_vacancies)
        suspended_current = sum(1 for v in current_vacancies if v['status'] == "Регистрация временно приостановлена")
        active_current = total_current - suspended_current
        
        logger.info(f"Статистика: Всего {total_current} | Приостановлено {suspended_current} | Активных {active_current}")
        
        # Отправляем общую статистику раз в день
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
            
            await self.send_telegram_message(stats_message.strip())
        
        # Обновляем предыдущее состояние
        self.previous_vacancies = current_dict
        
        return len(new_open_vacancies) > 0
    
    async def check_for_updates(self):
        """Основная функция проверки обновлений"""
        try:
            logger.info("Начинаю проверку вакансий...")
            
            # Получаем текущие вакансии
            current_vacancies = await self.check_all_pages()
            
            if not current_vacancies:
                logger.warning("Не удалось получить данные о вакансиях")
                await self.send_telegram_message("⚠️ <b>Внимание:</b> Не удалось получить данные с сайта Agropraktika")
                return
            
            # Анализируем изменения
            changes_found = await self.analyze_changes(current_vacancies)
            
            if not changes_found:
                logger.info("Изменений не обнаружено")
            
        except Exception as e:
            logger.error(f"Ошибка при проверке: {e}")
            await self.send_telegram_message(f"⚠️ <b>Ошибка мониторинга:</b>\n{str(e)[:200]}")
    
    async def run(self):
        """Основной цикл работы бота"""
        # Начальное сообщение
        startup_message = f"""
🚀 <b>Мониторинг Agropraktika запущен!</b>

📡 Отслеживаю открытие регистрации на вакансии
⏱ Интервал проверки: {self.check_interval // 60} минут
🌐 Сайт: agropraktika.eu/vacancies

Бот будет уведомлять при открытии регистрации.
"""
        await self.send_telegram_message(startup_message.strip())
        
        # Первоначальный сбор данных
        logger.info("Первоначальный сбор данных...")
        try:
            initial_vacancies = await self.check_all_pages()
            if initial_vacancies:
                self.previous_vacancies = {v['id']: v for v in initial_vacancies}
                suspended = sum(1 for v in initial_vacancies if v['status'] == "Регистрация временно приостановлена")
                
                logger.info(f"Инициализация завершена. Загружено {len(initial_vacancies)} вакансий")
                
                init_stats = f"""
📋 <b>Начальная загрузка завершена</b>

Загружено вакансий: {len(initial_vacancies)}
🔴 С приостановленной регистрацией: {suspended}
🟢 Активных: {len(initial_vacancies) - suspended}

Мониторинг начал работу. Ожидайте уведомлений!
"""
                await self.send_telegram_message(init_stats.strip())
            else:
                await self.send_telegram_message("⚠️ <b>Внимание:</b> Не удалось загрузить начальные данные. Проверьте доступность сайта.")
        except Exception as e:
            logger.error(f"Ошибка при начальной загрузке: {e}")
            await self.send_telegram_message(f"⚠️ <b>Ошибка начальной загрузки:</b>\n{str(e)[:200]}")
        
        # Основной цикл
        check_count = 0
        while True:
            try:
                check_count += 1
                logger.info(f"=== Проверка #{check_count} ===")
                
                await self.check_for_updates()
                logger.info(f"Следующая проверка через {self.check_interval} секунд")
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("Мониторинг остановлен пользователем")
                await self.send_telegram_message("🛑 <b>Мониторинг остановлен</b>")
                break
            except Exception as e:
                logger.error(f"Критическая ошибка: {e}")
                await asyncio.sleep(60)  # Пауза при критической ошибке

def main():
    """Запуск мониторинга"""
    print("=" * 50)
    print("🤖 Мониторинг вакансий Agropraktika")
    print("=" * 50)
    print(f"Токен бота: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"Интервал проверки: {CHECK_INTERVAL} сек ({CHECK_INTERVAL//60} мин)")
    print("=" * 50)
    print("Запускаю мониторинг...")
    print("Для остановки нажмите Ctrl+C")
    
    try:
        asyncio.run(VacancyMonitor().run())
    except KeyboardInterrupt:
        print("\nМониторинг остановлен.")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    main()

