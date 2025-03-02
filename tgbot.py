import logging
import os
from g4f.client import Client
import g4f
import requests
from telegram import Update, ParseMode, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler, ConversationHandler
from PIL import Image
from io import BytesIO
import tempfile
import re

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен Telegram бота
TOKEN = ""

# Максимальная длина сообщения в Telegram
MAX_MESSAGE_LENGTH = 4000  # Оставляем небольшой запас от лимита в 4096

# Состояния для ConversationHandler
WAITING_FOR_GPT_PROMPT = 1
WAITING_FOR_IMAGE_PROMPT = 2

# Инициализация g4f провайдеров
g4f.debug.logging = False  # Отключить дебаг логи
# Получаем доступные модели
try:
    # Пробуем получить модели из нового API
    available_models = list(g4f.models.__all__)
except:
    try:
        # Альтернативный способ для новых версий
        available_models = [model.__name__ for model in g4f.models.list()]
    except:
        # Запасной вариант
        available_models = ["gpt-3.5-turbo", "gpt-4", "gpt-4o", "gpt-4o-mini"]

# Устанавливаем модели по умолчанию
try:
    # Модель для текстовых ответов
    if hasattr(g4f.models, "gpt_4o_mini"):
        text_model = g4f.models.gpt_4o_mini
    else:
        text_model = "gpt-4o-mini"  # Используем строковое имя модели
    
    # Модель для генерации изображений
    if hasattr(g4f.models, "flux"):
        image_model = g4f.models.flux
    else:
        image_model = "flux"  # Используем строковое имя модели
except:
    text_model = "gpt-4o-mini"
    image_model = "flux"

# История сообщений для каждого пользователя
user_history = {}

def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = update.effective_user.id
    
    # Инициализируем историю пользователя без системного сообщения о разметке
    user_history[user_id] = []
    
    # Создаем клавиатуру с основными командами
    keyboard = [
        [KeyboardButton("🤖 GPT"), KeyboardButton("🖼 Изображение")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f'Привет, {user.first_name}! Я бот, работающий с GPT. '
        f'Вы можете отправить мне сообщение, и я отвечу, используя GPT4free.\n\n'
        f'Текущие настройки:\n'
        f'- Текстовые ответы: gpt-4o-mini\n'
        f'- Генерация изображений: flux\n\n'
        f'Команды:\n'
        f'/gpt <сообщение> - Получить ответ от GPT\n'
        f'/image <описание> - Сгенерировать изображение',
        reply_markup=reply_markup
    )

def get_gpt_response(prompt, model=text_model, history=None):
    """Получить ответ от GPT4free"""
    try:
        messages = []
        if history:
            messages.extend(history)
        
        messages.append({"role": "user", "content": prompt})
        
        try:
            # Пробуем использовать клиент API
            client = Client()
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            response_text = response.choices[0].message.content
        except Exception as client_error:
            logger.warning(f"Ошибка при использовании клиента: {client_error}")
            # Если клиент не работает, используем старый метод
            response_text = g4f.ChatCompletion.create(
                model=model,
                messages=messages,
                stream=False,
            )
        
        return response_text, {"role": "assistant", "content": response_text}
    except Exception as e:
        logger.error(f"Ошибка при получении ответа от GPT: {e}")
        return f"Произошла ошибка: {str(e)}", None

def generate_image(prompt):
    """Генерировать изображение по описанию"""
    try:
        # Логируем запрос
        logger.info(f"Запрос на генерацию изображения: {prompt}")
        
        # Создаем клиент
        client = Client()
        
        # Генерируем изображение
        response = client.images.generate(
            model="flux",
            prompt=prompt,
            response_format="url"
        )
        
        # Получаем URL изображения
        if response and hasattr(response, 'data') and len(response.data) > 0:
            image_url = response.data[0].url
            logger.info(f"Получен URL изображения: {image_url}")
            
            # Загружаем изображение
            img_response = requests.get(image_url, timeout=60)
            
            # Возвращаем содержимое изображения
            return img_response.content
        
        return None
    except Exception as e:
        logger.error(f"Ошибка при генерации изображения: {e}")
        return None

def handle_message(update: Update, context: CallbackContext) -> None:
    """Обработчик обычных сообщений"""
    user_id = update.effective_user.id
    prompt = update.message.text
    
    # Обработка кнопок меню
    if prompt == "🤖 GPT":
        # Запрашиваем у пользователя ввод для GPT
        update.message.reply_text(
            "Введите ваш запрос для GPT:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Отмена", callback_data="cancel")]
            ])
        )
        return WAITING_FOR_GPT_PROMPT
    
    elif prompt == "🖼 Изображение":
        # Запрашиваем у пользователя описание изображения
        update.message.reply_text(
            "Введите описание изображения, которое хотите сгенерировать:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Отмена", callback_data="cancel")]
            ])
        )
        return WAITING_FOR_IMAGE_PROMPT
    
    # Инициализация истории пользователя, если её нет
    if user_id not in user_history:
        user_history[user_id] = []
    
    # Отправка "печатает..."
    context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # Получение ответа от GPT
    response, assistant_message = get_gpt_response(prompt, history=user_history[user_id])
    
    # Добавление сообщений в историю
    user_history[user_id].append({"role": "user", "content": prompt})
    if assistant_message:
        user_history[user_id].append(assistant_message)
    
    # Разбиваем длинное сообщение на части и отправляем
    message_parts = split_long_message(response)
    for i, part in enumerate(message_parts):
        try:
            # Добавляем индикатор части для длинных сообщений
            if len(message_parts) > 1:
                part_indicator = f"[Часть {i+1}/{len(message_parts)}]\n"
                if i > 0:  # Для всех частей, кроме первой, добавляем индикатор в начало
                    part = part_indicator + part
                else:  # Для первой части добавляем индикатор только если он поместится
                    if len(part) + len(part_indicator) <= MAX_MESSAGE_LENGTH:
                        part = part_indicator + part
            
            update.message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # Если не удалось отправить с Markdown, пробуем без форматирования
            logger.warning(f"Ошибка при отправке с Markdown: {e}")
            update.message.reply_text(part)
    
    return ConversationHandler.END

