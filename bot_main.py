# bot_main.py
import os
import re
import json
import random
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web

# ================== НАЛАШТУВАННЯ ==================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN     = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "-1000000000000"))
PAYMENT_CARD  = os.getenv("PAYMENT_CARD", "4441 1110 3900 4548")
DB_FILE       = os.getenv("DB_FILE_PATH", "./game_db.json")
QUESTS_FILE   = os.getenv("QUESTS_FILE", "./quests_tayemnyci_150.json")
PRICE         = 100

if not BOT_TOKEN:
    raise RuntimeError("Environment BOT_TOKEN is missing")

bot = Bot(BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
rt  = Router()
dp.include_router(rt)

random.seed(42)

# ================== ДАНІ ГРИ ==================
def _empty_db():
    return {
        "pending": {},
        "registrations": {},
        "stats": {},
        "progress": {},
        "inventory": {},
        "debts": {}
    }

def load_db():
    try:
        if not os.path.exists(DB_FILE):
            return _empty_db()
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _empty_db()

def save_db(db):
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_quests():
    # очікуємо структуру:
    # { "tasks":[{id,title,text,stitches,tech,color,keyword,dice_event?}], "artifacts":[{code,name,effect}], "dice_outcomes":[{value,effect}] }
    if os.path.exists(QUESTS_FILE):
        with open(QUESTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # fallback мінімальний набір, щоб не впасти
    return {
        "tasks": [
            {
                "id": 1,
                "title": "Перший стібок",
                "text": "Зроби 400 стібків у будь-якому процесі.",
                "stitches": 400,
                "tech": "хрестик",
                "color": "вільно",
                "keyword": "#СТАРТ_1",
                "dice_event": True
            }
        ],
        "artifacts": [
            {"code": "amulet_light",  "name": "Амулет Світла",  "effect": "-100 стібків у наступному завданні"},
            {"code": "bead_luck",     "name": "Бісер Удачі",   "effect": "Кращий шанс на 5–6 під час кидка"},
            {"code": "scissors_fate", "name": "Ножиці Долі",   "effect": "Разове зняття кари/штрафу"}
        ],
        "dice_outcomes": [
            {"value": 1, "effect": "+100 борг стібків"},
            {"value": 2, "effect": "+50 борг стібків"},
            {"value": 3, "effect": "нічого не відбулося"},
            {"value": 4, "effect": "нічого не відбулося"},
            {"value": 5, "effect": "шанс на артефакт або -100 стібків у наступному"},
            {"value": 6, "effect": "гарантований артефакт"}
        ]
    }

db = load_db()
quests = load_quests()

TASKS      = quests.get("tasks", [])
ARTIFACTS  = {a["code"]: a for a in quests.get("artifacts", [])}
DICE_TABLE = quests.get("dice_outcomes", [])

# ================== КОРИСНІ ФУНКЦІЇ ==================
def ensure_user(uid: int, user: types.User):
    suid = str(uid)
    db["stats"].setdefault(suid, {
        "name": user.first_name,
        "username": user.username,
        "reports": 0,
        "stitches_total": 0
    })
    db["progress"].setdefault(suid, {"current": 1, "history": []})
    db["inventory"].setdefault(suid, {})
    db["debts"].setdefault(suid, 0)

def game_name(_: str) -> str:
    return "Таємниці Ниток"

def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎮 Ігри")
    kb.button(text="💳 Оплата")
    kb.button(text="🧵 Статус")
    kb.button(text="📸 Звіт")
    kb.button(text="🎯 Завдання")
    kb.button(text="🎲 Кинути кубик")
    kb.button(text="🎒 Інвентар")
    kb.button(text="📊 Моя статистика")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup(resize_keyboard=True)

def task_card(t):
    base = [
        f"Завдання #{t['id']} — {t['title']}",
        t['text'],
        f"🧵 Стібків: {t['stitches']} | Техніка: {t['tech']} | Колір: {t['color']}",
        f"🔑 Ключове слово: {t['keyword']}"
    ]
    if t.get("dice_event"):
        base.append("🎲 Подія: на цьому етапі доступний кидок кубика.")
    return "\n".join(base)

def grant_artifact(suid: str, code: str) -> str:
    if code not in ARTIFACTS:
        return "Невідомий артефакт"
    inv = db["inventory"].setdefault(suid, {})
    inv[code] = inv.get(code, 0) + 1
    save_db(db)
    return ARTIFACTS[code]["name"]

def apply_artifact_effects_on_next(suid: str, base_stitches: int) -> int:
    # Амулет Світла: -100 стібків у наступному завданні, потім згорає
    inv = db["inventory"].setdefault(suid, {})
    if inv.get("amulet_light", 0) > 0:
        inv["amulet_light"] -= 1
        if inv["amulet_light"] <= 0:
            inv.pop("amulet_light", None)
        save_db(db)
        return max(100, base_stitches - 100)
    return base_stitches

def roll_dice(suid: str) -> int:
    # Бісер Удачі злегка зсуває шанс у бік 5–6
    inv = db["inventory"].setdefault(suid, {})
    bias = 0.0
    if inv.get("bead_luck", 0) > 0:
        bias = 0.10
    r = random.random()
    if r < (1/6 - bias/2): return 1
    if r < (2/6 - bias/2): return 2
    if r < (3/6):          return 3
    if r < (4/6):          return 4
    if r < (5/6 + bias/2): return 5
    return 6

# ================== ХЕНДЛЕРИ МЕНЮ ==================
@rt.message(Command("start"))
async def start_cmd(m: types.Message):
    ensure_user(m.from_user.id, m.from_user)
    await m.answer("Привіт! 🧶 Я бот творчої бджілки. Обери дію нижче 👇", reply_markup=main_menu())

@rt.message(F.text == "🎮 Ігри")
async def show_games(m: types.Message):
    await m.answer("Активна гра: Таємниці Ниток.\nОплати участь та надсилай звіти.", parse_mode="Markdown")

@rt.message(F.text == "💳 Оплата")
@rt.message(Command("pay", "оплата"))
async def pay_info(m: types.Message):
    await m.answer(
        f"💳 Оплата участі — {PRICE} грн\<br><br>"
        f"Картка: {PAYMENT_CARD}\<br><br>"
        f"Після оплати — надішли скриншот у цей чат. Я передам адміну ✅",
        parse_mode="HTML"
    )

@rt.message(F.text == "🧵 Статус")
@rt.message(Command("status"))
async def my_status(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    reg = db["registrations"].get(uid)
    if reg and reg.get("approved"):
        t_index = db["progress"][uid]["current"]
        await m.answer(f"✅ Ти у грі {game_name('x')}. Поточне завдання: #{t_index}", parse_mode="Markdown")
    elif uid in db["pending"]:
        await m.answer("⏳ Заявка очікує підтвердження адміністратором.")
    else:
        await m.answer("ℹ Ти ще не реєструвалася. Надішли скрин оплати після «💳 Оплата».")

@rt.message(F.text == "📸 Звіт")
@rt.message(Command("report"))
async def report_help(m: types.Message):
    await m.answer(
        "📸 Формат підпису до фото-звіту:\n"
        "звіт: старт 520  або  звіт: фініш 840\n(дозволено 300–1200 стібків).\n"
        "Після фінішу нове завдання приходить одразу, а адмін перевіряє пізніше.",
        parse_mode="Markdown"
    )

@rt.message(Command("quest"))
@rt.message(F.text.contains("Завдання"))
async def give_quest(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    cur = db["progress"][uid]["current"]
    if cur > len(TASKS):
        await m.answer("🏁 Фінал! Усі завдання виконано. Ти — Майстриня Осердя ✨")
        return
    t = TASKS[cur - 1]
    stitches = apply_artifact_effects_on_next(uid, t["stitches"])
    card = {**t, "stitches": stitches}
    await m.answer(task_card(card), parse_mode="Markdown")

@rt.message(F.text == "🎲 Кинути кубик")
@rt.message(Command("roll"))
async def do_roll(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    cur = db["progress"][uid]["current"]
    if cur > len(TASKS):
        await m.answer("Гра завершена. Кубик більше не впливає ✨")
        return
    t = TASKS[cur - 1]
    if not t.get("dice_event"):
        await m.answer("На цьому етапі доля спить. Кубик не потрібен 🙂")
        return

    val = roll_dice(uid)
    note = next((d["effect"] for d in DICE_TABLE if d.get("value") == val), "—")
    text = f"🎲 Кубик: {val} → {note}"

    if val in (1, 2):
        add = 100 if val == 1 else 50
        db["debts"][uid] = db["debts"].get(uid, 0) + add
        text += f"\n📌 Додано борг: +{add} стібків (погашення з наступних завдань)."
    elif val == 5:
        # 10% артефакт, інакше тимчасовий -100
        if random.random() < 0.10 and ARTIFACTS:
            name = grant_artifact(uid, random.choice(list(ARTIFACTS.keys())))
            text += f"\n🎁 Випав артефакт: {name}"
        else:
            grant_artifact(uid, "amulet_light")
            text += "\n🎁 Бонус: -100 стібків до наступного завдання."
    elif val == 6 and ARTIFACTS:
        name = grant_artifact(uid, random.choice(list(ARTIFACTS.keys())))
        text += f"\n🎁 Випав артефакт: {name}"

    save_db(db)
    await m.answer(text, parse_mode="Markdown")

@rt.message(F.text == "🎒 Інвентар")
@rt.message(Command("bag"))
async def show_bag(m: types.Message):
    uid = str(m.from_user.id)
    inv = db["inventory"].get(uid, {})
    if not inv:
        await m.answer("🎒 Порожньо. Артефакти ще не знайдені.")
        return
    lines = ["🎒 Твої артефакти:"]
    for code, count in inv.items():
        meta = ARTIFACTS.get(code, {"name": code, "effect": ""})
        lines.append(f"• {meta['name']} ×{count} — {meta.get('effect','')}")
    await m.answer("\n".join(lines), parse_mode="Markdown")

@rt.message(F.text == "📊 Моя статистика")
@rt.message(Command("mystats"))
async def mystats(m: types.Message):
    uid = str(m.from_user.id)
    s = db["stats"].get(uid)
    if not s:
        await m.answer("Поки що немає статистики. Надішли хоч один звіт 🧵")
        return
    debt = db["debts"].get(uid, 0)
    cur = db["progress"][uid]["current"]
    await m.answer(
        "📊 Твоя статистика\n"
        f"Звіти: {s.get('reports', 0)}\n"
        f"Сумарно стібків: {s.get('stitches_total', 0)}\n"
        f"Поточне завдання: #{cur if cur <= len(TASKS) else 'фінал'}\n"
        f"Борг стібків: {debt}"
    )

# ================== ФОТО: ЗВІТИ ТА ОПЛАТА ==================
REPORT_RE = re.compile(r"^\s*звіт\s*:\s*(старт|фініш)\s+(\d+)\s*$", re.I)

@rt.message(F.photo)
async def on_photo(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    caption = (m.caption or "").strip()

    # --- Фото-звіт ---
    mreport = REPORT_RE.search(caption)
    if mreport:
        kind, stitches = mreport.groups()
        kind = kind.lower()
        stitches = int(stitches)
        if stitches < 300 or stitches > 1200:
            await m.answer("⚠ Дозволено 300–1200 стібків за один звіт.")
            return

        # В адмін-групу
        cap = (
            f"📜 Фото-звіт\n"
            f"👤 {m.from_user.first_name} (@{m.from_user.username or '—'}) | ID {uid}\n"
            f"📌 Тип: {kind}\n"
            f"🧵 Стібків: {stitches}"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Зарахувати", callback_data=f"okrep|{uid}|{kind}|{stitches}")
        kb.button(text="❌ Відхилити",  callback_data=f"badrep|{uid}")
        kb.button(text="⚠ Кара",       callback_data=f"punish|{uid}")
        kb.adjust(3)
        await bot.send_photo(ADMIN_CHAT_ID, m.photo[-1].file_id, caption=cap, reply_markup=kb.as_markup())
        await m.answer("🧾 Звіт надіслано адміну. Дякую!")

        # Автовидача наступного завдання після ФІНІШ
        if kind == "фініш":
            cur = db["progress"][uid]["current"]
            if cur <= len(TASKS):
                t = TASKS[cur - 1]
                base = t["stitches"]

                # Амулет / борг
                base = apply_artifact_effects_on_next(uid, base)
                debt = db["debts"].get(uid, 0)
                if debt > 0:
                    take = min(debt, base // 2)   # не більше половини
                    db["debts"][uid] = debt - take
                    base = max(50, base - take)

                save_db(db)
                await m.answer("🎯 Наступне завдання:", parse_mode=None)
                await m.answer(task_card({**t, "stitches": base}), parse_mode="Markdown")
                db["progress"][uid]["current"] = cur + 1
                save_db(db)
            else:
                await m.answer("🏁 Фінал! Усі завдання виконано. Ти — Майстриня Осердя ✨")
        return

    # --- Скрин оплати ---
    reg = db["registrations"].get(uid)
    if not reg:
        db["pending"][uid] = {"game": "tayemnyci", "requested_at": datetime.now().isoformat(timespec="seconds")}
        save_db(db)

    cap = (
        f"💳 Скриншот оплати\n"
        f"👤 {m.from_user.first_name} (@{m.from_user.username or '—'}) | ID {uid}\n"
        f"🎮 Гра: {game_name('x')}\n"
        f"💳 Картка: {PAYMENT_CARD}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтвердити оплату", callback_data=f"apprpay|{uid}")
    kb.button(text="❌ Відхилити",          callback_data=f"declpay|{uid}")
    kb.adjust(2)
    await bot.send_photo(ADMIN_CHAT_ID, m.photo[-1].file_id, caption=cap, reply_markup=kb.as_markup())
    await m.answer("✅ Скрин відправлено адміну. Статус дивись у «🧵 Статус»")

# ================== ДІЇ АДМІНА (callback) ==================
@rt.callback_query(F.data.contains("|"))
async def admin_actions(call: types.CallbackQuery):
    if call.message.chat.id != ADMIN_CHAT_ID:
        await call.answer("Кнопки діють лише в адмін-групі", show_alert=True)
        return

    parts = call.data.split("|")
    action = parts[0]

    try:
        if action in ("apprpay", "declpay"):
            _, uid = parts
            if action == "apprpay":
                db["registrations"][uid] = {
                    "game": "tayemnyci",
                    "approved": True,
                    "approved_at": datetime.now().isoformat(timespec="seconds")
                }
                db["pending"].pop(uid, None)
                save_db(db)
                await bot.send_message(int(uid), "🎉 Оплату підтверджено! Стартуй у «🎯 Завдання».")
            else:
                db["pending"].pop(uid, None)
                save_db(db)
                await bot.send_message(int(uid), "❌ Оплату не підтверджено. Спробуй ще або напиши адміну.")
            await call.message.edit_reply_markup(reply_markup=None)
            await call.answer("ОК")

        elif action == "okrep":
            _, uid, kind, stitches = parts
            stitches = int(stitches)
            db["stats"].setdefault(uid, {"name": "", "username": "", "reports": 0, "stitches_total": 0})
            db["stats"][uid]["reports"]        = db["stats"][uid].get("reports", 0) + 1
            db["stats"][uid]["stitches_total"] = db["stats"][uid].get("stitches_total", 0) + stitches
            save_db(db)
            await bot.send_message(int(uid), f"✅ Зараховано {stitches} стібків ({kind}). Молодчинка! 🧵")
            await call.message.edit_reply_markup(reply_markup=None)
            await call.answer("ОК")

        elif action == "badrep":
            _, uid = parts
            debt_add = 150
            inv = db["inventory"].setdefault(uid, {})
            if inv.get("scissors_fate", 0) > 0:
                inv["scissors_fate"] -= 1
                if inv["scissors_fate"] <= 0:
                    inv.pop("scissors_fate", None)
                msg = "✂ Кара знята ножицями долі. Штраф не накладено."
            else:
                db["debts"][uid] = db["debts"].get(uid, 0) + debt_add
                msg = f"⚠ Звіт відхилено. Накладено борг: +{debt_add} стібків."
            save_db(db)
            await bot.send_message(int(uid), msg)
            await call.message.edit_reply_markup(reply_markup=None)
            await call.answer("ОК")

        elif action == "punish":
            _, uid = parts
            db["debts"][uid] = db["debts"].get(uid, 0) + 200
            save_db(db)
            await bot.send_message(int(uid), "🕯 Містична кара: +200 боргу стібків.")
            await call.message.edit_reply_markup(reply_markup=None)
            await call.answer("ОК")

    except Exception as e:
        await call.answer(str(e), show_alert=True)

# ================== ДІАГНОСТИКА ==================
@rt.message(Command("id"))
async def show_id(m: types.Message):
    await m.answer(f"chat_id: {m.chat.id}")

@rt.message(Command("test_admin"))
async def test_admin(m: types.Message):
    try:
        await bot.send_message(ADMIN_CHAT_ID, f"🔔 Тест від {m.from_user.first_name} (id {m.from_user.id})")
        await m.answer("✅ Надіслав тест у адмін-групу")
    except Exception as e:
        await m.answer(f"❌ Не зміг надіслати в адмін-групу: {e}")

# ================== WEBHOOK для Render ==================
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.exception(f"Webhook handle error: {e}")
        return web.Response(status=500, text="error")

app = web.Application()
app.router.add_post(f"/{BOT_TOKEN}", handle_webhook)

async def on_startup(app_: web.Application):
    base_url = os.getenv("RENDER_EXTERNAL_URL", "https://tvorcha-bot.onrender.com")
    webhook_url = f"{base_url}/{BOT_TOKEN}"
    await bot.set_webhook(webhook_url)
    logging.info(f"✅ Webhook set: {webhook_url}")

async def on_shutdown(app_: web.Application):
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception:
        pass
    logging.info("🛑 Webhook removed, bot session closed")

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

# ------------------ WEBHOOK ONLY (Render) ------------------
import os
import logging
from aiogram import types
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# healthcheck (GET) — щоб перевіряти у браузері
async def handle_health(request: web.Request):
    return web.Response(text="ok")

# приймаємо апдейти від Telegram (POST)
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
        logging.info(f"⬇ update: {data.get('update_id')} {list(data.keys())}")
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")
    except Exception as e:
        logging.exception(f"Webhook handle error: {e}")
        return web.Response(status=500, text="error")

app = web.Application()
app.router.add_post(f'/{BOT_TOKEN}', handle_webhook)
app.router.add_get("/", lambda r: web.Response(text="ok"))

async def on_startup(app_: web.Application):
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").strip() or "https://tvorcha-bot.onrender.com"
    webhook_url = f"{base_url}/{BOT_TOKEN}"
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        pass
    await bot.set_webhook(webhook_url, allowed_updates=["message","callback_query"])
    logging.info(f"✅ Webhook set: {webhook_url}")

async def on_shutdown(app_: web.Application):
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception:
        pass
    logging.info("🛑 Webhook removed, bot session closed")

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    web.run_app(app, host="0.0.0.0", port=port)










