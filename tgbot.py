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

# Режимы работы бота
MODE_TEXT = "text"
MODE_IMAGE = "image"

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
# Режим работы для каждого пользователя (по умолчанию - текст)
user_mode = {}

def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = update.effective_user.id
    
    # Инициализируем историю пользователя без системного сообщения о разметке
    user_history[user_id] = []
    # Устанавливаем режим по умолчанию - текст
    user_mode[user_id] = MODE_TEXT
    
    # Создаем клавиатуру с основными командами
    keyboard = [
        [KeyboardButton("🤖 GPT"), KeyboardButton("🎨 Изображение")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f'Привет, {user.first_name}! Я бот, работающий с GPT. '
        f'Вы можете отправить мне сообщение, и я отвечу, используя GPT4free.\n\n'
        f'Текущие настройки:\n'
        f'- Текстовые ответы: gpt-4o-mini\n'
        f'- Генерация изображений: flux\n\n'
        f'Нажмите кнопку "🤖 GPT" для генерации текста или "🎨 Изображение" для генерации картинок.\n'
        f'Текущий режим: Генерация текста',
        reply_markup=reply_markup
    )

def get_gpt_response(prompt, model=text_model, history=None):
    """Получить ответ от GPT4free"""
    try:
        messages = []
        if history:
            messages.extend(history)
        
        messages.append({"role": "user", "content": prompt})
        
        # Флаг для отслеживания, получили ли мы потоковый ответ
        received_streaming_response = False
        
        try:
            # Пробуем использовать клиент API
            client = Client()
            
            try:
                # Сначала пробуем получить полный ответ без стриминга
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=False,  # Явно указываем, что не хотим стриминг
                )
                
                # Проверяем, не является ли ответ потоковым, несмотря на наши настройки
                if hasattr(response, 'choices') and hasattr(response.choices[0], 'message'):
                    response_text = response.choices[0].message.content
                else:
                    # Если ответ все-таки потоковый, собираем его вручную
                    logger.warning("Получен потоковый ответ, несмотря на stream=False")
                    received_streaming_response = True
                    full_response = ""
                    
                    # Если response - это генератор или итератор
                    if hasattr(response, '__iter__') or hasattr(response, '__next__'):
                        for chunk in response:
                            if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                                if hasattr(chunk.choices[0], 'delta') and hasattr(chunk.choices[0].delta, 'content'):
                                    content = chunk.choices[0].delta.content
                                    if content:
                                        full_response += content
                                elif hasattr(chunk.choices[0], 'message') and hasattr(chunk.choices[0].message, 'content'):
                                    content = chunk.choices[0].message.content
                                    if content:
                                        full_response += content
                    
                    response_text = full_response if full_response else "Не удалось получить ответ от модели."
            
            except Exception as stream_error:
                # Если произошла ошибка при попытке получить ответ без стриминга,
                # попробуем явно использовать стриминг и собрать ответ вручную
                logger.warning(f"Ошибка при получении ответа без стриминга: {stream_error}. Пробуем со стримингом.")
                received_streaming_response = True
                
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,  # Явно запрашиваем стриминг
                )
                
                full_response = ""
                for chunk in response:
                    if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                        if hasattr(chunk.choices[0], 'delta') and hasattr(chunk.choices[0].delta, 'content'):
                            content = chunk.choices[0].delta.content
                            if content:
                                full_response += content
                
                response_text = full_response if full_response else "Не удалось получить ответ от модели."
                
        except Exception as client_error:
            logger.warning(f"Ошибка при использовании клиента: {client_error}")
            # Если клиент не работает, используем старый метод
            try:
                # Пробуем сначала без стриминга
                response_text = g4f.ChatCompletion.create(
                    model=model,
                    messages=messages,
                    stream=False,
                )
                
                # Проверяем, не является ли ответ потоковым или словарем с данными
                if isinstance(response_text, (list, tuple, set)) or hasattr(response_text, '__iter__') or isinstance(response_text, dict):
                    logger.warning("g4f.ChatCompletion вернул не строку. Обрабатываем специальным образом.")
                    received_streaming_response = True
                    
                    full_response = ""
                    
                    # Если это итерируемый объект
                    if hasattr(response_text, '__iter__') and not isinstance(response_text, (str, dict)):
                        for chunk in response_text:
                            if isinstance(chunk, str):
                                full_response += chunk
                            elif isinstance(chunk, dict) and 'content' in chunk:
                                full_response += chunk['content']
                    # Если это словарь
                    elif isinstance(response_text, dict):
                        if 'content' in response_text:
                            full_response = response_text['content']
                        elif 'message' in response_text and 'content' in response_text['message']:
                            full_response = response_text['message']['content']
                    
                    response_text = full_response if full_response else "Не удалось получить ответ от модели."
                
                # Если ответ содержит "data: {" - это признак потокового ответа в текстовом формате
                elif isinstance(response_text, str) and "data: {" in response_text:
                    logger.warning("Обнаружен потоковый ответ в текстовом формате")
                    received_streaming_response = True
                    
                    # Извлекаем содержимое из строк вида "data: {"content":"текст"}"
                    full_response = ""
                    for line in response_text.split('\n'):
                        if line.startswith('data: {'):
                            try:
                                # Пытаемся извлечь JSON из строки
                                import json
                                data_str = line.replace('data: ', '')
                                data = json.loads(data_str)
                                if 'content' in data and data['content']:
                                    full_response += data['content']
                            except Exception as json_error:
                                logger.error(f"Ошибка при разборе JSON из потокового ответа: {json_error}")
                    
                    response_text = full_response if full_response else "Не удалось получить ответ от модели."
                
            except Exception as g4f_error:
                logger.error(f"Ошибка при использовании g4f.ChatCompletion: {g4f_error}")
                
                # Последняя попытка - явно запросить стриминг и собрать ответ
                try:
                    logger.info("Пробуем явно запросить стриминг через g4f.ChatCompletion")
                    received_streaming_response = True
                    
                    response_stream = g4f.ChatCompletion.create(
                        model=model,
                        messages=messages,
                        stream=True,
                    )
                    
                    full_response = ""
                    for chunk in response_stream:
                        if isinstance(chunk, str):
                            full_response += chunk
                        elif isinstance(chunk, dict) and 'content' in chunk:
                            full_response += chunk['content']
                    
                    response_text = full_response if full_response else "Не удалось получить ответ от модели."
                except Exception as stream_error:
                    logger.error(f"Ошибка при использовании стриминга через g4f.ChatCompletion: {stream_error}")
                    response_text = f"Произошла ошибка при получении ответа: {str(g4f_error)}"
        
        # Финальная проверка на потоковый формат в текстовом ответе
        if isinstance(response_text, str) and "data: {" in response_text:
            logger.warning("Финальная проверка: обнаружен потоковый ответ в текстовом формате")
            received_streaming_response = True
            
            # Извлекаем содержимое из строк вида "data: {"content":"текст"}"
            full_response = ""
            for line in response_text.split('\n'):
                if line.startswith('data: {'):
                    try:
                        # Пытаемся извлечь JSON из строки
                        import json
                        data_str = line.replace('data: ', '')
                        data = json.loads(data_str)
                        if 'content' in data and data['content']:
                            full_response += data['content']
                    except Exception as json_error:
                        logger.error(f"Ошибка при разборе JSON из потокового ответа: {json_error}")
            
            if full_response:
                response_text = full_response
        
        # Логируем информацию о типе полученного ответа
        if received_streaming_response:
            logger.info("Был получен и обработан потоковый ответ")
        
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
        
        try:
            # Генерируем изображение
            response = client.images.generate(
                model="flux",
                prompt=prompt,
                response_format="url",
                width=1024,  # Уменьшаем размер для большей надежности
                height=1024
            )
            
            # Получаем URL изображения
            if response and hasattr(response, 'data') and len(response.data) > 0:
                image_url = response.data[0].url
                logger.info(f"Получен URL изображения: {image_url}")
                
                # Загружаем изображение
                img_response = requests.get(image_url, timeout=60)
                
                if img_response.status_code == 200:
                    # Проверяем, что полученные данные - действительно изображение
                    try:
                        img = Image.open(BytesIO(img_response.content))
                        # Преобразуем изображение в JPEG для гарантии совместимости с Telegram
                        buffer = BytesIO()
                        img.convert('RGB').save(buffer, format='JPEG')
                        buffer.seek(0)
                        
                        return buffer.getvalue()
                    except Exception as img_error:
                        logger.error(f"Ошибка при обработке изображения: {img_error}")
                        return None
                else:
                    logger.error(f"Ошибка при загрузке изображения: {img_response.status_code}")
                    return None
            else:
                logger.error("Не удалось получить URL изображения из ответа API")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при генерации изображения через client.images.generate: {e}")
            
            # Пробуем альтернативный метод
            try:
                logger.info("Пробуем альтернативный метод g4f.images.create")
                img_url = g4f.images.create(
                    prompt=prompt,
                    model="flux"
                )
                
                if img_url:
                    img_response = requests.get(img_url, timeout=60)
                    
                    if img_response.status_code == 200:
                        # Проверяем, что полученные данные - действительно изображение
                        try:
                            img = Image.open(BytesIO(img_response.content))
                            # Преобразуем изображение в JPEG
                            buffer = BytesIO()
                            img.convert('RGB').save(buffer, format='JPEG')
                            buffer.seek(0)
                            
                            return buffer.getvalue()
                        except Exception as img_error:
                            logger.error(f"Ошибка при обработке изображения: {img_error}")
                            return None
                    else:
                        logger.error(f"Ошибка при загрузке изображения: {img_response.status_code}")
                        return None
                else:
                    logger.error("Не удалось получить URL изображения из g4f.images.create")
                    return None
            except Exception as alt_error:
                logger.error(f"Ошибка при использовании альтернативного метода: {alt_error}")
                return None
                
    except Exception as e:
        logger.error(f"Общая ошибка при генерации изображения: {e}")
        return None

