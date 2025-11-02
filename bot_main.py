import os, json, re, logging, asyncio, random
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ================== НАЛАШТУВАННЯ ==================
TOKEN         = os.getenv("BOT_TOKEN", "8260944061:AAE_LWhH1UMwVhZSy0WK0ZEoDFGnlItdsgs")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "-1003202544470"))
PAYMENT_CARD  = os.getenv("PAYMENT_CARD", "4441 1110 3900 4548")
DB_FILE       = os.getenv("DB_FILE_PATH", "./game_db.json")
QUESTS_FILE   = os.getenv("QUESTS_FILE", "./quests_tayemnyci_150.json")
PRICE         = 100

logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
rt  = Router()
dp.include_router(rt)

random.seed(42)

# ================== ЧИТАЄМО ГРУ ==================
def load_db():
    try:
        if not os.path.exists(DB_FILE):
            return {"pending": {}, "registrations": {}, "stats": {}, "progress": {}, "inventory": {}, "debts": {}}
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"pending": {}, "registrations": {}, "stats": {}, "progress": {}, "inventory": {}, "debts": {}}

def save_db(db):
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_quests():
    with open(QUESTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

db = load_db()
quests = load_quests()
TASKS = quests["tasks"]
ARTIFACTS = {a["code"]: a for a in quests["artifacts"]}
DICE = quests["dice_outcomes"]

# ================== КОРИСНЕ ==================
def ensure_user(uid: int, user: types.User):
    suid = str(uid)
    db["stats"].setdefault(suid, {"name": user.first_name, "username": user.username, "reports": 0, "stitches_total": 0})
    db["progress"].setdefault(suid, {"current": 1, "history": []})
    db["inventory"].setdefault(suid, {})
    db["debts"].setdefault(suid, 0)

def game_name(code: str) -> str:
    return "Таємниці Ниток"

def main_menu() -> ReplyKeyboardMarkup:
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
    return (f"*Завдання #{t['id']} — {t['title']}*\n"
            f"{t['text']}\n"
            f"🧵 Стібків: {t['stitches']} | Техніка: {t['tech']} | Колір: {t['color']}\n"
            f"🔑 Ключове слово: {t['keyword']}"
            + ("" if not t.get("dice_event") else "\n🎲 Подія: кидок кубика доступний."))

def grant_artifact(uid: str, code: str) -> str:
    inv = db["inventory"].setdefault(uid, {})
    inv[code] = inv.get(code, 0) + 1
    save_db(db)
    return ARTIFACTS[code]["name"]

def apply_artifact_effects_on_next(uid: str, base_stitches: int) -> int:
    """Амулет Світла зменшує на 100 і згорає."""
    inv = db["inventory"].setdefault(uid, {})
    if inv.get("amulet_light", 0) > 0:
        inv["amulet_light"] -= 1
        if inv["amulet_light"] <= 0: inv.pop("amulet_light", None)
        save_db(db)
        return max(100, base_stitches - 100)
    return base_stitches

def roll_dice(uid: str) -> dict:
    # bead_luck збільшує шанс хороших результатів 5-6
    inv = db["inventory"].setdefault(uid, {})
    bias = 0.0
    if inv.get("bead_luck", 0) > 0:
        bias = 0.1
    r = random.random()
    if r < (1/6 - bias/2): val = 1
    elif r < (2/6 - bias/2): val = 2
    elif r < (3/6): val = 3
    elif r < (4/6): val = 4
    elif r < (5/6 + bias/2): val = 5
    else: val = 6
    return {"value": val}

# ================== МЕНЮ / КОМАНДИ ==================
@rt.message(Command("start"))
async def start_cmd(m: types.Message):
    await m.answer(
        "Привіт! 🧶 Я бот творчої бджілки.\n"
        "Вибери дію в меню нижче 👇",
        reply_markup=main_menu()
    )

@rt.message(F.text == "🎮 Ігри")
async def show_games(m: types.Message):
    await m.reply("Поки активна головна гра: Таємниці Ниток. Зареєструйся через «💳 Оплата» та шли звіти.",
                  parse_mode="Markdown")

@rt.message(F.text == "💳 Оплата")
@rt.message(Command("pay","оплата"))
async def pay_info(m: types.Message):
    await m.reply(
        "💳 Оплата участі — 100 грн\n"
        f"Картка: {PAYMENT_CARD}\n"
        "Після оплати — надішли скриншот у цей чат. Я передам адміну ✅",
        parse_mode="Markdown"
    )

@rt.message(F.text == "🧵 Статус")
@rt.message(Command("status"))
async def my_status(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    reg = db["registrations"].get(uid)
    if reg and reg.get("approved"):
        t_index = db["progress"][uid]["current"]
        await m.reply(f"✅ Ви у грі {game_name('x')}. Поточне завдання: #{t_index}")
    elif uid in db["pending"]:
        await m.reply("⏳ Заявка очікує підтвердження адміністратором.")
    else:
        await m.reply("ℹ Ви ще не реєструвалися. Просто шліть скрин оплати після «💳 Оплата».")

@rt.message(F.text == "📸 Звіт")
@rt.message(Command("report"))
async def report_help(m: types.Message):
    await m.reply(
        "📸 Формат підпису до фото-звіту:\n"
        "звіт: старт 520 або звіт: фініш 840 (дозволено 300–1200).\n"
        "Після фінішу нове завдання прийде одразу, а адмін перевірить пізніше.",
        parse_mode="Markdown"
    )

@rt.message(F.text == "🎯 Завдання")
@rt.message(Command("quest"))
async def give_quest(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    cur = db["progress"][uid]["current"]
    if cur > len(TASKS):
        await m.reply("🏁 Фінал! Усі 150 завдань виконано. Ти — Майстриня Осердя ✨")
        return
    t = TASKS[cur-1]
    stitches = apply_artifact_effects_on_next(uid, t["stitches"])
    await m.reply(task_card({**t, "stitches": stitches}), parse_mode="Markdown")

@rt.message(F.text == "🎲 Кинути кубик")
@rt.message(Command("roll"))
async def do_roll(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    cur = db["progress"][uid]["current"]
    if cur > len(TASKS):
        await m.reply("Гра завершена. Кубик більше не впливає ✨")
        return
    t = TASKS[cur-1]
    if not t.get("dice_event"):
        await m.reply("На цьому етапі доля спить. Кубик не потрібен 🙂")
        return

    # перевір артефакт на повторний кидок
    inv = db["inventory"].setdefault(uid, {})
    used_needle = False

    res = roll_dice(uid)
    value = res["value"]
    note = next((d["effect"] for d in DICE if d["value"]==value), "—")
    text = f"🎲 Кубик: {value} → {note}"

    # ефекти
    if value in (1,2):
        add = 100 if value==1 else 50
        db["debts"][uid] = db["debts"].get(uid, 0) + add
        text += f"\n📌 Додано борг: +{add} стібків (погашується автоматично з наступних завдань)."
    elif value == 5:
        if random.random() < 0.10:
            name = grant_artifact(uid, random.choice(list(ARTIFACTS.keys())))
            text += f"\n🎁 Випав артефакт: {name}"
        else:
            text += "\n🎁 Бонус: -100 стібків до наступного завдання."
            # реалізуємо як тимчасовий амулет
            grant_artifact(uid, "amulet_light")
    elif value == 6:
        name = grant_artifact(uid, random.choice(list(ARTIFACTS.keys())))
        text += f"\n🎁 Випав артефакт: {name}"

    save_db(db)
    await m.reply(text, parse_mode="Markdown")

@rt.message(F.text == "🎒 Інвентар")
@rt.message(Command("bag"))
async def show_bag(m: types.Message):
    uid = str(m.from_user.id)
    inv = db["inventory"].get(uid, {})
    if not inv:
        await m.reply("🎒 Порожньо. Артефакти ще не знайдені.")
        return
    lines = ["🎒 Твої артефакти:"]
    for code,count in inv.items():
        lines.append(f"• {ARTIFACTS[code]['name']} ×{count} — {ARTIFACTS[code]['effect']}")
    await m.reply("\n".join(lines), parse_mode="Markdown")

@rt.message(F.text == "📊 Моя статистика")
@rt.message(Command("mystats"))
async def mystats(m: types.Message):
    uid = str(m.from_user.id)
    s = db["stats"].get(uid)
    if not s:
        await m.reply("Поки що немає статистики. Надішли хоч один звіт 🧵")
        return
    debt = db["debts"].get(uid, 0)
    cur = db["progress"][uid]["current"]
    await m.reply(
        f"📊 Твоя статистика\n"
        f"Звіти: {s.get('reports',0)}\n"
        f"Сумарно стібків: {s.get('stitches_total',0)}\n"
        f"Поточне завдання: #{cur if cur<=len(TASKS) else 'фінал'}\n"
        f"Борг стібків: {debt}",
        parse_mode="Markdown"
    )

# ================== ФОТО (ОПЛАТА/ЗВІТ) ==================
REPORT_RE = re.compile(r"^\s*звіт\s*:\s*(старт|фініш)\s+(\d+)\s*$", re.I)

@rt.message(F.photo)
async def on_photo(m: types.Message):
    uid = str(m.from_user.id)
    ensure_user(m.from_user.id, m.from_user)
    caption = (m.caption or "").strip()

    # ----- Фото-звіт -----
    if REPORT_RE.search(caption):
        kind, stitches = REPORT_RE.search(caption).groups()
        kind = kind.lower()
        stitches = int(stitches)
        if stitches < 300 or stitches > 1200:
            await m.reply("⚠ Дозволено 300–1200 стібків за один звіт.")
            return

        # зафіксуємо звіт у статистиці ТИМЧАСОВО (лише кількість звітів росте після підтвердження)
        # тут просто відправляємо в адмін, а гравчині даємо НАСТУПНЕ завдання одразу, як ти просила
        # 1) Надсилаємо в адмін-групу з кнопками
        cap = (f"📜 Фото-звіт\n"
               f"👤 {m.from_user.first_name} (@{m.from_user.username or '—'}) | ID {uid}\n"
               f"📌 Тип: {kind}\n"
               f"🧵 Стібків: {stitches}")
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Зарахувати", callback_data=f"okrep|{uid}|{kind}|{stitches}")
        kb.button(text="❌ Відхилити",  callback_data=f"badrep|{uid}")
        kb.button(text="⚠ Кара",       callback_data=f"punish|{uid}")
        kb.adjust(3)
        await bot.send_photo(ADMIN_CHAT_ID, m.photo[-1].file_id, caption=cap, reply_markup=kb.as_markup())
        await m.reply("🧾 Звіт надіслано адміну. Дякую!")

        # 2) Якщо це фініш — видати наступне завдання ОДРАЗУ
        if kind == "фініш":
            # перекриваємо борг стібків із майбутніх завдань автоматично
            # (борг віднімається по 50/100 за крок — спростимо: знімемо одразу з наступного)
            cur = db["progress"][uid]["current"]
            if cur <= len(TASKS):
                t = TASKS[cur-1]
                base = t["stitches"]
                # застосувати амулет та борг
                base = apply_artifact_effects_on_next(uid, base)
                debt = db["debts"].get(uid, 0)
                if debt > 0:
                    take = min(debt, base//2)  # не більше половини завдання
                    db["debts"][uid] = debt - take
                    base = max(50, base - take)
                save_db(db)
                await m.reply("🎯 Наступне завдання:", parse_mode=None)
                await m.reply(task_card({**t, "stitches": base}), parse_mode="Markdown")
                db["progress"][uid]["current"] = cur + 1
                save_db(db)
            else:
                await m.reply("🏁 Фінал! Усі 150 завдань виконано. Ти — Майстриня Осердя ✨")
        return

    # ----- Скрин оплати -----
    reg = db["registrations"].get(uid)
    if not reg:
        # ще не додані — створимо pending
        db["pending"][uid] = {"game": "tayemnyci", "requested_at": datetime.now().isoformat(timespec="seconds")}
        save_db(db)

    cap = (f"💳 Скриншот оплати\n"
           f"👤 {m.from_user.first_name} (@{m.from_user.username or '—'}) | ID {uid}\n"
           f"🎮 Гра: {game_name('x')}\n"
           f"💳 Картка: {PAYMENT_CARD}")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Підтвердити оплату", callback_data=f"apprpay|{uid}")
    kb.button(text="❌ Відхилити",          callback_data=f"declpay|{uid}")
    kb.adjust(2)
    await bot.send_photo(ADMIN_CHAT_ID, m.photo[-1].file_id, caption=cap, reply_markup=kb.as_markup())
    await m.reply("✅ Скрин відправлено адміну. Статус дивись у «🧵 Статус»")

# ================== ДІЇ АДМІНА ==================
@rt.callback_query(F.data.contains("|"))
async def admin_actions(call: types.CallbackQuery):
    if call.message.chat.id != ADMIN_CHAT_ID:
        await call.answer("Кнопки діють лише в адмін-групі", show_alert=True)
        return
    parts = call.data.split("|")
    action = parts[0]
    try:
        if action in ("apprpay","declpay"):
            _, uid = parts
            if action == "apprpay":
                db["registrations"][uid] = {"game":"tayemnyci", "approved": True, "approved_at": datetime.now().isoformat(timespec="seconds")}
                db["pending"].pop(uid, None)
                save_db(db)
                await bot.send_message(int(uid), f"🎉 Оплату підтверджено! Стартуй у «🎯 Завдання».", parse_mode="Markdown")
            else:
                db["pending"].pop(uid, None); save_db(db)
                await bot.send_message(int(uid), "❌ Оплату не підтверджено. Спробуй ще раз або напиши адміну.")
            await call.message.edit_reply_markup(reply_markup=None); await call.answer("ОК")

        elif action == "okrep":
            _, uid, kind, stitches = parts
            stitches = int(stitches)
            # підтверджуємо статистику
            db["stats"].setdefault(uid, {"name":"", "username":"", "reports":0, "stitches_total":0})
            db["stats"][uid]["reports"]        = db["stats"][uid].get("reports",0) + 1
            db["stats"][uid]["stitches_total"] = db["stats"][uid].get("stitches_total",0) + stitches
            save_db(db)
            await bot.send_message(int(uid), f"✅ Зараховано {stitches} стібків ({kind}). Молодчинка! 🧵")
            await call.message.edit_reply_markup(reply_markup=None); await call.answer("ОК")

        elif action == "badrep":
            _, uid = parts
            # штраф або борг; scissors_fate може зняти
            debt_add = 150
            inv = db["inventory"].setdefault(uid, {})
            if inv.get("scissors_fate",0)>0:
                inv["scissors_fate"] -= 1
                if inv["scissors_fate"]<=0: inv.pop("scissors_fate",None)
                msg = "✂ Кара знята ножицями долі. Штраф не накладено."
            else:
                db["debts"][uid] = db["debts"].get(uid,0) + debt_add
                msg = f"⚠ Звіт відхилено. Накладено борг: +{debt_add} стібків, знімемо з наступних завдань."
            save_db(db)
            await bot.send_message(int(uid), msg)
            await call.message.edit_reply_markup(reply_markup=None); await call.answer("ОК")

        elif action == "punish":
            _, uid = parts
            db["debts"][uid] = db["debts"].get(uid,0) + 200
            save_db(db)
            await bot.send_message(int(uid), "🕯 Містична кара: +200 боргу стібків. Спокутуй у наступних завданнях.")
            await call.message.edit_reply_markup(reply_markup=None); await call.answer("ОК")

    except Exception as e:
        await call.answer(str(e), show_alert=True)

# ================== ДІАГНОСТИКА ==================
@rt.message(Command("id"))
async def show_id(m: types.Message):
    await m.reply(f"chat_id: {m.chat.id}")

@rt.message(Command("test_admin"))
async def test_admin(m: types.Message):
    try:
        await bot.send_message(ADMIN_CHAT_ID, f"🔔 Тест від {m.from_user.first_name} (id {m.from_user.id})")
        await m.reply("✅ Надіслав тест у адмін-групу")
    except Exception as e:
        await m.reply(f"❌ Не зміг надіслати в адмін-групу: {e}")

# ================== СТАРТ ==================
async def main():
    logging.info("🚀 Бот запущений і готовий до гри!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())