import time
import aiosqlite
from monsters import MONSTERS, get_monster_info

DB_NAME = "cybermon.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")

        # Таблица игроков
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                pet_name TEXT,
                element TEXT,
                hp INTEGER DEFAULT 100,
                max_hp INTEGER DEFAULT 100,
                atk INTEGER DEFAULT 16,
                def INTEGER DEFAULT 5,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                coins INTEGER DEFAULT 50,
                paid_cases INTEGER DEFAULT 0,
                last_free_case INTEGER DEFAULT 0,
                battles_won INTEGER DEFAULT 0
            )
        """)

        # Миграции структуры для users
        user_cols = [
            "max_hp INTEGER DEFAULT 100",
            "level INTEGER DEFAULT 1",
            "exp INTEGER DEFAULT 0",
            "coins INTEGER DEFAULT 50",
            "paid_cases INTEGER DEFAULT 0",
            "last_free_case INTEGER DEFAULT 0",
            "battles_won INTEGER DEFAULT 0"
        ]
        for col_def in user_cols:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_def}")
            except Exception:
                pass

        # Таблица инвентаря
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_type TEXT,
                item_name TEXT,
                is_listed INTEGER DEFAULT 0
            )
        """)
        try:
            await db.execute("ALTER TABLE inventory ADD COLUMN is_listed INTEGER DEFAULT 0")
        except Exception:
            pass

        # Таблица P2P Маркетплейса
        await db.execute("""
            CREATE TABLE IF NOT EXISTS market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER,
                seller_name TEXT,
                item_id INTEGER,
                item_type TEXT,
                item_name TEXT,
                price_stars INTEGER,
                created_at INTEGER,
                status TEXT DEFAULT 'active'
            )
        """)

        await db.commit()