def handle_gpt_prompt(update: Update, context: CallbackContext) -> int:
    """Обработчик ввода запроса для GPT после нажатия кнопки GPT"""
    user_id = update.effective_user.id
    prompt = update.message.text
    
    # Инициализация истории пользователя, если её нет
    if user_id not in user_history:
        user_history[user_id] = []
    
    # Отправка "печатает..."
    context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # Получение ответа от GPT
    response, assistant_message = get_gpt_response(prompt, history=user_history[user_id])
    
    # Добавление сообщений в историю
    user_history[user_id].append({"role": "user", "content": prompt})
    if assistant_message:
        user_history[user_id].append(assistant_message)
    
    # Разбиваем длинное сообщение на части и отправляем
    message_parts = split_long_message(response)
    for i, part in enumerate(message_parts):
        try:
            # Добавляем индикатор части для длинных сообщений
            if len(message_parts) > 1:
                part_indicator = f"[Часть {i+1}/{len(message_parts)}]\n"
                if i > 0:  # Для всех частей, кроме первой, добавляем индикатор в начало
                    part = part_indicator + part
                else:  # Для первой части добавляем индикатор только если он поместится
                    if len(part) + len(part_indicator) <= MAX_MESSAGE_LENGTH:
                        part = part_indicator + part
            
            update.message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # Если не удалось отправить с Markdown, пробуем без форматирования
            logger.warning(f"Ошибка при отправке с Markdown: {e}")
            update.message.reply_text(part)
    
    return ConversationHandler.END

def handle_image_prompt(update: Update, context: CallbackContext) -> int:
    """Обработчик ввода описания изображения после нажатия кнопки Изображение"""
    prompt = update.message.text
    
    # Отправка "отправляет фото..."
    context.bot.send_chat_action(chat_id=update.effective_chat.id, action='upload_photo')
    
    # Генерация изображения
    img_data = generate_image(prompt)
    
    if img_data:
        # Отправка изображения
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            tmp_file.write(img_data)
            tmp_file_path = tmp_file.name
        
        with open(tmp_file_path, 'rb') as f:
            update.message.reply_photo(
                photo=f, 
                caption=f"Сгенерировано по запросу: {prompt}"
            )
        
        # Удаление временного файла
        os.unlink(tmp_file_path)
        
        # Убираем очистку истории пользователя после генерации изображения
        # user_id = update.effective_user.id
        # if user_id in user_history:
        #     user_history[user_id] = []
    else:
        update.message.reply_text('Не удалось сгенерировать изображение. Попробуйте другой запрос.')
    
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена текущего диалога"""
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="Операция отменена.")
    return ConversationHandler.END

def handle_gpt_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /gpt"""
    user_id = update.effective_user.id
    
    if not context.args:
        update.message.reply_text('Пожалуйста, добавьте сообщение после команды /gpt')
        return
    
    prompt = ' '.join(context.args)
    
    # Инициализация истории пользователя, если её нет
    if user_id not in user_history:
        user_history[user_id] = []
    
    # Отправка "печатает..."
    context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # Получение ответа от GPT
    response, assistant_message = get_gpt_response(prompt, history=user_history[user_id])
    
    # Добавление сообщений в историю
    user_history[user_id].append({"role": "user", "content": prompt})
    if assistant_message:
        user_history[user_id].append(assistant_message)
    
    # Разбиваем длинное сообщение на части и отправляем
    message_parts = split_long_message(response)
    for i, part in enumerate(message_parts):
        try:
            # Добавляем индикатор части для длинных сообщений
            if len(message_parts) > 1:
                part_indicator = f"[Часть {i+1}/{len(message_parts)}]\n"
                if i > 0:  # Для всех частей, кроме первой, добавляем индикатор в начало
                    part = part_indicator + part
                else:  # Для первой части добавляем индикатор только если он поместится
                    if len(part) + len(part_indicator) <= MAX_MESSAGE_LENGTH:
                        part = part_indicator + part
            
            update.message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # Если не удалось отправить с Markdown, пробуем без форматирования
            logger.warning(f"Ошибка при отправке с Markdown: {e}")
            update.message.reply_text(part)

