"""
Yeah HQ Bot v3.0
@YeahHQ_Bot
Полностью переработан: кнопочное меню команд, кнопочное добавление в группу с авто-администратором,
платные функции с кнопкой «Купить», мини-приложение, расширенная статистика, исправлены баги.
"""

import logging
import random
import os
import threading
from datetime import datetime, timedelta
from aiohttp import web as aiohttp_web

from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

import database as db
from config import BOT_TOKEN, BOT_USERNAME, PRICES, MINI_APP_URL, WEB_PORT

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  ИЕРАРХИЯ РАНГОВ
# ═══════════════════════════════════════════════════════════════════

ROLES_HIERARCHY = [
    {"name": "Участник",      "rank": 1, "emoji": "👤"},
    {"name": "Модератор",     "rank": 2, "emoji": "🔨"},
    {"name": "Админ",         "rank": 3, "emoji": "🛡"},
    {"name": "Старший админ", "rank": 4, "emoji": "⚡"},
    {"name": "Создатель",     "rank": 5, "emoji": "👑"},
]
RANK_BY_NAME = {r["name"].lower(): r for r in ROLES_HIERARCHY}

active_duels: dict = {}
flood_tracker: dict = {}

# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

async def get_tg_status(chat_id, user_id, context) -> str:
    try:
        m = await context.bot.get_chat_member(chat_id, user_id)
        return m.status
    except Exception:
        return "left"

async def get_rank(chat_id, user_id, context) -> int:
    if db.is_bot_owner(user_id):
        return 9999
    if chat_id is None:
        return 1
    status = await get_tg_status(chat_id, user_id, context)
    if status == ChatMemberStatus.OWNER:
        return 1000
    if status == ChatMemberStatus.ADMINISTRATOR:
        return 900
    role = db.get_role(chat_id, user_id)
    return role["rank"] if role else 1

async def can_act(acting_id, target_id, chat_id, context):
    if db.is_bot_owner(target_id):
        return False, "❌ Этот пользователь — <b>владелец бота</b> и защищён от любых действий."
    t_status = await get_tg_status(chat_id, target_id, context)
    a_status = await get_tg_status(chat_id, acting_id, context)
    if t_status == ChatMemberStatus.ADMINISTRATOR and a_status != ChatMemberStatus.OWNER and not db.is_bot_owner(acting_id):
        return False, "❌ Telegram-администратора может наказывать только <b>владелец группы</b>."
    ar = await get_rank(chat_id, acting_id, context)
    tr = await get_rank(chat_id, target_id, context)
    if ar <= tr:
        return False, "❌ Нельзя применять действия к участнику с <b>равным или более высоким</b> рангом."
    return True, ""

async def resolve_target(update, context):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        ident = context.args[0].lstrip("@")
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, int(ident))
            return m.user
        except Exception:
            pass
        u = db.find_user_by_username(ident)
        if u:
            try:
                m = await context.bot.get_chat_member(update.effective_chat.id, u["user_id"])
                return m.user
            except Exception:
                pass
    return None

def group_only(f):
    async def w(update, context):
        if update.effective_chat.type not in ("group", "supergroup"):
            await update.message.reply_text("❌ Команда работает только в группах.")
            return
        if db.is_chat_disabled(update.effective_chat.id):
            return
        return await f(update, context)
    w.__name__ = f.__name__
    return w

def require_premium(feature):
    def dec(f):
        async def w(update, context):
            user = update.effective_user
            chat = update.effective_chat
            if db.is_bot_owner(user.id) or db.has_free_grant(user.id, feature) or db.has_feature(user.id, feature):
                return await f(update, context)
            try:
                for a in await context.bot.get_chat_administrators(chat.id):
                    if a.status == ChatMemberStatus.OWNER:
                        if db.has_feature(a.user.id, feature) or db.has_free_grant(a.user.id, feature):
                            return await f(update, context)
                        break
            except Exception:
                pass
            price = PRICES.get(feature, 30)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🛒 Купить ({price} ⭐)", callback_data=f"buy_{feature}"),
            ]])
            await update.message.reply_text(
                f"❌ Эта функция <b>платная</b>.\n"
                f"💡 Купите за <b>{price} ⭐ Stars</b> — доступ сразу во всех группах!",
                parse_mode="HTML",
                reply_markup=kb
            )
        w.__name__ = f.__name__
        return w
    return dec

async def answer_text(update: Update, text: str, **kw):
    await update.message.reply_text(text, parse_mode="HTML", **kw)

# ═══════════════════════════════════════════════════════════════════
#  КНОПОЧНОЕ МЕНЮ КОМАНД
# ═══════════════════════════════════════════════════════════════════

COMMANDS_SECTIONS = {
    "cmd_section_mod": {
        "title": "🔨 Модерация",
        "commands": [
            ("🔇 Замутить", "/mute @user [мин]"),
            ("🔊 Размутить", "/unmute @user"),
            ("👢 Кикнуть", "/kick @user"),
            ("🚫 Забанить", "/ban @user [дней]"),
            ("✅ Разбанить", "/unban @user"),
            ("⚠️ Предупреждение (платно)", "/warn @user [причина]", True),
            ("🧹 Снять варны (платно)", "/unwarn @user", True),
        ]
    },
    "cmd_section_roles": {
        "title": "🎖 Должности",
        "commands": [
            ("⬆️ Повысить", "/promote @user [роль]"),
            ("⬇️ Понизить", "/demote @user"),
            ("👥 Список ролей", "/roles"),
            ("🆔 Моя должность", "/whoami"),
        ]
    },
    "cmd_section_fun": {
        "title": "🎮 Развлечения",
        "commands": [
            ("⚔️ Дуэль (платно)", "/duel @user", True),
            ("🎰 Рулетка (платно)", "/luck", True),
            ("🎲 Казино (платно)", "/casino", True),
            ("💍 Предложение (платно)", "/marry @user", True),
            ("💔 Развод (платно)", "/divorce", True),
            ("💑 Пары (платно)", "/marriages", True),
        ]
    },
    "cmd_section_settings": {
        "title": "⚙️ Настройки",
        "commands": [
            ("👋 Приветствие (платно)", "/welcome Текст", True),
            ("📜 Правила (платно)", "/rules Текст", True),
            ("🛡️ Антифлуд (платно)", "/antiflood N", True),
            ("📊 Голосование (платно)", "/poll Вопрос|Вар1|Вар2", True),
            ("📌 Заметки (платно)", "/note [add|get|del|list]", True),
            ("🔍 Фильтры (платно)", "/filter keyword ответ", True),
        ]
    },
    "cmd_section_profile": {
        "title": "👤 Профиль & Статистика",
        "commands": [
            ("👤 Профиль", "/profile [@user]"),
            ("🏅 Достижения", "/achievements"),
            ("🏆 Топ активности", "/top"),
            ("📊 Статистика чата", "/chatstats"),
        ]
    },
}