async def get_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                await db.execute(
                    """
                    INSERT INTO users (
                        user_id, username, pet_name, element, hp, max_hp, atk, def,
                        level, exp, coins, paid_cases, last_free_case, battles_won
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, username, "Вольтфокс", "Электро", 100, 100, 16, 5, 1, 0, 100, 0, 0, 0)
                )
                await db.commit()
                # Стартовый питомец
                await db.execute(
                    "INSERT INTO inventory (user_id, item_type, item_name, is_listed) VALUES (?, 'pet', 'Вольтфокс', 0)",
                    (user_id,)
                )
                # Подарочное зелье новичку
                await db.execute(
                    "INSERT INTO inventory (user_id, item_type, item_name, is_listed) VALUES (?, 'potion', 'Зелье Здоровья 🧪', 0)",
                    (user_id,)
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur2:
                    user = await cur2.fetchone()
            return dict(user)

async def add_item(user_id: int, item_type: str, item_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO inventory (user_id, item_type, item_name, is_listed) VALUES (?, ?, ?, 0)",
            (user_id, item_type, item_name)
        )
        await db.commit()

async def get_inventory(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM inventory WHERE user_id = ? AND is_listed = 0 ORDER BY id DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def set_active_pet(user_id: int, pet_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM inventory WHERE user_id = ? AND item_type = 'pet' AND item_name = ? AND is_listed = 0",
            (user_id, pet_name)
        ) as cursor:
            item = await cursor.fetchone()
            if not item:
                return {"success": False, "message": "Питомец не найден в вашем рюкзаке!"}

        stats = get_monster_info(pet_name)

        async with db.execute("SELECT level FROM users WHERE user_id = ?", (user_id,)) as u_cur:
            user_row = await u_cur.fetchone()
            lvl = user_row["level"] if user_row else 1

        scaled_max_hp = stats["hp"] + (lvl - 1) * 10
        scaled_atk = stats["atk"] + (lvl - 1) * 2
        scaled_def = stats["def"] + (lvl - 1) * 1

        await db.execute(
            """
            UPDATE users 
            SET pet_name = ?, element = ?, hp = ?, max_hp = ?, atk = ?, def = ?
            WHERE user_id = ?
            """,
            (stats["name"], stats["element"], scaled_max_hp, scaled_max_hp, scaled_atk, scaled_def, user_id)
        )
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as user_cur:
            updated_user = await user_cur.fetchone()
            return {"success": True, "message": f"{stats['name']} теперь ваш активный боец!", "user": dict(updated_user)}

async def use_potion(user_id: int, item_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM inventory WHERE id = ? AND user_id = ? AND item_type = 'potion' AND is_listed = 0",
            (item_id, user_id)
        ) as cursor:
            item = await cursor.fetchone()
            if not item:
                return {"success": False, "message": "Зелье не найдено в рюкзаке!"}

        item_dict = dict(item)
        item_name = item_dict["item_name"]

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as user_cur:
            user = await user_cur.fetchone()
            if not user:
                return {"success": False, "message": "Игрок не найден"}
            user = dict(user)

        max_hp = user.get("max_hp", 100) or 100
        cur_hp = user.get("hp", 100) or 100
        cur_atk = user.get("atk", 16) or 16

        if "Здоровья" in item_name:
            if cur_hp >= max_hp:
                return {"success": False, "message": "Здоровье вашего питомца уже на максимуме!"}
            heal = 40
            new_hp = min(max_hp, cur_hp + heal)
            await db.execute("UPDATE users SET hp = ? WHERE user_id = ?", (new_hp, user_id))
            msg = f"Здоровье восстановлено: +{new_hp - cur_hp} HP! ({new_hp}/{max_hp})"
        elif "Атаки" in item_name:
            new_atk = cur_atk + 4
            await db.execute("UPDATE users SET atk = ? WHERE user_id = ?", (new_atk, user_id))
            msg = f"Сила атаки повышена: +4 АТК! (Текущая: {new_atk})"
        else:
            msg = f"Предмет {item_name} успешно применен!"

        await db.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as user_cur2:
            updated_user = await user_cur2.fetchone()
            return {"success": True, "message": msg, "user": dict(updated_user)}

async def update_user_hp(user_id: int, new_hp: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET hp = ? WHERE user_id = ?", (new_hp, user_id))
        await db.commit()

async def award_battle_rewards(user_id: int, exp_gain: int = 35, coins_gain: int = 25):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                return None
            user = dict(user)

        lvl = user.get("level", 1) or 1
        cur_exp = (user.get("exp", 0) or 0) + exp_gain
        cur_coins = (user.get("coins", 0) or 0) + coins_gain
        cur_wins = (user.get("battles_won", 0) or 0) + 1

        leveled_up = False
        max_hp = user.get("max_hp", 100) or 100
        cur_hp = user.get("hp", 100) or 100
        cur_atk = user.get("atk", 16) or 16
        cur_def = user.get("def", 5) or 5

        # Повышение уровня при каждых 100 EXP
        while cur_exp >= 100:
            cur_exp -= 100
            lvl += 1
            max_hp += 12
            cur_hp = max_hp  # Полное исцеление питомца
            cur_atk += 3
            cur_def += 1
            leveled_up = True

        await db.execute(
            """
            UPDATE users
            SET level = ?, exp = ?, coins = ?, battles_won = ?, max_hp = ?, hp = ?, atk = ?, def = ?
            WHERE user_id = ?
            """,
            (lvl, cur_exp, cur_coins, cur_wins, max_hp, cur_hp, cur_atk, cur_def, user_id)
        )
        await db.commit()

        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as u2:
            updated = await u2.fetchone()
            return {
                "leveled_up": leveled_up,
                "exp_gain": exp_gain,
                "coins_gain": coins_gain,
                "user": dict(updated)
            }

async def get_free_case_cooldown(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT last_free_case FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or not row["last_free_case"]:
                return 0
            now = int(time.time())
            cooldown = 86400  # 24 часа
            passed = now - row["last_free_case"]
            return max(0, cooldown - passed)

async def claim_free_case(user_id: int):
    now = int(time.time())
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET last_free_case = ? WHERE user_id = ?", (now, user_id))
        await db.commit()

async def credit_paid_case(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET paid_cases = paid_cases + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def consume_paid_case(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT paid_cases FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row["paid_cases"] <= 0:
                return False
        await db.execute("UPDATE users SET paid_cases = paid_cases - 1 WHERE user_id = ?", (user_id,))
        await db.commit()
        return True

async def list_item_on_market(user_id: int, seller_name: str, item_id: int, price_stars: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM inventory WHERE id = ? AND user_id = ? AND is_listed = 0",
            (item_id, user_id)
        ) as cursor:
            item = await cursor.fetchone()
            if not item:
                return {"success": False, "message": "Предмет не найден или уже выставлен!"}

        item_dict = dict(item)
        now = int(time.time())

        await db.execute("UPDATE inventory SET is_listed = 1 WHERE id = ?", (item_id,))
        await db.execute(
            """
            INSERT INTO market_listings (seller_id, seller_name, item_id, item_type, item_name, price_stars, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (user_id, seller_name, item_id, item_dict["item_type"], item_dict["item_name"], price_stars, now)
        )
        await db.commit()
        return {"success": True, "message": f"{item_dict['item_name']} выставлен на маркет за {price_stars} ⭐️!"}

async def cancel_market_listing(user_id: int, listing_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM market_listings WHERE id = ? AND seller_id = ? AND status = 'active'",
            (listing_id, user_id)
        ) as cursor:
            listing = await cursor.fetchone()
            if not listing:
                return {"success": False, "message": "Лот не найден!"}

        item_id = dict(listing)["item_id"]
        await db.execute("UPDATE market_listings SET status = 'cancelled' WHERE id = ?", (listing_id,))
        await db.execute("UPDATE inventory SET is_listed = 0 WHERE id = ?", (item_id,))
        await db.commit()
        return {"success": True, "message": "Лот снят с продажи!"}

async def get_market_listings():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM market_listings WHERE status = 'active' ORDER BY id DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def buy_market_listing(buyer_id: int, listing_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM market_listings WHERE id = ? AND status = 'active'",
            (listing_id,)
        ) as cursor:
            listing = await cursor.fetchone()
            if not listing:
                return {"success": False, "message": "Лот уже продан или недоступен!"}

        listing_dict = dict(listing)
        if listing_dict["seller_id"] == buyer_id:
            return {"success": False, "message": "Нельзя купить свой собственный лот!"}

        item_id = listing_dict["item_id"]
        await db.execute("UPDATE inventory SET user_id = ?, is_listed = 0 WHERE id = ?", (buyer_id, item_id))
        await db.execute("UPDATE market_listings SET status = 'sold' WHERE id = ?", (listing_id,))
        await db.commit()
        return {"success": True, "message": f"Вы приобрели {listing_dict['item_name']}!"}

async def get_leaderboard():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT username, pet_name, element, level, battles_won, coins
            FROM users
            ORDER BY battles_won DESC, level DESC
            LIMIT 10
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]