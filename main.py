import requests
from bs4 import BeautifulSoup
from telegram import Bot
import asyncio
import os
from keep_alive import keep_alive
keep_alive()


# Ваш токен бота и ID чата
TOKEN = '6600994228:AAEKvdJCVZPCBXkP3ylfFW9jHqS-l0U1WPo'
CHAT_ID = '@Ginesis_v1'

# Глобальная переменная для хранения предыдущего состояния статусов вакансий
previous_statuses = None


async def send_telegram_message(message):
    bot = Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=message)


async def send_startup_message():
    message = "Мониторинг сайта Agropraktika.eu запущен."
    print(message)
    await send_telegram_message(message)


def get_vacancy_statuses():
    url = 'https://agropraktika.eu/vacancies'
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Находим все элементы с классами, представленными в вашем примере
    vacancy_status_elements = soup.find_all('p', class_='text-sm text-red-400 font-medium m-0')

    # Получаем список статусов вакансий
    vacancy_statuses = [element.text.strip() for element in vacancy_status_elements]
    return vacancy_statuses


async def check_vacancy_page():
    global previous_statuses

    current_statuses = get_vacancy_statuses()

    if previous_statuses is None:
        # Если это первая проверка, просто обновляем предыдущее состояние
        previous_statuses = current_statuses
        return

    # Проверяем, изменились ли статусы вакансий
    if current_statuses != previous_statuses:
        message = "Изменение статуса вакансий!"
        print(message)
        await send_telegram_message(message)
        previous_statuses = current_statuses

    # Подсчитываем количество вакансий со статусом "Регистрация временно приостановлена"
    suspended_vacancies_count = current_statuses.count('Регистрация временно приостановлена')
    print(f"Вакансий с приостановленной регистрацией: {suspended_vacancies_count}")


# Уведомление о начале мониторинга
asyncio.run(send_startup_message())

# Основной цикл мониторинга
while True:
    asyncio.run(check_vacancy_page())

    # Проверяем каждые 5 минут
    await asyncio.sleep(300)
