import subprocess
import json
import time
import requests
import os

# == НАСТРОЙКИ ==
FASTAPI_HOST = "https://hide-and-seek-gz7u.onrender.com"
CONFIRM_URL = f"{FASTAPI_HOST}/confirm"
LOCATION_URL = f"{FASTAPI_HOST}/location"


# == Получить текущую локацию ==
def get_location():
    result = subprocess.run(
        ["termux-location", "-p", "gps"],
        stdout=subprocess.PIPE
    )
    location_json = result.stdout.decode("utf-8")
    return json.loads(location_json)

# == Запросить данные у пользователя ==
def input_auth():
    user_id = input("🆔 Введите свой user_id: ").strip()
    confirm_code = input("🔑 Введите код подтверждения: ").strip()

    # Отправка запроса на подтверждение
    response = requests.get(CONFIRM_URL, params={
        "user_id": user_id,
        "code": confirm_code
    })

    if response.status_code != 200:
        print("❌ Ошибка подтверждения:", response.text)
        return None

    token = response.json()["token"]
    print("✅ Авторизация успешна.")
    return {"user_id": user_id, "token": token}

# == Главный цикл ==
def main():
        
    print("🔐 Авторизация не найдена. Запрашиваю...")
    auth = input_auth()
    if not auth:
    	return

    user_id = auth["user_id"]
    token = auth["token"]

    while True:
        try:
            loc = get_location()
            lat = loc["latitude"]
            lon = loc["longitude"]

            
            res = requests.get(LOCATION_URL, params={
                "user_id": user_id,
                "token": token,
                "lat": lat,
                "lon": lon
            })

            print("✅ Ответ сервера:", res.json())

        except Exception as e:
            print("⚠️ Ошибка:", e)

        time.sleep(10)

if __name__ == "__main__":
    main()