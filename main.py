import asyncio
import random
import uvicorn
from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice

from config import BOT_TOKEN, WEBAPP_URL, PORT, HOST
from monsters import MONSTERS, get_monster_info, get_element_multiplier
from database import (
    init_db,
    get_or_create_user,
    add_item,
    get_inventory,
    set_active_pet,
    use_potion,
    get_free_case_cooldown,
    claim_free_case,
    credit_paid_case,
    consume_paid_case,
    award_battle_rewards,
    update_user_hp,
    list_item_on_market,
    cancel_market_listing,
    get_market_listings,
    buy_market_listing,
    get_leaderboard
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Запускаем polling бота параллельно с веб-сервером
    bot_task = asyncio.create_task(dp.start_polling(bot))
    yield
    bot_task.cancel()

app = FastAPI(title="CyberMon TMA Game", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ================= Модели Pydantic =================
class EquipPetRequest(BaseModel):
    user_id: int
    pet_name: str

class UseItemRequest(BaseModel):
    user_id: int
    item_id: int

class UserActionRequest(BaseModel):
    user_id: int

class MarketListRequest(BaseModel):
    user_id: int
    seller_name: str = "Тренер"
    item_id: int
    price_stars: int

class MarketCancelRequest(BaseModel):
    user_id: int
    listing_id: int

class MarketBuyRequest(BaseModel):
    buyer_id: int
    listing_id: int

# ================= Telegram Bot =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await get_or_create_user(message.from_user.id, message.from_user.first_name)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Открыть CyberMon", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        f"⚡️ Привет, {message.from_user.first_name}!\n\n"
        "Добро пожаловать в **CyberMon** — собери сильнейший отряд Кибермонов, участвуй в аренах со стихийными преимуществами, открывай кейсы и торгуй на маркете за Telegram Stars!\n\n"
        "Нажимай кнопку ниже для входа в игру 👇",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_received(message: types.Message):
    user_id = message.from_user.id
    await credit_paid_case(user_id)
    await message.answer("⭐️ Оплата 50 Stars прошла успешно! 1 Элитный кейс зачислен на ваш баланс. Открывайте в игре!")

# ================= API Endpoints =================
@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int, username: str = "Тренер"):
    user = await get_or_create_user(user_id, username)
    user_data = dict(user)
    info = get_monster_info(user_data["pet_name"])
    user_data["image_file"] = info["image"]
    user_data["rarity"] = info["rarity"]
    return user_data

@app.get("/api/buy_case")
async def create_invoice(user_id: int):
    prices = [LabeledPrice(label="Элитный Кейс", amount=50)]
    invoice_link = await bot.create_invoice_link(
        title="Элитный Кейс CyberMon",
        description="Открой, чтобы выбить редкого или легендарного Кибермона!",
        payload=f"buy_case_{user_id}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    return {"invoice_link": invoice_link}

@app.post("/api/open_case")
async def open_elite_case(data: UserActionRequest):
    consumed = await consume_paid_case(data.user_id)
    if not consumed:
        return {"success": False, "message": "У вас нет доступных Элитных кейсов! Купите за 50 ⭐️ в магазине."}

    # Пул наград: все 11 Кибермонов + зелья
    all_pets = list(set([m["name"] for m in MONSTERS.values()]))
    drops = [{"type": "pet", "name": p} for p in all_pets]
    drops.append({"type": "potion", "name": "Зелье Здоровья 🧪"})
    drops.append({"type": "potion", "name": "Усилитель Атаки ⚔️"})

    drop = random.choice(drops)
    await add_item(data.user_id, drop["type"], drop["name"])

    if drop["type"] == "pet":
        m_info = get_monster_info(drop["name"])
        drop["image_file"] = m_info["image"]
        drop["rarity"] = m_info["rarity"]
    else:
        drop["image_file"] = None
        drop["rarity"] = "Item"

    return {"success": True, "drop": drop}

@app.get("/api/free_case_status/{user_id}")
async def get_free_status(user_id: int):
    seconds = await get_free_case_cooldown(user_id)
    return {"can_claim": seconds <= 0, "seconds_left": seconds}

@app.post("/api/open_free_case")
async def open_free_case(data: UserActionRequest):
    seconds = await get_free_case_cooldown(data.user_id)
    if seconds > 0:
        return {"success": False, "message": f"Кейс еще перезаряжается ({seconds} сек.)"}

    await claim_free_case(data.user_id)

    # 65% зелья, 35% питомцы
    if random.random() < 0.65:
        pot_name = random.choice(["Зелье Здоровья 🧪", "Усилитель Атаки ⚔️"])
        drop = {"type": "potion", "name": pot_name, "image_file": None, "rarity": "Item"}
    else:
        starter_pool = ["Вольтфокс", "Пиротиг", "Аквалотль", "Флорафавн", "Фростоул", "Террапан"]
        pet_name = random.choice(starter_pool)
        m_info = get_monster_info(pet_name)
        drop = {"type": "pet", "name": pet_name, "image_file": m_info["image"], "rarity": m_info["rarity"]}

    await add_item(data.user_id, drop["type"], drop["name"])
    return {"success": True, "drop": drop}

@app.get("/api/inventory/{user_id}")
async def fetch_inventory(user_id: int):
    items = await get_inventory(user_id)
    out = []
    for row in items:
        it = dict(row)
        if it["item_type"] == "pet":
            info = get_monster_info(it["item_name"])
            it["image_file"] = info["image"]
            it["element"] = info["element"]
            it["rarity"] = info["rarity"]
        else:
            it["image_file"] = None
            it["element"] = None
            it["rarity"] = "Item"
        out.append(it)
    return out

@app.post("/api/equip_pet")
async def equip_pet(data: EquipPetRequest):
    res = await set_active_pet(data.user_id, data.pet_name)
    if res.get("success") and "user" in res:
        u = res["user"]
        u["image_file"] = get_monster_info(u["pet_name"])["image"]
        res["user"] = u
    return res

@app.post("/api/use_item")
async def use_item(data: UseItemRequest):
    res = await use_potion(data.user_id, data.item_id)
    if res.get("success") and "user" in res:
        u = res["user"]
        u["image_file"] = get_monster_info(u["pet_name"])["image"]
        res["user"] = u
    return res

@app.post("/api/battle/pve")
async def pve_arena(data: UserActionRequest):
    user = await get_or_create_user(data.user_id, "Тренер")
    u_hp = user.get("hp", 100) or 100
    u_max_hp = user.get("max_hp", 100) or 100
    u_atk = user.get("atk", 16) or 16
    u_def = user.get("def", 5) or 5
    u_lvl = user.get("level", 1) or 1
    u_pet = user.get("pet_name", "Вольтфокс")
    u_elem = user.get("element", "Электро")

    if u_hp <= 15:
        return {"success": False, "message": f"{u_pet} ранен (HP: {u_hp})! Восстановите силы зельем в рюкзаке."}

    # Подбор дикого Кибермона
    wild_keys = [k for k in MONSTERS.keys() if k != "Вольт-Фокс"]
    enemy_name = random.choice(wild_keys)
    e_info = MONSTERS[enemy_name]
    e_lvl = max(1, u_lvl + random.choice([-1, 0, 1]))
    e_max_hp = e_info["hp"] + (e_lvl - 1) * 10
    e_hp = e_max_hp
    e_atk = e_info["atk"] + (e_lvl - 1) * 2
    e_def = e_info["def"] + (e_lvl - 1) * 1
    e_elem = e_info["element"]

    # Расчет стихий
    p_mult = get_element_multiplier(u_elem, e_elem)
    e_mult = get_element_multiplier(e_elem, u_elem)

    log = []
    if p_mult > 1.0:
        log.append(f"✨ Преимущество стихий! Ваша {u_elem} сокрушает {e_elem} (+25% урона).")
    elif p_mult < 1.0:
        log.append(f"⚠️ Неудобная стихия! Вражеский {e_elem} устойчив к вашей {u_elem}.")

    p_cur = u_hp
    e_cur = e_hp
    rnd = 1

    while p_cur > 0 and e_cur > 0 and rnd <= 8:
        # Удар игрока
        crit = 1.3 if random.random() < 0.15 else 1.0
        dmg_p = max(6, int(((u_atk * p_mult * crit) - (e_def * 0.35)) + random.randint(-2, 3)))
        e_cur = max(0, e_cur - dmg_p)
        c_note = " 💥 КРИТИЧЕСКИЙ УДАР!" if crit > 1.0 else ""
        log.append(f"⚔️ Раунд {rnd}: {u_pet} атакует и наносит {dmg_p} урона!{c_note} (Враг: {e_cur}/{e_max_hp} HP)")

        if e_cur <= 0:
            break

        # Ответ врага
        dmg_e = max(4, int(((e_atk * e_mult) - (u_def * 0.35)) + random.randint(-2, 2)))
        p_cur = max(0, p_cur - dmg_e)
        log.append(f"🛡 {e_info['name']} контратакует на {dmg_e} урона! (Вы: {p_cur}/{u_max_hp} HP)")
        rnd += 1

    final_hp = max(1, p_cur)
    await update_user_hp(data.user_id, final_hp)

    winner = "player" if e_cur <= 0 else "enemy"
    reward = None

    if winner == "player":
        reward = await award_battle_rewards(data.user_id, exp_gain=35, coins_gain=25)
        if random.random() < 0.25:
            await add_item(data.user_id, "potion", "Зелье Здоровья 🧪")
            log.append("🎁 За победу найдено Зелье Здоровья 🧪!")
        updated_u = reward["user"]
    else:
        updated_u = await get_or_create_user(data.user_id, "Тренер")
        updated_u["hp"] = final_hp

    updated_u["image_file"] = get_monster_info(updated_u["pet_name"])["image"]
    updated_u["rarity"] = get_monster_info(updated_u["pet_name"])["rarity"]

    return {
        "success": True,
        "winner": winner,
        "enemy": {
            "name": e_info["name"],
            "element": e_elem,
            "level": e_lvl,
            "image_file": e_info["image"],
            "max_hp": e_max_hp
        },
        "log": log,
        "rewards": reward,
        "user": updated_u
    }

# ================= Маркетплейс =================
@app.get("/api/marketplace")
async def list_market():
    listings = await get_market_listings()
    for row in listings:
        if row["item_type"] == "pet":
            inf = get_monster_info(row["item_name"])
            row["image_file"] = inf["image"]
            row["element"] = inf["element"]
            row["rarity"] = inf["rarity"]
        else:
            row["image_file"] = None
            row["element"] = None
            row["rarity"] = "Item"
    return listings

@app.post("/api/marketplace/list")
async def create_listing(data: MarketListRequest):
    if data.price_stars < 1 or data.price_stars > 5000:
        return {"success": False, "message": "Цена должна быть от 1 до 5000 Stars"}
    return await list_item_on_market(data.user_id, data.seller_name, data.item_id, data.price_stars)

@app.post("/api/marketplace/cancel")
async def cancel_listing(data: MarketCancelRequest):
    return await cancel_market_listing(data.user_id, data.listing_id)

@app.post("/api/marketplace/buy")
async def buy_listing(data: MarketBuyRequest):
    return await buy_market_listing(data.buyer_id, data.listing_id)

@app.get("/api/leaderboard")
async def leaderboard():
    return await get_leaderboard()

if __name__ == "__main__":
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)