from fastapi import FastAPI, HTTPException
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandObject, Command
from aiogram.enums import ParseMode
from aiogram.types import Message
from contextlib import asynccontextmanager
from functools import wraps
import asyncio
import uuid
import time
import random
import redis

# Redis
r = redis.asyncio.from_url("redis://red-d1o905ruibrs73cf7km0:6379", decode_responses=True)

# Telegram
TOKEN = "8134083101:AAFcVKlE-3bVSI_68rihZWkWPFRY4EJKu7E"
chat_id = -1002806306845  # Группа
admin_ids = [5415079744]  # Впишите ID админов

# Webhook
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://hide-and-seek-gz7u.onrender.com{WEBHOOK_PATH}"
# Настройки игры
default_timer = 600  # 5 минут
wave_intervals = {1: 20, 2: 60, 3: 30}

bot = Bot(token=TOKEN)
dp = Dispatcher()

location_task = None
timer_task = None
monitor_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global location_task, timer_task, monitor_task
    await bot.set_webhook(WEBHOOK_URL)
    try:
        yield
    finally:
        if location_task: location_task.cancel()
        if timer_task: timer_task.cancel()
        if monitor_task: monitor_task.cancel()
        

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping():
	return "pong"

@app.post(WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    telegram_update = types.Update(**update)
    await dp.feed_update(bot, telegram_update)
    return {"ok": True}

def admin_only(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Ищем объект Message в аргументах
        message = None
        for arg in args:
            if isinstance(arg, Message):
                message = arg
                break
        if not message:
            message = kwargs.get("message")

        if message and message.from_user.id not in admin_ids:
            await message.answer("❌ У тебя нет прав.")
            return

        return await func(*args, **kwargs)
    return wrapper

@dp.message(Command("set_timer"))
@admin_only
async def set_timer_cmd(message: Message, command: CommandObject):
    if await r.get("game_started") == "1":
        await message.reply("⚠️ Игра уже идёт.")
        return
    if not command.args or not command.args.isdigit():
        await message.reply("❗ Используй: /set_timer <секунд>")
        return
    await r.set("game_timer", int(command.args))
    await message.reply(f"⏳ Таймер установлен на {command.args} сек.")

@dp.message(Command("set_seekers"))
@admin_only
async def set_seekers_cmd(message: Message, command: CommandObject):
    if not command.args or not command.args.isdigit():
        await message.reply("❗ Используй: /set_seekers <кол-во>")
        return
    count = int(command.args)
    if count < 1:
        await message.reply("❗ Должен быть хотя бы 1 искатель.")
        return
    await r.set("seekers_count", count)
    await message.reply(f"🔢 Количество искателей установлено: {count}")

@dp.message(Command("set_wave"))
@admin_only
async def set_wave_cmd(message: Message, command: CommandObject):
    if await r.get("game_started") == "1":
        await message.reply("⚠️ Игра уже идёт.")
        return
    args = command.args.split() if command.args else []
    if len(args) != 2 or not all(a.isdigit() for a in args):
        await message.reply("❗ Используй: /set_wave <номер> <секунд>")
        return
    wave, sec = map(int, args)
    if wave not in [1, 2, 3]:
        await message.reply("⚠️ Только волны 1, 2 или 3")
        return
    await r.hset("waves", str(wave), sec)
    await message.reply(f"🌊 Волна {wave} теперь каждые {sec} сек.")


@dp.message(Command("reg"))
async def reg_user(message: Message):
    if await r.get("game_started") == "1":
        await message.reply("⚠️ Игра уже идёт.")
        return
    user = message.from_user
    if await r.sismember("players", user.id):
        await message.reply("❗ Уже зарегистрирован.")
        return
    code = random.randint(100000, 999999)
    await r.hset(f"confirm:{user.id}", mapping={
        "user_id": user.id, "first_name": user.first_name, "code": code
    })
    try:
        await bot.send_message(user.id, f"👤 Юзер айди: <code>{user.id}</code>\n🔐 Код: <code>{code}</code>", parse_mode="HTML")
        await message.reply("✅ Проверь личку!")
    except:
        await message.reply("❌ Напиши боту в личку.")

@app.get("/confirm")
async def confirm(user_id: int, code: int):
    conf = await r.hgetall(f"confirm:{user_id}")
    if not conf or str(conf["code"]) != str(code):
        raise HTTPException(400, detail="❌ Код неверный.")
    if await r.sismember("players", user_id):
    	token = await r.hget(f"player:{user_id}", "token")
    	return {"status": "ok", "token": token}
    	
    pid = await r.incr("player_id")
    while True:
    	kill_code = random.randint(100000, 999999)
    	exists = await r.sismember("all_kill_codes", kill_code)
    	if not exists:
    		token = str(uuid.uuid4())
    		await r.hset(f"player:{user_id}", mapping={
    			"player_id": pid, "user_id": user_id, "first_name": conf["first_name"], "token": token, "kills": 0, "kill_code": kill_code
    		})
    		await r.sadd("all_kill_codes", kill_code)
    		break
    await r.sadd("players", user_id)
    return {"status": "ok", "token": token}

@app.get("/location")
async def location(user_id: int, token: str, lat: float, lon: float):
    info = await r.hgetall(f"player:{user_id}")
    if not info or info["token"] != token:
        raise HTTPException(403, detail="❌ Нет доступа")
    await r.hset(f"player:{user_id}", mapping={"lat": lat, "lon": lon, "last": time.time()})
    return {"status": "ok"}

@dp.message(Command("game_start"))
@admin_only
async def game_start(message: Message):
    if await r.get("game_started") == "1":
        await message.reply("⚠️ Игра уже запущена.")
        return

    players = list(await r.smembers("players"))
    if len(players) < 2:
        await message.answer("❗ Нужно минимум 2 игрока.")
        return

    seekers_count = int(await r.get("seekers_count") or 1)
    seekers = random.sample(players, min(seekers_count, len(players)))
    await r.delete("seekers")
    for sid in seekers:
        await r.sadd("seekers", sid)
        await r.set(f"seeker_tries:{sid}", 0)

    await r.set("game_started", "1")

    text = "Вот роли игроков 👀:\n\n"

    for pid in players:
        role = "seeker" if pid in seekers else "hider"
        await r.hset(f"player:{pid}", "role", role)
        player_info = await r.hgetall(f"player:{pid}")
        if role == "seeker":
            text += f"Игрок {player_info['first_name']} — <b>ИСКАТЕЛЬ</b> 🔪!\n"
            try:
                await bot.send_message(pid, "🕵️ Ты — ИСКАТЕЛЬ! Лови их через /kill <код>")
            except:
                pass
        else:
            text += f"Игрок {player_info['first_name']} — <b>ПРЯЧУЩИЙСЯ</b> 🏃!\n"
            try:
                code = await r.hget(f"player:{pid}", "kill_code")
                await bot.send_message(pid, f"🙈 Ты — ПРЯЧУЩИЙСЯ!\n🔐 Твой kill-код: <code>{code}</code>", parse_mode="HTML")
            except:
                pass

    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)

    await asyncio.sleep(10)

    timer = int(await r.get("game_timer") or default_timer)
    waves = await r.hgetall("waves")
    for i in [1, 2, 3]:
        if str(i) not in waves:
            await r.hset("waves", str(i), wave_intervals[i])

    global timer_task, location_task, monitor_task
    timer_task = asyncio.create_task(game_timer(timer))
    location_task = asyncio.create_task(send_locations(timer))
    monitor_task = asyncio.create_task(location_monitor())

    await message.reply("👀 Игра началась!")

@dp.message(Command("kill"))
async def kill_cmd(message: Message, command: CommandObject):
    if await r.get("game_started") != "1":
        await message.reply("⚠️ Игра не идёт.")
        return
    if not await r.sismember("seekers", str(message.from_user.id)):
        await message.reply("❌ Только искатель может использовать эту команду.")
        return
    if not command.args or not command.args.isdigit():
        await message.reply("❗ Используй: /kill <код>")
        return

    kill_code = command.args.strip()
    for user_id in await r.smembers("players"):
        info = await r.hgetall(f"player:{user_id}")
        if info.get("role") == "hider" and info.get("kill_code") == kill_code:
            await r.srem("players", user_id)
            await bot.send_message(user_id, "❌ Ты пойман искателем!")
            try:
                await bot.send_message(chat_id, f"✅ Игрок {info['first_name']} пойман искателем {message.from_user.first_name}!")
            except Exception as e:
                await bot.send_message(chat_id, f"Ошибка отправки: {e}")
            await check_game_end()
            return

    tries_key = f"seeker_tries:{message.from_user.id}"
    tries = int(await r.get(tries_key) or 0) + 1
    await r.set(tries_key, tries)
    await message.reply(f"❌ Неверный код! ({tries}/3)")

    if tries >= 3:
        await stop_game(f"😵 Искатель {message.from_user.first_name} ошибся 3 раза — проиграл!")

async def game_timer(seconds: int):
    start = time.time()
    end = start + seconds
    await r.set("game_started", "1")
    while time.time() < end:
        remain = int(end - time.time())
        if remain % 30 == 0:
            await bot.send_message(chat_id, f"⏳ Осталось: {remain} сек.")
        await asyncio.sleep(1)
    await stop_game("⏱️ Время вышло! Прячущиеся победили!")

async def send_locations(total_seconds):
    players = list(await r.smembers("players"))
    waves = {int(k): int(v) for k, v in (await r.hgetall("waves")).items()}
    timestamps = {1: 0, 2: int(total_seconds * 0.5), 3: int(total_seconds * 0.75)}
    started = time.time()

    while True:
        elapsed = int(time.time() - started)
        percent = (elapsed / total_seconds) * 100

        for wave, start_at in timestamps.items():
            if elapsed >= start_at:
                for user_id in players:
                    data = await r.hgetall(f"player:{user_id}")
                    if data.get("role") != "hider":
                        continue
                    last = float(data.get("last") or 0)
                    if time.time() - last > 120:
                        await remove(user_id)
                        continue
                    if "lat" in data and "lon" in data:
                        await bot.send_location(chat_id=chat_id, latitude=float(data['lat']), longitude=float(data['lon']))
                        await bot.send_message(chat_id, f"📍 Геопозиция игрока {data['first_name']} обновлена.")
                        

                await asyncio.sleep(waves[wave])

async def location_monitor():
    while await r.get("game_started") == "1":
        players = list(await r.smembers("players"))
        for user_id in players:
            data = await r.hgetall(f"player:{user_id}")
            if data.get("role") != "hider":
                continue
            last = float(data.get("last") or 0)
            if time.time() - last > 120:
                await remove(user_id)
        await asyncio.sleep(10)


async def check_game_end():
    hiders_left = 0
    for pid in await r.smembers("players"):
        info = await r.hgetall(f"player:{pid}")
        if info.get("role") == "hider":
            hiders_left += 1
    if hiders_left == 0:
        await stop_game("🔪 Искатель победил! Все пойманы.")

async def stop_game(reason: str):
    global timer_task, location_task, monitor_task
    if timer_task: timer_task.cancel()
    if location_task: location_task.cancel()
    if monitor_task: monitor_task.cancel()

    await bot.send_message(chat_id, f"🔚 Игра окончена!\n{reason}")

    # Удаление всех попыток искателей и сам список
    seekers = await r.smembers("seekers")
    for sid in seekers:
        await r.delete(f"seeker_tries:{sid}")
    await r.delete("seekers")

    await r.set("game_started", "0")
    await r.delete("game_timer")
    await r.delete("waves")
    await r.delete("waves_percent")

    # Удалить игроков
    for pid in await r.smembers("players"):
        await r.delete(f"player:{pid}")
    await r.delete("players")
    await r.delete("all_kill_codes")

    # Очистка подтверждений
    for key in await r.keys("confirm:*"):
        await r.delete(key)

    # Сбросить счётчик player_id
    await r.set("player_id", 0)

@dp.message(Command("game_cancel"))
@admin_only
async def game_cancel(message: Message):
    await stop_game("🧹 Игра остановлена админом.")
    
@dp.message(Command("mycode"))
async def show_my_code(message: Message):
    if not await r.get("game_started") == "1":
        await message.reply("⚠️ Игра не началась.")
        return
    info = await r.hgetall(f"player:{message.from_user.id}")
    if not info or info.get("role") != "hider":
        await message.reply("❌ Ты не прячущийся.")
        return
    kill_code = info.get("kill_code", "❌ Не найден")
    await message.reply(f"🔑 Твой kill-код: <code>{kill_code}</code>", parse_mode="HTML")

async def remove(pid):
    info = await r.hgetall(f"player:{pid}")
    await bot.send_message(chat_id, f"🚫 Игрок {info['first_name']} выбыл (не отправил геолокацию).")
    await r.srem("players", pid)
    await r.delete(f"player:{pid}")
    await check_game_end()


if __name__ == "__main__":
    import uvicorn
    import os
    
    uvicorn.run("main:app", host="0.0.0.0", port=os.environ.get("PORT", 8000))