def handle_image_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /image"""
    if not context.args:
        update.message.reply_text('Пожалуйста, добавьте описание изображения после команды /image')
        return
    
    prompt = ' '.join(context.args)
    
    # Отправка "отправляет фото..."
    context.bot.send_chat_action(chat_id=update.effective_chat.id, action='upload_photo')
    
    # Генерация изображения
    img_data = generate_image(prompt)
    
    if img_data:
        # Отправка изображения
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            tmp_file.write(img_data)
            tmp_file_path = tmp_file.name
        
        with open(tmp_file_path, 'rb') as f:
            update.message.reply_photo(
                photo=f, 
                caption=f"Сгенерировано по запросу: {prompt}"
            )
        
        # Удаление временного файла
        os.unlink(tmp_file_path)
        
        # Убираем очистку истории пользователя после генерации изображения
        # user_id = update.effective_user.id
        # if user_id in user_history:
        #     user_history[user_id] = []
    else:
        update.message.reply_text('Не удалось сгенерировать изображение. Попробуйте другой запрос.')

def clear_history(update: Update, context: CallbackContext) -> None:
    """Очистить историю сообщений пользователя"""
    user_id = update.effective_user.id
    if user_id in user_history:
        user_history[user_id] = []
    update.message.reply_text('История сообщений очищена.')

def list_models(update: Update, context: CallbackContext) -> None:
    """Показать список доступных моделей"""
    models_list = "\n".join(available_models)
    update.message.reply_text(f'Доступные модели:\n{models_list}')

def split_long_message(text, max_length=MAX_MESSAGE_LENGTH):
    """Разбивает длинное сообщение на части подходящей длины"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while text:
        # Находим подходящее место для разделения (предпочтительно на переносе строки или пробеле)
        if len(text) <= max_length:
            parts.append(text)
            break
        
        # Ищем последний перенос строки в пределах max_length
        split_point = text[:max_length].rfind('\n')
        if split_point == -1 or split_point < max_length // 2:
            # Если перенос строки не найден или слишком близко к началу,
            # ищем последний пробел
            split_point = text[:max_length].rfind(' ')
            if split_point == -1 or split_point < max_length // 2:
                # Если и пробел не найден, просто разделяем по максимальной длине
                split_point = max_length
        else:
            # Если нашли перенос строки, включаем его в текущую часть
            split_point += 1
        
        parts.append(text[:split_point])
        text = text[split_point:]
    
    return parts

def main() -> None:
    """Основная функция для запуска бота"""
    # Создание Updater и передача токена бота
    updater = Updater(TOKEN)

    # Получение диспетчера для регистрации обработчиков
    dispatcher = updater.dispatcher

    # Создаем обработчик разговора для кнопок меню
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.text & ~Filters.command, handle_message)],
        states={
            WAITING_FOR_GPT_PROMPT: [MessageHandler(Filters.text & ~Filters.command, handle_gpt_prompt)],
            WAITING_FOR_IMAGE_PROMPT: [MessageHandler(Filters.text & ~Filters.command, handle_image_prompt)],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern='^cancel$')],
    )
    
    # Регистрация обработчиков команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("gpt", handle_gpt_command))
    dispatcher.add_handler(CommandHandler("image", handle_image_command))
    
    # Регистрация обработчика разговора
    dispatcher.add_handler(conv_handler)
    
    # Регистрация обработчика callback-запросов для кнопок
    dispatcher.add_handler(CallbackQueryHandler(cancel))

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