def handle_message(update: Update, context: CallbackContext) -> None:
    """Обработчик обычных сообщений"""
    user_id = update.effective_user.id
    prompt = update.message.text
    
    # Обработка кнопок меню
    if prompt == "🤖 GPT":
        # Переключаем режим на генерацию текста
        user_mode[user_id] = MODE_TEXT
        update.message.reply_text(
            "Режим переключен на генерацию текста. Теперь все ваши сообщения будут обрабатываться как запросы к GPT."
        )
        return
    
    elif prompt == "🎨 Изображение":
        # Переключаем режим на генерацию изображений
        user_mode[user_id] = MODE_IMAGE
        update.message.reply_text(
            "Режим переключен на генерацию изображений. Теперь все ваши сообщения будут обрабатываться как запросы на создание изображений."
        )
        return
    
    # Инициализация истории пользователя, если её нет
    if user_id not in user_history:
        user_history[user_id] = []
    
    # Если пользователь не имеет режима, устанавливаем по умолчанию
    if user_id not in user_mode:
        user_mode[user_id] = MODE_TEXT
    
    # Обработка сообщения в зависимости от текущего режима
    if user_mode[user_id] == MODE_TEXT:
        # Режим генерации текста
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
    
    elif user_mode[user_id] == MODE_IMAGE:
        # Режим генерации изображений
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
        else:
            update.message.reply_text('Не удалось сгенерировать изображение. Попробуйте другой запрос.')

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

    # Регистрация обработчиков команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("gpt", handle_gpt_command))
    dispatcher.add_handler(CommandHandler("image", handle_image_command))
    dispatcher.add_handler(CommandHandler("clear", clear_history))
    dispatcher.add_handler(CommandHandler("models", list_models))
    
    # Регистрация обработчика обычных сообщений
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Регистрация обработчика callback-запросов для кнопок
    dispatcher.add_handler(CallbackQueryHandler(cancel))

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
