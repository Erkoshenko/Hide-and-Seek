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

@asynccontextmanager
async def lifespan(app: FastAPI):
    global location_task, timer_task
    await bot.set_webhook(WEBHOOK_URL)
    try:
        yield
    finally:
        if location_task: location_task.cancel()
        if timer_task: timer_task.cancel()

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
    global location_task, timer_task
    if await r.get("game_started") == "1":
        await message.reply("⚠️ Игра уже запущена.")
        return
    players = await r.smembers("players")
    if len(players) < 2:
        await message.reply("⚠️ Мин. 2 игрока")
        return
    await r.set("game_started", "1")
    timer = int(await r.get("game_timer") or default_timer)
    waves = await r.hgetall("waves") or {str(k): v for k, v in wave_intervals.items()}
    seeker_id = await r.srandmember("players")
    await r.set("seeker", seeker_id)
    await r.hset(f"player:{seeker_id}", "role", "seeker")
    text = "👥 Игроки:\n\n"
    
    for pid in players:
        info = await r.hgetall(f"player:{pid}")
        if pid == seeker_id:
            try:
                await bot.send_message(pid, "🔪 Ты искатель. Пиши /kill <код>!", parse_mode="HTML")
            except:
            	await remove(pid)
            	await check_game_end()
            
            role = "искатель 🔪"
        else:
            await r.hset(f"player:{pid}", "role", "hider")
            try:
                await bot.send_message(pid, f"🏃 Ты прячущийся!\nВот твой килл код: <code>{info['kill_code']}</code>", parse_mode="HTML")
            except:
            	await remove(pid)
            	await check_game_end()
            	
            role = "прячущийся 🏃"
        text += f"<b>{info['first_name']}</b>: {role}\n"
        
    await bot.send_message(chat_id, text, parse_mode="HTML")
    await asyncio.sleep(60)
    location_task = asyncio.create_task(send_locations(waves, timer))
    timer_task = asyncio.create_task(game_timer(timer))

@dp.message(Command("mycode"))
async def show_my_code(message: Message):
    if not await r.get("game_started") == "1":
        await message.reply("⚠️ Игра не началась.")
        return
    info = await r.hgetall(f"player:{message.from_user.id}")
    kill_code = info.get("kill_code", "❌ Не найден")
    await message.reply(f"🔑 Твой килл код: <code>{kill_code}</code>", parse_mode="HTML")

@dp.message(Command("kill"))
async def kill_cmd(message: Message, command: CommandObject):
    user_id = str(message.from_user.id)
    if user_id != await r.get("seeker"):
        await message.reply("❌ Только искатель может!")
        return
    args = command.args
    if not args:
        await message.reply("❗ /kill <код>")
        return
    attempts = int(await r.get(f"seeker_tries:{user_id}") or 0)
    if attempts >= 3:
        await message.reply("☠️ Ты проиграл! Больше 3 ошибок.")
        await stop_game(f"<b>Искатель</b> проиграл по попыткам.")
        return
    target = None
    for pid in await r.smembers("players"):
        info = await r.hgetall(f"player:{pid}")
        if info.get("kill_code") == args.strip():
            target = pid
            break
    if not target:
        await r.incr(f"seeker_tries:{user_id}")
        await message.reply("❌ Неверный код.")
        return
    await remove(target)
    await bot.send_message(chat_id, f"🔪 Игрок <b>{info['first_name']}</b> пойман!", parse_mode="HTML")
    await check_game_end()

@dp.message(Command("game_cancel"))
@admin_only
async def game_cancel(message: Message):
    await stop_game("🧹 Игра остановлена админом.")
    
async def check_game_end():
    if not await r.get("game_started") == "1":
    	return
    	
    players = await r.smembers("players")
    hiders = 0
    seeker_exists = False

    for pid in players:
        info = await r.hgetall(f"player:{pid}")
        role = info.get("role")
        if role == "hider":
            hiders += 1
        elif role == "seeker":
            seeker_exists = True

    if hiders == 0 and seeker_exists:
        await stop_game("🔪 Искатель победил! Все прячущиеся пойманы.")
        return True
    elif hiders > 0 and not seeker_exists:
        await stop_game("🏃 Прячущиеся победили! Искатель исчез.")
        return True

    return False
    

async def stop_game(reason):
    global location_task, timer_task
    if location_task: location_task.cancel()
    if timer_task: timer_task.cancel()
    await r.set("game_started", "0")
    for pid in await r.smembers("players"):
        await remove(pid)
        
    await r.delete("players")
    await r.delete("player_id")
    for key in await r.keys("seeker_tries:*"):
    	await r.delete(key)
    for key in await r.keys("confirm:*"):
    	await r.delete(key)
    await r.delete("seeker")  # чтобы не остался
    await r.delete("game_timer")
    await r.delete("waves")
    await bot.send_message(chat_id, reason, parse_mode="HTML")

async def game_timer(seconds):
    start_time = time.time()
    
    msg = await bot.send_message(chat_id, f"🌊 1 волна!\n⏳ Осталось {seconds} сек.")
    await bot.pin_chat_message(chat_id, msg.message_id)
    while True:
    	elapsed = time.time() - start_time
    	seconds_left = seconds - int(elapsed)
    	
    	wave = await r.get("current_wave")
    	text = f"🌊 {wave} волна!\n⏳ Осталось {seconds} сек."
    	if seconds_left <= 0:
    		break
    	try:
    		await bot.edit_message_text(text, chat_id=chat_id, message_id=msg.message_id)
    	except:
    		await bot.send_message(chat_id, text)
    	
    	await asyncio.sleep(1)
    	
    try:
    	await msg.delete()
    except:
    	pass
    await stop_game("⌛ Время вышло. Прячущиеся победили!")

async def send_locations(waves, total_seconds):
    wave_percents = await r.hgetall("waves_percent")
    wave_percents = {
        2: int(wave_percents.get("2", 30)),
        3: int(wave_percents.get("3", 70)),
    }
    wave_percents = dict(sorted(wave_percents.items(), key=lambda x: x[1]))
    start_time = time.time()
    current_wave = "1"

    while True:
        elapsed = time.time() - start_time
        percent_passed = (elapsed / total_seconds) * 100

        # Определяем текущую волну по проценту
        for wave, percent in wave_percents.items():
            if percent_passed >= percent:
                current_wave = str(wave)
                await r.set("current_wave", current_wave)

        interval = int(waves.get(current_wave, 60))

        players = await r.smembers("players")
        now = time.time()
        for pid in players:
            info = await r.hgetall(f"player:{pid}")
            if info.get("role") == "seeker":
                continue
            last = float(info.get("last", 0))
            if now - last > 120:
                await remove(pid)
                await check_game_end()
                continue

            lat, lon = info.get("lat"), info.get("lon")
            if lat and lon:
                try:
                    await bot.send_location(chat_id, latitude=float(lat), longitude=float(lon))
                except: pass

        if not players:
            await bot.send_message(chat_id, "🏁 Все игроки выбыли.")
            return

        await asyncio.sleep(interval)

async def remove(pid):
    player = await r.hgetall(f"player:{pid}")
    await r.srem("players", pid)
    await r.srem("all_kill_codes", player['kill_code'])
    await r.delete(f"player:{pid}")
    await r.delete(f"confirm:{pid}")


if __name__ == "__main__":
    import uvicorn
    import os
    
    uvicorn.run("main:app", host="0.0.0.0", port=os.environ.get("PORT", 5000))
    