async def show_commands_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню разделов команд."""
    kb = []
    for cb_key, section in COMMANDS_SECTIONS.items():
        kb.append([InlineKeyboardButton(section["title"], callback_data=cb_key)])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")])
    text = "📋 <b>Команды Yeah HQ Bot</b>\n\nВыберите раздел:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def commands_section_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "back_to_start":
        await start_inline(update, context)
        return

    if q.data == "back_to_commands":
        await show_commands_menu(update, context)
        return

    section = COMMANDS_SECTIONS.get(q.data)
    if not section:
        return

    kb = []
    has_premium = False
    for cmd_info in section["commands"]:
        name = cmd_info[0]
        usage = cmd_info[1]
        is_premium = len(cmd_info) > 2 and cmd_info[2]
        if is_premium:
            has_premium = True
        kb.append([InlineKeyboardButton(name, callback_data=f"cmd_info:{usage}")])

    kb.append([InlineKeyboardButton("🔙 К разделам", callback_data="back_to_commands")])
    if has_premium:
        kb.append([InlineKeyboardButton("🛒 Купить платные функции", callback_data="open_shop")])

    await q.edit_message_text(
        f"{section['title']}\n\nВыберите команду для справки:\n"
        "<i>(платно) = платная функция</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_info_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data.startswith("cmd_info:"):
        usage = q.data[9:]
        await q.answer(f"Использование: {usage}", show_alert=True)

async def start_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактировать или отправить стартовое сообщение."""
    user = update.effective_user if not update.callback_query else update.callback_query.from_user
    is_owner = db.is_bot_owner(user.id)
    owner_badge = " 🌟" if is_owner else ""

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🛒 Магазин"),         KeyboardButton("📋 Команды")],
            [KeyboardButton("➕ Добавить в группу"), KeyboardButton("👤 Мой профиль")],
            [KeyboardButton("📊 Статистика"),       KeyboardButton("📱 Мини-приложение")],
        ],
        resize_keyboard=True
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(
            f"👋 Привет, <b>{user.first_name}{owner_badge}</b>!\n\nВоспользуйтесь меню ниже 👇",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        await update.message.reply_text(
            f"👋 Привет, <b>{user.first_name}{owner_badge}</b>!\n\nВоспользуйтесь меню ниже 👇",
            parse_mode="HTML",
            reply_markup=kb
        )

# ═══════════════════════════════════════════════════════════════════
#  START / HELP
# ═══════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or "")
    is_owner = db.is_bot_owner(user.id)

    if update.effective_chat.type == "private":
        owner_note = "\n\n🌟 <b>Вы — владелец бота!</b> Все функции бесплатны." if is_owner else ""

        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton("🛒 Магазин"),          KeyboardButton("📋 Команды")],
                [KeyboardButton("➕ Добавить в группу"), KeyboardButton("👤 Мой профиль")],
                [KeyboardButton("📊 Статистика"),        KeyboardButton("📱 Мини-приложение")],
            ],
            resize_keyboard=True
        )

        await update.message.reply_text(
            f"👋 Привет, <b>{user.first_name}</b>! Добро пожаловать в <b>Yeah HQ Bot</b> 🎉\n\n"
            "Я — продвинутый менеджер для Telegram-групп. Вот что я умею:\n\n"
            "🔨 <b>Модерация</b> — мут, бан, кик, варны с автобаном на 3-м предупреждении\n"
            "🎖 <b>Система рангов</b> — 5 уровней: Участник, Модератор, Админ, Ст. Админ, Создатель\n"
            "⚔️ <b>Дуэли</b> — интерактивные бои с HP, прицеливанием и уклонением\n"
            "💍 <b>Браки</b> — предложение руки и сердца прямо в чате\n"
            "🎰 <b>Казино & Рулетка</b> — развлечения для активных участников\n"
            "📋 <b>Заметки & Фильтры</b> — сохраняйте важные тексты и автоответы\n"
            "🏆 <b>Топ активности</b> — рейтинг самых активных участников\n"
            "📊 <b>Голосования</b> — быстрые опросы одной командой\n"
            "🛡️ <b>Антифлуд</b> — автоматический мут за спам\n"
            "📱 <b>Мини-приложение</b> — удобное управление прямо в Telegram\n\n"
            "Используйте кнопки меню внизу для навигации 👇" + owner_note,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        # В группе — регистрируем группу
        db.register_group(update.effective_chat.id, update.effective_chat.title or "")
        await update.message.reply_text(
            "✅ <b>Yeah HQ Bot</b> активен в этой группе!\n"
            "Напишите мне в личку /start для настройки.",
            parse_mode="HTML"
        )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_commands_menu(update, context)

# ═══════════════════════════════════════════════════════════════════
#  ДОБАВЛЕНИЕ В ГРУППУ — КНОПКОЙ С АВТО-АДМИНИСТРАТОРОМ
# ═══════════════════════════════════════════════════════════════════

async def add_to_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает кнопку добавления бота в группу с запросом прав администратора."""
    # Ссылка с запросом всех необходимых прав (TG автоматически сделает бота админом)
    add_link = (
        f"https://t.me/{BOT_USERNAME}?startgroup=true"
        f"&admin=restrict_members+ban_members+pin_messages+invite_users+delete_messages+manage_chat"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Добавить Yeah HQ Bot в группу",
            url=add_link
        )],
        [InlineKeyboardButton("❓ Зачем нужны права администратора?", callback_data="why_admin")],
    ])

    await update.message.reply_text(
        "➕ <b>Добавить Yeah HQ Bot в группу</b>\n\n"
        "Нажмите на кнопку ниже — откроется выбор группы. "
        "Бот <b>автоматически получит права администратора</b>, "
        "необходимые для работы всех функций.",
        parse_mode="HTML",
        reply_markup=kb
    )

async def why_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer(
        "Бот нуждается в правах для: мута/бана пользователей, удаления сообщений, "
        "закрепления постов и управления группой.",
        show_alert=True
    )

# ═══════════════════════════════════════════════════════════════════
#  ОБРАБОТЧИК КНОПОК НИЖНЕЙ КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════════

async def keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🛒 Магазин":
        await shop_cmd(update, context)
    elif text == "📋 Команды":
        await show_commands_menu(update, context)
    elif text == "➕ Добавить в группу":
        await add_to_group_handler(update, context)
    elif text == "👤 Мой профиль":
        context.args = []
        await profile_cmd(update, context)
    elif text == "📊 Статистика":
        if db.is_bot_owner(update.effective_user.id):
            await botstats_cmd(update, context)
        else:
            context.args = []
            await profile_cmd(update, context)
    elif text == "📱 Мини-приложение":
        await miniapp_cmd(update, context)

# ═══════════════════════════════════════════════════════════════════
#  МИНИ-ПРИЛОЖЕНИЕ
# ═══════════════════════════════════════════════════════════════════

async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or "")

    # Кнопка открытия Mini App
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📱 Открыть Yeah HQ",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )
    ]])
    await update.message.reply_text(
        "📱 <b>Yeah HQ Mini App</b>\n\n"
        "Управляйте ботом удобно через мини-приложение:\n"
        "• Просматривайте статистику групп\n"
        "• Управляйте покупками и функциями\n"
        "• Смотрите топ активности\n"
        "• Настраивайте правила и приветствие",
        parse_mode="HTML",
        reply_markup=kb
    )

# ═══════════════════════════════════════════════════════════════════
#  МОДЕРАЦИЯ
# ═══════════════════════════════════════════════════════════════════

@group_only
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    db.ensure_user(acting.id, acting.username or "")
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя (реплай или @username).")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    mins = 10
    idx = 0 if update.message.reply_to_message else 1
    if context.args and len(context.args) > idx:
        try:
            mins = max(1, int(context.args[idx]))
        except ValueError:
            pass
    try:
        await context.bot.restrict_chat_member(
            chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(minutes=mins)
        )
        db.log_action(chat.id, acting.id, target.id, "mute", f"{mins}m")
        db.add_activity(chat.id, acting.id)
        await answer_text(update, f"🔇 {target.mention_html()} замолчан на <b>{mins} мин.</b>")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    try:
        await context.bot.restrict_chat_member(
            chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        db.log_action(chat.id, acting.id, target.id, "unmute", "")
        await answer_text(update, f"🔊 {target.mention_html()} разглушён.")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    try:
        await context.bot.ban_chat_member(chat.id, target.id)
        await context.bot.unban_chat_member(chat.id, target.id)
        db.log_action(chat.id, acting.id, target.id, "kick", "")
        await answer_text(update, f"👢 {target.mention_html()} кикнут из чата.")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    days = 1
    idx = 0 if update.message.reply_to_message else 1
    if context.args and len(context.args) > idx:
        try:
            days = max(1, int(context.args[idx]))
        except ValueError:
            pass
    try:
        await context.bot.ban_chat_member(
            chat.id, target.id,
            until_date=datetime.now() + timedelta(days=days)
        )
        db.log_action(chat.id, acting.id, target.id, "ban", f"{days}d")
        await answer_text(update, f"🚫 {target.mention_html()} забанен на <b>{days} дн.</b>")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ar = await get_rank(chat.id, acting.id, context)
    if ar < 900:
        return await answer_text(update, "❌ Недостаточно прав.")
    try:
        await context.bot.unban_chat_member(chat.id, target.id)
        db.log_action(chat.id, acting.id, target.id, "unban", "")
        await answer_text(update, f"✅ {target.mention_html()} разбанен.")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
@require_premium("warns")
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    idx = 0 if update.message.reply_to_message else 1
    cause = " ".join(context.args[idx:]) if context.args and len(context.args) > idx else "нет причины"
    count = db.add_warn(chat.id, target.id)
    db.log_action(chat.id, acting.id, target.id, "warn", cause)
    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat.id, target.id)
            db.reset_warns(chat.id, target.id)
            await answer_text(update,
                f"🚫 {target.mention_html()} получил 3-е предупреждение и <b>автоматически забанен</b>!")
        except TelegramError:
            pass
    else:
        await answer_text(update,
            f"⚠️ {target.mention_html()} — предупреждение <b>{count}/3</b>\nПричина: {cause}")

@group_only
@require_premium("warns")
async def unwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    db.reset_warns(chat.id, target.id)
    await answer_text(update, f"✅ Все предупреждения с {target.mention_html()} сняты.")

# Пин-сообщений
@group_only
@require_premium("pin")
async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    acting = update.effective_user
    ar = await get_rank(chat.id, acting.id, context)
    if ar < 900:
        return await answer_text(update, "❌ Нужны права администратора.")
    if not update.message.reply_to_message:
        return await answer_text(update, "❌ Ответьте на сообщение которое хотите закрепить.")
    try:
        await context.bot.pin_chat_message(chat.id, update.message.reply_to_message.message_id)
        await answer_text(update, "📌 Сообщение закреплено.")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

@group_only
@require_premium("pin")
async def unpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    acting = update.effective_user
    ar = await get_rank(chat.id, acting.id, context)
    if ar < 900:
        return await answer_text(update, "❌ Нужны права администратора.")
    try:
        if update.message.reply_to_message:
            await context.bot.unpin_chat_message(chat.id, update.message.reply_to_message.message_id)
        else:
            await context.bot.unpin_chat_message(chat.id)
        await answer_text(update, "📌 Сообщение откреплено.")
    except TelegramError as e:
        await answer_text(update, f"❌ Ошибка: {e}")

# ═══════════════════════════════════════════════════════════════════
#  СИСТЕМА РАНГОВ
# ═══════════════════════════════════════════════════════════════════

@group_only
async def promote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя (реплай или @username).")

    ar = await get_rank(chat.id, acting.id, context)
    tr = await get_rank(chat.id, target.id, context)

    if ar <= tr:
        return await answer_text(update, "❌ Нельзя повысить участника с равным или более высоким рангом.")

    idx = 0 if update.message.reply_to_message else 1
    desired_name = " ".join(context.args[idx:]).strip().lower() if context.args and len(context.args) > idx else None

    if desired_name:
        role = RANK_BY_NAME.get(desired_name)
        if not role:
            names = ", ".join(r["name"] for r in ROLES_HIERARCHY)
            return await answer_text(update, f"❌ Должность не найдена.\nДоступные: {names}")
        if role["rank"] >= ar:
            return await answer_text(update, "❌ Нельзя назначить должность, равную или выше вашей.")
        if role["rank"] <= tr:
            return await answer_text(update, "❌ Указанная должность не выше текущей. Для понижения — /demote.")
        new_role = role
    else:
        cur = db.get_role(chat.id, target.id)
        cur_r = cur["rank"] if cur else 1
        next_r = cur_r + 1
        if next_r > 5:
            return await answer_text(update, "❌ Участник уже на максимальной должности.")
        if next_r >= ar:
            return await answer_text(update, "❌ Следующая должность равна или выше вашей.")
        new_role = next((r for r in ROLES_HIERARCHY if r["rank"] == next_r), None)
        if not new_role:
            return await answer_text(update, "❌ Достигнут максимальный ранг.")

    db.set_role(chat.id, target.id, new_role["name"], new_role["rank"])
    db.ensure_user(target.id, target.username or "")
    await answer_text(update,
        f"⬆️ {target.mention_html()} повышен до <b>{new_role['emoji']} {new_role['name']}</b>!")

@group_only
async def demote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acting = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    ok, reason = await can_act(acting.id, target.id, chat.id, context)
    if not ok:
        return await answer_text(update, reason)
    role = db.get_role(chat.id, target.id)
    if not role or role["rank"] <= 1:
        return await answer_text(update, "❌ Участник уже на минимальной должности.")
    prev = next((r for r in ROLES_HIERARCHY if r["rank"] == role["rank"] - 1), ROLES_HIERARCHY[0])
    db.set_role(chat.id, target.id, prev["name"], prev["rank"])
    await answer_text(update,
        f"⬇️ {target.mention_html()} понижен до <b>{prev['emoji']} {prev['name']}</b>.")

@group_only
async def roles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_roles = db.get_chat_roles(chat.id)

    by_rank: dict = {}
    for r in chat_roles:
        by_rank.setdefault(r["rank"], []).append(r)

    text = f"👥 <b>Должности в чате «{chat.title}»:</b>\n"
    for role in reversed(ROLES_HIERARCHY):
        members = by_rank.get(role["rank"], [])
        if not members:
            continue
        names = []
        for m in members:
            if m.get("username"):
                names.append(f"@{m['username']}")
            else:
                names.append(f"<code>{m['user_id']}</code>")
        text += f"\n{role['emoji']} <b>{role['name']}:</b>\n  " + ", ".join(names) + "\n"

    if not by_rank:
        text += "\nПока никому не назначены должности."
    await answer_text(update, text)

@group_only
async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    await profile_for_user(update, context, user, chat.id)

# ═══════════════════════════════════════════════════════════════════
#  ПРОФИЛЬ
# ═══════════════════════════════════════════════════════════════════

async def profile_for_user(update, context, user, chat_id):
    rank = await get_rank(chat_id, user.id, context)
    role = db.get_role(chat_id, user.id) if chat_id else None
    role_name = role["role_name"] if role else "Участник"
    role_emoji = next((r["emoji"] for r in ROLES_HIERARCHY if r["name"] == role_name), "👤")
    warns = db.get_warns(chat_id, user.id) if chat_id else 0
    dstats = db.get_duel_stats(user.id)
    achiev = db.get_achievements(user.id)
    spouse_id = db.get_spouse_id(user.id, chat_id) if chat_id else None
    is_owner = db.is_bot_owner(user.id)
    is_main = db.is_main_owner(user.id)
    owned = db.get_owned_features(user.id)

    if is_main:
        owner_badge = " 👑 Владелец бота"
    elif is_owner:
        owner_badge = " 🔑 Совладелец бота"
    else:
        owner_badge = ""

    text = (
        f"👤 <b>{user.full_name}</b>{owner_badge}\n"
        f"🆔 <code>{user.id}</code>"
        f"{f' | @{user.username}' if user.username else ''}\n\n"
        f"🎖 Должность: <b>{role_emoji} {role_name}</b>\n"
        f"⚠️ Предупреждения: <b>{warns}/3</b>\n"
        f"⚔️ Дуэли: {dstats['wins']}🏆 / {dstats['losses']}💀\n"
    )
    if owned:
        text += f"💎 Купленных функций: <b>{len(owned)}</b>\n"
    if spouse_id:
        try:
            sm = await context.bot.get_chat_member(chat_id, spouse_id)
            text += f"💍 Супруг(а): {sm.user.mention_html()}\n"
        except Exception:
            text += f"💍 Супруг(а): <code>{spouse_id}</code>\n"
    if achiev:
        badges = []
        for a in achiev:
            d = db.ACHIEVEMENTS_DEF.get(a["achievement"])
            if d:
                badges.append(d[0])
        if badges:
            text += f"🏅 Достижения: {''.join(badges)}\n"
    await answer_text(update, text)

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = (
        await resolve_target(update, context)
        if (context.args or (update.message.reply_to_message))
        else update.effective_user
    )
    if not target:
        target = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat.type in ("group", "supergroup") else None
    await profile_for_user(update, context, target, chat_id)

async def achievements_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    achiev = db.get_achievements(user.id)
    if not achiev:
        return await answer_text(update, "🏅 У вас пока нет достижений. Играйте активнее!")
    text = "🏅 <b>Ваши достижения:</b>\n\n"
    for a in achiev:
        d = db.ACHIEVEMENTS_DEF.get(a["achievement"])
        if d:
            text += f"{d[0]} <b>{d[1]}</b>\n   <i>{d[2]}</i>\n\n"
    await answer_text(update, text)

@group_only
async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    top = db.get_top_activity(chat.id, 10)
    if not top:
        return await answer_text(update, "📊 Активности пока нет.")
    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    text = f"🏆 <b>Топ активности — {chat.title}:</b>\n\n"
    for i, row in enumerate(top):
        name = f"@{row['username']}" if row.get("username") else f"User {row['user_id']}"
        text += f"{medals[i]} {name} — <b>{row['score']} очков</b>\n"
    # Лидер получает ачивку
    if top:
        db.grant_achievement(top[0]["user_id"], "top_1")
    await answer_text(update, text)

@group_only
async def chatstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    s = db.get_stats(chat.id)
    await answer_text(update,
        f"📊 <b>Статистика чата «{chat.title}»:</b>\n\n"
        f"👥 Участников в БД: <b>{s['users']}</b>\n"
        f"⚡ Действий всего: <b>{s['actions']}</b>\n"
        f"  · За день: <b>{s['actions_day']}</b>\n"
        f"  · За неделю: <b>{s['actions_week']}</b>\n"
        f"  · За месяц: <b>{s['actions_month']}</b>\n"
        f"💍 Браков: <b>{s['marriages']}</b>"
    )

# ═══════════════════════════════════════════════════════════════════
#  ЗАМЕТКИ
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("notes")
async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not context.args:
        return await answer_text(update,
            "📌 <b>Заметки</b>\n\n"
            "/note add <имя> <текст> — добавить\n"
            "/note get <имя> — показать\n"
            "/note del <имя> — удалить\n"
            "/note list — список всех"
        )
    sub = context.args[0].lower()
    if sub == "add" and len(context.args) >= 3:
        name = context.args[1].lower()
        content = " ".join(context.args[2:])
        db.set_note(chat.id, name, content)
        await answer_text(update, f"📌 Заметка <b>{name}</b> сохранена.")
    elif sub == "get" and len(context.args) >= 2:
        name = context.args[1].lower()
        c = db.get_note(chat.id, name)
        if c:
            await answer_text(update, f"📌 <b>{name}:</b>\n{c}")
        else:
            await answer_text(update, f"❌ Заметка <b>{name}</b> не найдена.")
    elif sub == "del" and len(context.args) >= 2:
        name = context.args[1].lower()
        db.del_note(chat.id, name)
        await answer_text(update, f"🗑 Заметка <b>{name}</b> удалена.")
    elif sub == "list":
        notes = db.get_all_notes(chat.id)
        if not notes:
            return await answer_text(update, "📋 Заметок нет.")
        await answer_text(update, "📋 <b>Заметки:</b>\n" + "\n".join(f"• {n}" for n in notes))
    else:
        await answer_text(update, "❌ Неверный формат. /note add|get|del|list")

# ═══════════════════════════════════════════════════════════════════
#  ФИЛЬТРЫ
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("filters")
async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not context.args:
        return await answer_text(update,
            "🔍 <b>Фильтры</b>\n\n"
            "/filter <слово> <ответ> — добавить автоответ\n"
            "/filter del <слово> — удалить\n"
            "/filter list — список"
        )
    sub = context.args[0].lower()
    if sub == "del" and len(context.args) >= 2:
        kw = context.args[1].lower()
        db.del_filter(chat.id, kw)
        await answer_text(update, f"🗑 Фильтр <b>{kw}</b> удалён.")
    elif sub == "list":
        fltrs = db.get_filters(chat.id)
        if not fltrs:
            return await answer_text(update, "🔍 Фильтров нет.")
        await answer_text(update, "🔍 <b>Фильтры:</b>\n" + "\n".join(f"• {f['keyword']}" for f in fltrs))
    elif len(context.args) >= 2:
        keyword = context.args[0].lower()
        response = " ".join(context.args[1:])
        db.set_filter(chat.id, keyword, response)
        await answer_text(update, f"✅ Фильтр <b>{keyword}</b> добавлен.")
    else:
        await answer_text(update, "❌ Неверный формат.")

async def filter_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    text = update.message.text.lower()
    filters = db.get_filters(chat.id)
    for f in filters:
        if f["keyword"] in text:
            await update.message.reply_text(f["response"])
            break

# ═══════════════════════════════════════════════════════════════════
#  БРАКИ
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("marry")
async def marry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proposer = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя для предложения.")
    if target.id == proposer.id:
        return await answer_text(update, "❌ Нельзя жениться на себе 😅")
    if target.is_bot:
        return await answer_text(update, "❌ Боты не женятся 🤖")
    if db.is_married(proposer.id, chat.id):
        return await answer_text(update, "❌ Вы уже в браке! Сначала разведитесь: /divorce")
    if db.is_married(target.id, chat.id):
        return await answer_text(update, f"❌ {target.mention_html()} уже состоит в браке.")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💍 Согласен(а)!", callback_data=f"marry_yes:{proposer.id}:{target.id}"),
        InlineKeyboardButton("❌ Отказать",     callback_data=f"marry_no:{proposer.id}:{target.id}"),
    ]])
    msg = await update.message.reply_text(
        f"💌 {proposer.mention_html()} делает предложение {target.mention_html()}!\n\n"
        f"{target.mention_html()}, вы согласны? 💕",
        parse_mode="HTML",
        reply_markup=kb
    )
    db.add_proposal(proposer.id, target.id, chat.id, msg.message_id)

async def marry_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action = parts[0]
    proposer_id = int(parts[1])
    target_id   = int(parts[2])
    chat_id = q.message.chat.id

    if q.from_user.id != target_id:
        await q.answer("❌ Это предложение не вам!", show_alert=True)
        return

    prop = db.get_proposal(proposer_id, target_id, chat_id)
    if not prop:
        await q.edit_message_text("⌛ Предложение устарело.")
        return

    db.remove_proposal(proposer_id, target_id, chat_id)

    if action == "marry_yes":
        if db.is_married(proposer_id, chat_id) or db.is_married(target_id, chat_id):
            await q.edit_message_text("❌ Кто-то уже в браке, свадьба отменяется!")
            return
        db.create_marriage(proposer_id, target_id, chat_id)
        db.grant_achievement(proposer_id, "married")
        db.grant_achievement(target_id,   "married")
        try:
            pm = await context.bot.get_chat_member(chat_id, proposer_id)
            tm = await context.bot.get_chat_member(chat_id, target_id)
            pname = pm.user.mention_html()
            tname = tm.user.mention_html()
        except Exception:
            pname = f"<code>{proposer_id}</code>"
            tname = f"<code>{target_id}</code>"
        await q.edit_message_text(
            f"💍 Поздравляем!\n\n{pname} и {tname} теперь <b>женаты</b>! 🎉💒",
            parse_mode="HTML"
        )
    else:
        await q.edit_message_text("💔 Предложение отклонено.")

@group_only
@require_premium("marry")
async def divorce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    m = db.is_married(user.id, chat.id)
    if not m:
        return await answer_text(update, "❌ Вы не состоите в браке в этом чате.")
    db.divorce(user.id, chat.id)
    await answer_text(update, f"💔 {user.mention_html()} оформил(а) развод.")

@group_only
@require_premium("marry")
async def marriages_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    marriages = db.get_all_marriages(chat.id)
    if not marriages:
        return await answer_text(update, "💍 В этом чате пока нет браков.")
    text = f"💍 <b>Браки в чате «{chat.title}»:</b>\n\n"
    for i, m in enumerate(marriages, 1):
        try:
            u1 = await context.bot.get_chat_member(chat.id, m["user1_id"])
            u2 = await context.bot.get_chat_member(chat.id, m["user2_id"])
            n1 = u1.user.mention_html()
            n2 = u2.user.mention_html()
        except Exception:
            n1 = f"<code>{m['user1_id']}</code>"
            n2 = f"<code>{m['user2_id']}</code>"
        text += f"{i}. {n1} 💕 {n2}\n"
    await answer_text(update, text)

# ═══════════════════════════════════════════════════════════════════
#  ДУЭЛИ
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("duel")
async def duel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    challenger = update.effective_user
    chat = update.effective_chat
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите противника (реплай или @username).")
    if target.id == challenger.id:
        return await answer_text(update, "❌ Нельзя вызвать самого себя!")
    if target.is_bot:
        return await answer_text(update, "❌ Боты не дерутся 🤖")

    for key in active_duels:
        if chat.id == key[0] and (challenger.id in key or target.id in key):
            return await answer_text(update, "❌ Один из игроков уже в дуэли!")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚔️ Принять вызов!", callback_data=f"duel_accept:{challenger.id}:{target.id}"),
        InlineKeyboardButton("🏃 Убежать",        callback_data=f"duel_decline:{challenger.id}:{target.id}"),
    ]])
    await update.message.reply_text(
        f"⚔️ <b>{challenger.mention_html()}</b> вызывает <b>{target.mention_html()}</b> на дуэль!\n\n"
        f"{target.mention_html()}, принимаете ли вы вызов?",
        parse_mode="HTML",
        reply_markup=kb
    )

async def duel_accept_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action       = parts[0]
    challenger_id = int(parts[1])
    target_id     = int(parts[2])
    chat_id = q.message.chat.id

    if action == "duel_decline":
        if q.from_user.id != target_id:
            await q.answer("❌ Это не ваш вызов!", show_alert=True)
            return
        await q.edit_message_text("🏃 Вызов отклонён. Трус!")
        return

    if q.from_user.id != target_id:
        await q.answer("❌ Это предложение не вам!", show_alert=True)
        return

    await q.answer()

    key = (chat_id, challenger_id, target_id)
    active_duels[key] = {
        "hp": {challenger_id: 100, target_id: 100},
        "aim": {challenger_id: 0, target_id: 0},
        "turn": challenger_id,
        "msg_id": q.message.message_id,
        "bot_owner": challenger_id if db.is_bot_owner(challenger_id) else (target_id if db.is_bot_owner(target_id) else None),
    }
    db.grant_achievement(challenger_id, "first_duel")
    db.grant_achievement(target_id, "first_duel")

    await send_duel_state(context, q.message, key)

async def send_duel_state(context, message, key):
    state = active_duels.get(key)
    if not state:
        return
    chat_id, c_id, t_id = key
    c_hp = state["hp"][c_id]
    t_hp = state["hp"][t_id]
    turn = state["turn"]

    try:
        cm = await context.bot.get_chat_member(chat_id, c_id)
        tm = await context.bot.get_chat_member(chat_id, t_id)
        c_mention = cm.user.mention_html()
        t_mention = tm.user.mention_html()
        c_name = cm.user.first_name
        t_name = tm.user.first_name
    except Exception:
        c_name = f"User {c_id}"
        t_name = f"User {t_id}"
        c_mention = f"<code>{c_id}</code>"
        t_mention = f"<code>{t_id}</code>"

    def hp_bar(hp):
        filled = max(0, hp // 10)
        return "🟩" * filled + "⬛" * (10 - filled)

    text = (
        f"⚔️ <b>ДУЭЛЬ!</b>\n\n"
        f"{c_mention}\n❤️ {c_hp}/100 {hp_bar(c_hp)}\n\n"
        f"{t_mention}\n❤️ {t_hp}/100 {hp_bar(t_hp)}\n\n"
        f"🎯 Ход: <b>{c_name if turn == c_id else t_name}</b>"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎯 Прицелиться",  callback_data=f"duel_aim:{c_id}:{t_id}"),
        InlineKeyboardButton("💨 Сбить прицел", callback_data=f"duel_dodge:{c_id}:{t_id}"),
        InlineKeyboardButton("🔫 Стрелять",     callback_data=f"duel_shoot:{c_id}:{t_id}"),
    ]])

    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass

async def duel_action_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action_type = parts[0]
    c_id = int(parts[1])
    t_id = int(parts[2])
    chat_id = q.message.chat.id
    uid = q.from_user.id
    key = (chat_id, c_id, t_id)

    if key not in active_duels:
        await q.answer("Дуэль уже завершена!", show_alert=True)
        return

    state = active_duels[key]
    if uid not in (c_id, t_id):
        await q.answer("❌ Вы не участник этой дуэли!", show_alert=True)
        return

    if state["turn"] != uid:
        await q.answer("⏳ Сейчас не ваш ход!", show_alert=True)
        return

    opponent_id = t_id if uid == c_id else c_id
    bot_owner = state.get("bot_owner")

    if bot_owner == uid:
        action_type = "duel_shoot"

    if action_type == "duel_aim":
        state["aim"][uid] = min(state["aim"][uid] + 30, 80)
        await q.answer("🎯 Прицел улучшен! +30% к точности")
        state["turn"] = opponent_id

    elif action_type == "duel_dodge":
        state["aim"][opponent_id] = max(state["aim"][opponent_id] - 25, 0)
        await q.answer("💨 Сбили прицел противнику! -25% точности")
        state["turn"] = opponent_id

    elif action_type == "duel_shoot":
        base_chance = 50 + state["aim"][uid]
        hit = random.randint(1, 100) <= base_chance or bot_owner == uid
        if hit:
            dmg = random.randint(60, 100) if bot_owner == uid else random.randint(20, 45)
            state["hp"][opponent_id] = max(0, state["hp"][opponent_id] - dmg)
            await q.answer(f"💥 Попал! -{dmg} HP")
        else:
            await q.answer("😬 Промах!")
        state["aim"][uid] = 0
        state["turn"] = opponent_id

    c_hp = state["hp"][c_id]
    t_hp = state["hp"][t_id]

    if c_hp <= 0 or t_hp <= 0:
        winner_id = c_id if t_hp <= 0 else t_id
        loser_id  = t_id if winner_id == c_id else c_id
        del active_duels[key]

        db.record_duel(winner_id, loser_id)
        db.add_activity(chat_id, winner_id, 5)

        stats = db.get_duel_stats(winner_id)
        if stats["wins"] >= 5:
            db.grant_achievement(winner_id, "duel_winner_5")

        try:
            wm = await context.bot.get_chat_member(chat_id, winner_id)
            lm = await context.bot.get_chat_member(chat_id, loser_id)
            wname = wm.user.mention_html()
            lname = lm.user.mention_html()
        except Exception:
            wname = f"<code>{winner_id}</code>"
            lname = f"<code>{loser_id}</code>"

        await q.message.edit_text(
            f"⚔️ <b>ДУЭЛЬ ЗАВЕРШЕНА!</b>\n\n🏆 Победитель: {wname}\n💀 Проигравший: {lname}",
            parse_mode="HTML"
        )
        return

    await send_duel_state(context, q.message, key)

# ═══════════════════════════════════════════════════════════════════
#  РУЛЕТКА / КАЗИНО
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("luck")
async def luck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    r = random.randint(1, 100)
    db.add_activity(update.effective_chat.id, user.id, 1)
    if r == 100:
        db.grant_achievement(user.id, "luck_100")
        msg = f"🎰 {user.mention_html()} — <b>ИДЕАЛЬНЫЙ БРОСОК! 100/100!</b> 🎯🎉🎊"
    elif r >= 90:
        msg = f"🎰 {user.mention_html()} — <b>ДЖЕКПОТ!</b> {r}/100 🎉"
    elif r >= 60:
        msg = f"🎰 {user.mention_html()} — Удача! {r}/100 ✨"
    elif r >= 30:
        msg = f"🎰 {user.mention_html()} — Нейтрально {r}/100 😐"
    else:
        msg = f"🎰 {user.mention_html()} — Провал {r}/100 💀"
    await answer_text(update, msg)

@group_only
@require_premium("casino")
async def casino_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "⭐", "7️⃣"]
    s1, s2, s3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)
    db.add_activity(chat.id, user.id, 1)
    if s1 == s2 == s3:
        if s1 == "7️⃣":
            result = "🎰 ТРОЙНАЯ СЕМЁРКА — МЕГАДЖЕКПОТ!!! 🎉🎉🎉"
        elif s1 == "💎":
            result = "🎰 ТРОЙНОЙ БРИЛЛИАНТ — ДЖЕКПОТ! 💎💎💎"
        else:
            result = f"🎰 ТРОЙНОЕ СОВПАДЕНИЕ! Победа! {s1}{s2}{s3}"
        db.grant_achievement(user.id, "casino_win")
    elif s1 == s2 or s2 == s3:
        result = f"🎰 Два совпадения! {s1}{s2}{s3} — почти выиграл!"
    else:
        result = f"🎰 {s1}{s2}{s3} — В следующий раз повезёт!"
    await answer_text(update, f"{user.mention_html()}\n{result}")

# ═══════════════════════════════════════════════════════════════════
#  ОПРОСЫ / ПРАВИЛА / ПРИВЕТСТВИЕ / АНТИФЛУД
# ═══════════════════════════════════════════════════════════════════

@group_only
@require_premium("poll")
async def poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await answer_text(update, "Формат: /poll Вопрос|Вариант1|Вариант2|...")
    parts = " ".join(context.args).split("|")
    if len(parts) < 3:
        return await answer_text(update, "Нужно минимум 2 варианта.")
    await context.bot.send_poll(update.effective_chat.id, parts[0].strip(), [p.strip() for p in parts[1:]])

@group_only
@require_premium("welcome")
async def welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if context.args:
        db.set_setting(chat.id, "welcome", " ".join(context.args))
        await answer_text(update, "✅ Приветствие установлено!\nПеременные: {name}, {chat}")
    else:
        cur = db.get_setting(chat.id, "welcome")
        await answer_text(update, f"Текущее:\n{cur}" if cur else "Не установлено. /welcome Текст")

@group_only
@require_premium("rules")
async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if context.args:
        db.set_setting(chat.id, "rules", " ".join(context.args))
        await answer_text(update, "✅ Правила установлены!")
    else:
        rules = db.get_setting(chat.id, "rules")
        await answer_text(update, f"📜 <b>Правила чата:</b>\n{rules}" if rules else "Правила не установлены. /rules Текст")

@group_only
@require_premium("antiflood")
async def antiflood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    limit = 5
    if context.args:
        try:
            limit = max(2, int(context.args[0]))
        except ValueError:
            pass
    db.set_setting(chat.id, "antiflood", str(limit))
    await answer_text(update, f"🛡️ Антифлуд: >{limit} сообщ. за 10 сек → мут 5 мин.")

# ═══════════════════════════════════════════════════════════════════
#  МАГАЗИН
# ═══════════════════════════════════════════════════════════════════

FEATURES_LIST = [
    ("buy_warns",        "warns",        "⚠️ Предупреждения",      50),
    ("buy_welcome",      "welcome",      "👋 Авто-приветствие",    30),
    ("buy_rules",        "rules",        "📜 Правила чата",        20),
    ("buy_antiflood",    "antiflood",    "🛡️ Антифлуд",            40),
    ("buy_luck",         "luck",         "🎰 Рулетка",             25),
    ("buy_duel",         "duel",         "⚔️ Интерактивные дуэли", 60),
    ("buy_poll",         "poll",         "📊 Голосования",         35),
    ("buy_marry",        "marry",        "💍 Система браков",      15),
    ("buy_casino",       "casino",       "🎲 Казино",              45),
    ("buy_achievements", "achievements", "🏅 Достижения",          30),
    ("buy_pin",          "pin",          "📌 Закреп сообщений",    20),
    ("buy_notes",        "notes",        "📋 Заметки",             25),
    ("buy_filters",      "filters",      "🔍 Фильтры автоответа",  35),
]

async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or "")
    owned = db.get_owned_features(user.id)
    is_owner = db.is_bot_owner(user.id)

    kb = []
    for cb, fid, name, price in FEATURES_LIST:
        if is_owner or fid in owned or db.has_free_grant(user.id, fid):
            label = f"{name} ✅"
        else:
            label = f"{name} ({price} ⭐)"
        kb.append([InlineKeyboardButton(label, callback_data=cb)])

    header = "🌟 Владелец бота — все функции бесплатны!\n\n" if is_owner else ""
    msg = update.message if update.message else update.callback_query.message
    await msg.reply_text(
        f"🛒 <b>Магазин функций Yeah HQ Bot</b>\n\n{header}"
        "Купленная функция доступна во всех ваших группах.\n"
        "Оплата: Telegram Stars ⭐",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "open_shop":
        await shop_cmd(update, context)
        return

    user = q.from_user
    db.ensure_user(user.id, user.username or "")
    fm = {cb: (fid, name, price) for cb, fid, name, price in FEATURES_LIST}
    if q.data not in fm:
        return
    fid, fname, price = fm[q.data]

    if db.is_bot_owner(user.id):
        db.grant_feature(user.id, fid)
        await q.edit_message_text(f"✅ <b>{fname}</b> активирована бесплатно (владелец бота).", parse_mode="HTML")
        return
    if db.has_free_grant(user.id, fid):
        db.grant_feature(user.id, fid)
        await q.edit_message_text(f"✅ <b>{fname}</b> активирована бесплатно (спец. доступ).", parse_mode="HTML")
        return
    if db.has_feature(user.id, fid):
        await q.answer("Уже куплено!", show_alert=True)
        return
    try:
        await context.bot.send_invoice(
            chat_id=user.id,
            title=fname,
            description="Доступна во всех ваших группах после покупки.",
            payload=f"feature:{fid}",
            currency="XTR",
            prices=[LabeledPrice(fname, price)]
        )
        await q.edit_message_text(
            f"💳 Счёт на <b>{fname}</b> ({price} ⭐) отправлен в личные сообщения.",
            parse_mode="HTML"
        )
    except TelegramError:
        await q.edit_message_text("❌ Сначала напишите боту в личные сообщения /start, потом возвращайтесь в магазин.")

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pl = update.message.successful_payment.invoice_payload
    stars = update.message.successful_payment.total_amount
    if pl.startswith("feature:"):
        fid = pl.split(":")[1]
        db.grant_feature(user.id, fid)
        db.record_payment(user.id, fid, stars)
        await update.message.reply_text(
            f"✅ Оплата прошла! Функция <b>{fid}</b> активирована.\n"
            "Теперь она доступна во всех ваших группах!",
            parse_mode="HTML"
        )

# ═══════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЕ ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════════

async def new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if db.is_chat_disabled(chat.id):
        return
    db.register_group(chat.id, chat.title or "")
    for m in update.message.new_chat_members:
        if m.is_bot:
            continue
        db.ensure_user(m.id, m.username or "")
        welcome = db.get_setting(chat.id, "welcome")
        if welcome:
            text = welcome.replace("{name}", m.mention_html()).replace("{chat}", chat.title or "")
            await update.message.reply_text(text, parse_mode="HTML")

async def flood_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ("group", "supergroup"):
        return
    if db.is_chat_disabled(chat.id):
        return
    db.ensure_user(user.id, user.username or "")
    db.add_activity(chat.id, user.id, 1)

    limit_str = db.get_setting(chat.id, "antiflood")
    if not limit_str or db.is_bot_owner(user.id):
        return
    limit = int(limit_str)
    key = (chat.id, user.id)
    now = datetime.now().timestamp()
    times = [t for t in flood_tracker.get(key, []) if now - t < 10]
    times.append(now)
    flood_tracker[key] = times
    if len(times) > limit:
        try:
            await context.bot.restrict_chat_member(
                chat.id, user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now() + timedelta(minutes=5)
            )
            await update.message.reply_text(
                f"🛡️ {user.mention_html()} замолчан на 5 мин за флуд.", parse_mode="HTML"
            )
        except Exception:
            pass
        flood_tracker[key] = []

# ═══════════════════════════════════════════════════════════════════
#  КОМАНДЫ ВЛАДЕЛЬЦА БОТА
# ═══════════════════════════════════════════════════════════════════

async def ownerhelp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    await answer_text(update,
        "👑 <b>Команды владельца бота:</b>\n\n"
        "/ownerhelp — эта справка\n"
        "/addowner @user — добавить совладельца\n"
        "/removeowner @user — убрать совладельца\n"
        "/botowners — список владельцев\n"
        "/grantfree @user feature — бесплатный доступ\n"
        "/revokefree @user feature — убрать бесплатный доступ\n"
        "/disablechat [chat_id] — отключить бота\n"
        "/enablechat [chat_id] — включить бота\n"
        "/botstats — полная статистика\n"
        "/divorceforce @user — расторгнуть любой брак\n"
        "/allgroups — список всех групп\n\n"
        "<b>Привилегии:</b>\n"
        "• Всегда выигрывает в дуэлях\n"
        "• Все функции бесплатны\n"
        "• Нельзя замутить/забанить/кикнуть\n"
        "• Статусная строка во всех профилях"
    )

async def addowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    if not context.args:
        return await answer_text(update, "Использование: /addowner @username или ID")
    ident = context.args[0].lstrip("@")
    try:
        uid = int(ident)
        uname = ""
    except ValueError:
        u = db.find_user_by_username(ident)
        if not u:
            return await answer_text(update, "❌ Пользователь не найден. Он должен написать /start боту.")
        uid, uname = u["user_id"], u.get("username", "")
    db.add_bot_owner(uid, uname, is_main=False)
    await answer_text(update, f"✅ <code>{uid}</code> (@{uname}) добавлен как <b>совладелец</b> бота.")

async def removeowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    if not context.args:
        return await answer_text(update, "Использование: /removeowner @username или ID")
    ident = context.args[0].lstrip("@")
    try:
        uid = int(ident)
    except ValueError:
        u = db.find_user_by_username(ident)
        if not u:
            return await answer_text(update, "❌ Пользователь не найден.")
        uid = u["user_id"]
    db.remove_bot_owner(uid)
    await answer_text(update, f"✅ <code>{uid}</code> убран из совладельцев.")

async def botowners_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    owners = db.get_bot_owners()
    if not owners:
        await answer_text(update, "👑 Список владельцев пуст.")
        return
    await answer_text(update, "👑 <b>Владельцы и совладельцы бота:</b>\n\n" + "\n".join(f"• {o}" for o in owners))

async def grantfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        feats = ", ".join(f for _, f, _, _ in FEATURES_LIST)
        return await answer_text(update, f"Использование: /grantfree @user feature\nФункции: {feats}")
    ident, feature = context.args[0].lstrip("@"), context.args[1]
    try:
        uid = int(ident)
    except ValueError:
        u = db.find_user_by_username(ident)
        if not u:
            return await answer_text(update, "❌ Пользователь не найден.")
        uid = u["user_id"]
    db.set_free_grant(uid, feature, True)
    db.grant_feature(uid, feature)
    await answer_text(update, f"✅ Пользователю <code>{uid}</code> выдан бесплатный доступ к <code>{feature}</code>.")

async def revokefree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        return await answer_text(update, "Использование: /revokefree @user feature")
    ident, feature = context.args[0].lstrip("@"), context.args[1]
    try:
        uid = int(ident)
    except ValueError:
        u = db.find_user_by_username(ident)
        if not u:
            return await answer_text(update, "❌ Пользователь не найден.")
        uid = u["user_id"]
    db.set_free_grant(uid, feature, False)
    db.revoke_feature(uid, feature)
    await answer_text(update, f"✅ Бесплатный доступ к <code>{feature}</code> у <code>{uid}</code> отозван.")

async def disablechat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    cid = int(context.args[0]) if context.args else update.effective_chat.id
    db.disable_chat(cid)
    await answer_text(update, f"✅ Бот отключён в чате <code>{cid}</code>.")

async def enablechat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    cid = int(context.args[0]) if context.args else update.effective_chat.id
    db.enable_chat(cid)
    await answer_text(update, f"✅ Бот включён в чате <code>{cid}</code>.")

async def botstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    s = db.get_stats()
    marriages = len(db.get_all_marriages_global())
    await answer_text(update,
        f"📊 <b>Статистика бота — @{BOT_USERNAME}</b>\n\n"
        f"👤 Пользователей: <b>{s['users']}</b>\n"
        f"  · Новых сегодня: <b>{s['new_users_day']}</b>\n"
        f"  · За неделю: <b>{s['new_users_week']}</b>\n"
        f"  · За месяц: <b>{s['new_users_month']}</b>\n\n"
        f"🏘 Групп: <b>{s['groups']}</b>\n\n"
        f"⚡ Действий всего: <b>{s['actions']}</b>\n"
        f"  · За день: <b>{s['actions_day']}</b>\n"
        f"  · За неделю: <b>{s['actions_week']}</b>\n"
        f"  · За месяц: <b>{s['actions_month']}</b>\n\n"
        f"💍 Браков зарегистрировано: <b>{marriages}</b>\n\n"
        f"💰 <b>Заработано (Stars ⭐):</b>\n"
        f"  · Сегодня: <b>{s['earned_day']}</b>\n"
        f"  · Неделя: <b>{s['earned_week']}</b>\n"
        f"  · Месяц: <b>{s['earned_month']}</b>\n"
        f"  · Год: <b>{s['earned_year']}</b>\n"
        f"  · Всего: <b>{s['earned_total']}</b>"
    )

async def allgroups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    groups = db.get_all_groups()
    if not groups:
        return await answer_text(update, "📋 Бот пока не добавлен ни в одну группу.")
    text = f"📋 <b>Группы бота ({len(groups)}):</b>\n\n"
    for g in groups[:30]:
        text += f"• {g['title']} (<code>{g['chat_id']}</code>)\n"
    if len(groups) > 30:
        text += f"\n...и ещё {len(groups) - 30} групп"
    await answer_text(update, text)

async def divorceforce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db.is_bot_owner(update.effective_user.id):
        return
    target = await resolve_target(update, context)
    if not target:
        return await answer_text(update, "❌ Укажите пользователя.")
    chat_id = update.effective_chat.id
    m = db.is_married(target.id, chat_id)
    if not m:
        all_m = db.get_all_marriages_global()
        target_m = [x for x in all_m if target.id in (x["user1_id"], x["user2_id"])]
        if not target_m:
            return await answer_text(update, "❌ У этого пользователя нет браков.")
        db.divorce_by_id(target_m[0]["id"])
    else:
        db.divorce(target.id, chat_id)
    await answer_text(update, f"✅ Брак {target.mention_html()} расторгнут владельцем бота.")

# ═══════════════════════════════════════════════════════════════════
#  АЛЬТЕРНАТИВНЫЕ КОМАНДЫ С !
# ═══════════════════════════════════════════════════════════════════

EXCL_ALIASES = {
    "!мут":         mute_cmd,
    "!размут":      unmute_cmd,
    "!кик":         kick_cmd,
    "!бан":         ban_cmd,
    "!разбан":      unban_cmd,
    "!варн":        warn_cmd,
    "!повысить":    promote_cmd,
    "!разжаловать": demote_cmd,
    "!роли":        roles_cmd,
    "!профиль":     profile_cmd,
    "!+брак":       marry_cmd,
    "!развод":      divorce_cmd,
    "!браки":       marriages_cmd,
    "!топ":         top_cmd,
    "!достижения":  achievements_cmd,
    "!магазин":     shop_cmd,
    "!помощь":      help_cmd,
    "!команды":     help_cmd,
    "!правила":     rules_cmd,
    "!казино":      casino_cmd,
    "!рулетка":     luck_cmd,
    "!заметка":     note_cmd,
    "!фильтр":      filter_cmd,
}

async def exclamation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    lower = text.lower()
    for alias, func in EXCL_ALIASES.items():
        if lower == alias or lower.startswith(alias + " "):
            rest = text[len(alias):].strip()
            context.args = rest.split() if rest else []
            await func(update, context)
            return

# ═══════════════════════════════════════════════════════════════════
#  MINI APP — WebApp handler для данных
# ═══════════════════════════════════════════════════════════════════

async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает данные из Mini App."""
    if not update.message or not update.message.web_app_data:
        return
    data = update.message.web_app_data.data
    user = update.effective_user
    logger.info(f"WebApp data from {user.id}: {data}")
    await update.message.reply_text(
        f"✅ Данные из мини-приложения получены.",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
#  ВЕБ-СЕРВЕР ДЛЯ МИНИ-ПРИЛОЖЕНИЯ
# ═══════════════════════════════════════════════════════════════════

async def _miniapp_handler(request):
    """Отдаёт miniapp.html по запросу GET /miniapp"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "miniapp.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        return aiohttp_web.Response(text=content, content_type="text/html")
    except FileNotFoundError:
        return aiohttp_web.Response(text="<h1>miniapp.html not found</h1>", content_type="text/html", status=404)

def run_miniapp_server():
    """Запускает aiohttp-сервер в отдельном потоке."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    web_app = aiohttp_web.Application()
    web_app.router.add_get("/miniapp", _miniapp_handler)
    web_app.router.add_get("/", _miniapp_handler)  # корень тоже открывает мини-приложение
    runner = aiohttp_web.AppRunner(web_app)
    loop.run_until_complete(runner.setup())
    site = aiohttp_web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    loop.run_until_complete(site.start())
    loop.run_forever()

# ═══════════════════════════════════════════════════════════════════

def main():
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Базовые
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["whoami", "me"], whoami_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("achievements", achievements_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("chatstats", chatstats_cmd))
    app.add_handler(CommandHandler("miniapp", miniapp_cmd))

    # Модерация
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("unmute", unmute_cmd))
    app.add_handler(CommandHandler("kick", kick_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("warn", warn_cmd))
    app.add_handler(CommandHandler("unwarn", unwarn_cmd))
    app.add_handler(CommandHandler("pin", pin_cmd))
    app.add_handler(CommandHandler("unpin", unpin_cmd))

    # Должности
    app.add_handler(CommandHandler("promote", promote_cmd))
    app.add_handler(CommandHandler("demote", demote_cmd))
    app.add_handler(CommandHandler(["roles", "admins"], roles_cmd))

    # Заметки и фильтры
    app.add_handler(CommandHandler("note", note_cmd))
    app.add_handler(CommandHandler("filter", filter_cmd))

    # Браки
    app.add_handler(CommandHandler("marry", marry_cmd))
    app.add_handler(CommandHandler("divorce", divorce_cmd))
    app.add_handler(CommandHandler("marriages", marriages_cmd))
    app.add_handler(CallbackQueryHandler(marry_cb, pattern="^marry_"))

    # Дуэли
    app.add_handler(CommandHandler("duel", duel_cmd))
    app.add_handler(CallbackQueryHandler(duel_accept_cb, pattern="^duel_(accept|decline):"))
    app.add_handler(CallbackQueryHandler(duel_action_cb, pattern="^duel_(aim|dodge|shoot):"))

    # Развлечения
    app.add_handler(CommandHandler("luck", luck_cmd))
    app.add_handler(CommandHandler("casino", casino_cmd))
    app.add_handler(CommandHandler("poll", poll_cmd))

    # Настройки
    app.add_handler(CommandHandler("welcome", welcome_cmd))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("antiflood", antiflood_cmd))

    # Магазин
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CallbackQueryHandler(shop_cb, pattern="^(buy_|open_shop)"))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid))

    # Владелец бота
    app.add_handler(CommandHandler("ownerhelp", ownerhelp_cmd))
    app.add_handler(CommandHandler("addowner", addowner_cmd))
    app.add_handler(CommandHandler("removeowner", removeowner_cmd))
    app.add_handler(CommandHandler("botowners", botowners_cmd))
    app.add_handler(CommandHandler("grantfree", grantfree_cmd))
    app.add_handler(CommandHandler("revokefree", revokefree_cmd))
    app.add_handler(CommandHandler("disablechat", disablechat_cmd))
    app.add_handler(CommandHandler("enablechat", enablechat_cmd))
    app.add_handler(CommandHandler("botstats", botstats_cmd))
    app.add_handler(CommandHandler("allgroups", allgroups_cmd))
    app.add_handler(CommandHandler("divorceforce", divorceforce_cmd))

    # Меню команд (инлайн-кнопки)
    app.add_handler(CallbackQueryHandler(commands_section_cb, pattern="^(cmd_section_|back_to_|open_shop)"))
    app.add_handler(CallbackQueryHandler(cmd_info_cb, pattern="^cmd_info:"))
    app.add_handler(CallbackQueryHandler(why_admin_cb, pattern="^why_admin"))

    # Системные
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member))

    # WebApp данные
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data_handler))

    # Команды с !
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r"^!"),
        exclamation_handler
    ))

    # Кнопки нижней клавиатуры (личка)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        keyboard_buttons
    ))

    # Счётчик активности + антифлуд + фильтры
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        flood_check
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        filter_check
    ))

    logger.info(f"✅ Yeah HQ Bot v3.0 (@{BOT_USERNAME}) запущен!")

    # Запуск веб-сервера мини-приложения в отдельном потоке
    web_thread = threading.Thread(target=run_miniapp_server, daemon=True)
    web_thread.start()
    logger.info(f"🌐 Mini App сервер запущен на порту {WEB_PORT}")

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    if webhook_url:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 8000)),
            webhook_url=webhook_url + "/webhook",
            url_path="webhook"
        )
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
