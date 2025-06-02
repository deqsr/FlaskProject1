from flask import Flask, render_template, redirect, url_for, g
from models import db, Station, AirReading  # Переконайтесь, що models.py в тому ж каталозі
import requests
import os
from datetime import datetime, timedelta, timezone
import click

# --- Конфігурація ---
app = Flask(__name__)
# ВАШ WAQI API ТОКЕН (змініть на свій!)
WAQI_API_TOKEN = os.environ.get('WAQI_API_TOKEN', "36899e4e9eb89d9f69905d595ee34b3c0183b254")  # !!! ЗАМІНІТЬ ЦЕ !!!

# Налаштування SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///air_quality.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Список міст для моніторингу (ключ: назва для API, значення: назва для відображення)
# !!! ВАЖЛИВО: Перевірте та оновіть ці ідентифікатори (ключі словника) на сайті aqicn.org !!!
# Наприклад, якщо для Харкова API не знаходить "Kharkiv", спробуйте "kharkov" або ID станції "@XXXX"
CITIES_TO_MONITOR = {
    "Kyiv": "Київ, Центральна станція",
    "Lviv": "Львів, Міська станція",
    "Kharkiv": "Харків, Моніторингова станція",  # <--- ПЕРЕВІРТЕ ЦЕЙ ІДЕНТИФІКАТОР!
    "Odesa": "Одеса, Прибережна станція",
    "Dnipro": "Дніпро, Промислова станція",
}
DATA_EXPIRY_MINUTES = 30


# --- Допоміжні функції ---
def get_aqi_category_and_color(aqi_value):
    category = "Невідомо"
    color_class = "unknown"
    description = "Дані відсутні або неповні."

    if aqi_value is None:
        return category, color_class, description

    if 0 <= aqi_value <= 50:
        category = "Добре"
        color_class = "good"
        description = "Якість повітря вважається задовільною, і забруднення повітря становить невеликий ризик або не становить жодного."
    elif 51 <= aqi_value <= 100:
        category = "Помірно"
        color_class = "moderate"
        description = "Якість повітря є прийнятною; однак, для деяких забруднювачів може існувати помірний ризик для здоров'я дуже невеликої кількості людей, які надзвичайно чутливі до забруднення повітря."
    elif 101 <= aqi_value <= 150:
        category = "Нездоровий для чутливих груп"
        color_class = "unhealthy-sensitive"
        description = "Члени чутливих груп можуть відчувати вплив на здоров'я. Більшість населення навряд чи постраждає."
    elif 151 <= aqi_value <= 200:
        category = "Нездоровий"
        color_class = "unhealthy"
        description = "Кожен може почати відчувати вплив на здоров'я; члени чутливих груп можуть відчувати більш серйозні наслідки."
    elif 201 <= aqi_value <= 300:
        category = "Дуже нездоровий"
        color_class = "very-unhealthy"
        description = "Попередження про здоров'я: кожен може відчути більш серйозні наслідки для здоров'я."
    elif aqi_value > 300:
        category = "Небезпечний"
        color_class = "hazardous"
        description = "Попередження про здоров'я надзвичайних умов. Усе населення, ймовірно, постраждає."
    return category, color_class, description


def fetch_and_store_air_quality(station_name_api, station_display_name, force_refresh=False):
    if WAQI_API_TOKEN == "YOUR_WAQI_TOKEN_HERE":
        print("ПОМИЛКА: Будь ласка, встановіть свій WAQI_API_TOKEN в app.py")
        return None  # Повертаємо None, щоб вказати на серйозну помилку конфігурації

    # Знаходимо або створюємо станцію
    station = Station.query.filter_by(name=station_name_api).first()
    if not station:
        # Це не повинно траплятися часто, якщо init-db працює правильно
        print(f"Станція {station_name_api} не знайдена в БД. Спроба створити...")
        station = Station(name=station_name_api, display_name=station_display_name)
        db.session.add(station)
        try:
            db.session.commit()
            print(f"Станцію {station_name_api} успішно створено в БД.")
        except Exception as e:
            db.session.rollback()
            print(f"КРИТИЧНО: Помилка при збереженні нової станції {station_name_api} в БД: {e}")
            return None  # Не можемо продовжити без об'єкта станції

    # Перевірка кешу, якщо не примусове оновлення
    if not force_refresh:
        latest_reading = AirReading.query.filter_by(station_id=station.id) \
            .order_by(AirReading.timestamp.desc()) \
            .first()
        if latest_reading and (datetime.utcnow() - latest_reading.timestamp) < timedelta(minutes=DATA_EXPIRY_MINUTES):
            print(f"Використання кешованих даних для {station_display_name} (ID: {station.id})")
            return latest_reading

    print(f"Оновлення даних для {station_display_name} (API: {station_name_api}) з API...")
    url = f"https://api.waqi.info/feed/{station_name_api}/?token={WAQI_API_TOKEN}"

    reading_data_to_store = {
        "station_id": station.id,
        "timestamp": datetime.utcnow(),  # Час нашого запису
        "data_time": None, "aqi": None, "pm25": None, "pm10": None,
        "o3": None, "no2": None, "so2": None, "co": None
    }

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Викине помилку для 4xx/5xx
        data = response.json()

        if data.get("status") == "ok" and data.get("data"):
            api_data = data["data"]

            # Час вимірювання даних
            data_iso_time_str = api_data.get("time", {}).get("iso")
            if data_iso_time_str:
                try:
                    parsed_data_time = datetime.fromisoformat(data_iso_time_str)
                    if parsed_data_time.tzinfo:  # Перетворити в UTC
                        reading_data_to_store["data_time"] = parsed_data_time.astimezone(timezone.utc).replace(
                            tzinfo=None)
                    else:  # Якщо немає tzinfo, припускаємо, що це вже UTC (або найкраще, що можемо зробити)
                        reading_data_to_store["data_time"] = parsed_data_time
                except ValueError:
                    print(f"Не вдалося розпарсити час ISO для {station_name_api}: {data_iso_time_str}")

            # AQI
            aqi_val = api_data.get("aqi")
            if isinstance(aqi_val, (int, float)):
                reading_data_to_store["aqi"] = aqi_val
            elif aqi_val is not None:  # Якщо не число, але не None (напр. "-")
                print(f"Нечислове значення AQI для {station_name_api}: {aqi_val}")

            # Індивідуальні показники
            iaqi = api_data.get("iaqi", {})

            def get_iaqi_value(pollutant_code):
                pollutant_data = iaqi.get(pollutant_code, {}).get('v')
                return pollutant_data if isinstance(pollutant_data, (int, float)) else None

            reading_data_to_store["pm25"] = get_iaqi_value('pm25')
            reading_data_to_store["pm10"] = get_iaqi_value('pm10')
            reading_data_to_store["o3"] = get_iaqi_value('o3')
            reading_data_to_store["no2"] = get_iaqi_value('no2')
            reading_data_to_store["so2"] = get_iaqi_value('so2')
            reading_data_to_store["co"] = get_iaqi_value('co')

            print(f"Успішно отримано дані для {station_name_api}.")

        elif data.get("status") == "error" or (
                data.get("data") and isinstance(data.get("data"), str) and "Unknown station" in data.get("data")):
            # Специфічна обробка "Unknown station"
            print(f"ПОМИЛКА API для {station_name_api}: {data.get('data', 'Немає поля data або невідома помилка')}")
        else:  # Інші помилки API або неочікуваний формат
            print(f"Неочікувана відповідь API для {station_name_api}: {data}")

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:  # Станція не знайдена
            print(f"ПОМИЛКА API (404) для {station_name_api}: Станцію не знайдено. Перевірте ідентифікатор.")
        else:
            print(f"HTTP помилка запиту до API для {station_name_api}: {e}")
    except requests.exceptions.RequestException as e:  # Інші мережеві помилки
        print(f"Помилка запиту до API для {station_name_api}: {e}")
    except Exception as e:  # Неочікувані помилки при обробці JSON тощо
        print(f"Неочікувана помилка при обробці даних для {station_name_api}: {e}")

    # Завжди зберігаємо запис (можливо, частково заповнений або порожній при помилці)
    # Це оновлює timestamp останньої спроби
    new_reading = AirReading(**reading_data_to_store)
    db.session.add(new_reading)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Помилка при збереженні запису AirReading для {station_name_api}: {e}")
        return None  # Якщо не вдалося зберегти, повертаємо None

    return new_reading


# --- Глобальні налаштування для шаблонів ---
@app.before_request
def before_request_tasks():
    """Встановлює поточний рік для використання в шаблонах."""
    g.current_year = datetime.utcnow().year


# --- Маршрути ---
@app.route('/')
def index():
    if WAQI_API_TOKEN == "YOUR_WAQI_TOKEN_HERE":
        return "<h1>Будь ласка, встановіть свій WAQI_API_TOKEN в змінній WAQI_API_TOKEN в app.py або як змінну середовища.</h1>"

    all_stations_display_data = []
    active_alerts = []

    for city_api_name, city_display_name in CITIES_TO_MONITOR.items():
        # Переконуємося, що станція існує в БД (має бути створена через init-db)
        station_obj = Station.query.filter_by(name=city_api_name).first()

        if not station_obj:
            # Якщо станції немає, це проблема конфігурації або init-db не був запущений правильно
            print(f"УВАГА: Станція {city_api_name} не знайдена в БД. Пропущено. Запустіть 'flask init-db'.")
            all_stations_display_data.append({
                "name": city_display_name,
                "location": f"{city_api_name}, Україна",
                "error": f"Станція '{city_api_name}' не налаштована в БД. Зверніться до адміністратора."
            })
            continue  # Переходимо до наступної станції

        # Отримуємо або оновлюємо дані
        latest_reading = fetch_and_store_air_quality(station_obj.name, station_obj.display_name)

        if latest_reading:  # Навіть якщо дані "порожні", об'єкт буде
            category, color_class, _ = get_aqi_category_and_color(latest_reading.aqi)

            display_timestamp = "N/A"
            if latest_reading.data_time:
                try:  # Намагаємося конвертувати в локальний час для відображення, якщо є TZ
                    local_dt = latest_reading.data_time.replace(tzinfo=timezone.utc).astimezone()
                    display_timestamp = local_dt.strftime("%Y-%m-%d %H:%M %Z")
                except:  # Якщо щось пішло не так або немає TZ info, показуємо як є (UTC)
                    display_timestamp = latest_reading.data_time.strftime("%Y-%m-%d %H:%M UTC")

            station_data = {
                "id": station_obj.id,
                "name": station_obj.display_name,
                "location": f"{station_obj.name}, {station_obj.country}",  # station_obj.name - це API ключ
                "aqi": latest_reading.aqi,  # Може бути None
                "category": category,
                "color_class": color_class,
                "pm2_5": latest_reading.pm25,  # Може бути None
                "pm10": latest_reading.pm10,
                "o3": latest_reading.o3,
                "no2": latest_reading.no2,
                "so2": latest_reading.so2,
                "co": latest_reading.co,
                "timestamp": display_timestamp,  # Час даних з API
                "db_timestamp": latest_reading.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")  # Час оновлення в БД
            }
            all_stations_display_data.append(station_data)

            # Логіка для активних сповіщень
            if latest_reading.aqi and latest_reading.aqi > 150:  # Поріг для "Unhealthy"
                active_alerts.append({
                    "station_name": station_obj.display_name,
                    "message": f"AQI рівень {category} ({latest_reading.aqi})",
                    "timestamp": display_timestamp
                })
        else:  # Якщо fetch_and_store_air_quality повернув None (серйозна помилка)
            all_stations_display_data.append({
                "name": city_display_name,
                "location": f"{city_api_name}, Україна",
                "error": "Критична помилка отримання або збереження даних для цієї станції."
            })

    return render_template('index.html', stations=all_stations_display_data, alerts=active_alerts)


@app.route('/refresh_all')
def refresh_all_data():
    """Примусово оновлює дані для всіх станцій, ігноруючи кеш."""
    stations_in_db = Station.query.all()
    for station_obj in stations_in_db:
        if station_obj.name in CITIES_TO_MONITOR:  # Оновлюємо тільки ті, що в конфігу
            fetch_and_store_air_quality(station_obj.name, station_obj.display_name, force_refresh=True)
    return redirect(url_for('index'))


# --- Команди CLI ---
@app.cli.command("init-db")
def init_db_command():
    """Створює таблиці бази даних та початкові станції з CITIES_TO_MONITOR."""
    with app.app_context():  # Переконатись, що ми в контексті додатку
        db.create_all()
        click.echo("Базу даних ініціалізовано (таблиці створено/перевірено).")

        existing_station_names = {s.name for s in Station.query.all()}
        added_count = 0
        for city_api_name, city_display_name in CITIES_TO_MONITOR.items():
            if city_api_name not in existing_station_names:
                station = Station(name=city_api_name, display_name=city_display_name,
                                  country="Україна")  # Додав країну за замовчуванням
                db.session.add(station)
                added_count += 1
                click.echo(f"Додано станцію до БД: {city_display_name} (API ключ: {city_api_name})")

        if added_count > 0:
            try:
                db.session.commit()
                click.echo(f"Успішно додано {added_count} нових станцій до БД.")
            except Exception as e:
                db.session.rollback()
                click.echo(f"Помилка при збереженні нових станцій: {e}", err=True)
        else:
            click.echo("Всі сконфігуровані станції (з CITIES_TO_MONITOR) вже існують в БД.")


if __name__ == '__main__':
    # Для запуску через `python app.py` (не рекомендовано для розробки з Flask CLI)
    # Щоб створити БД при першому запуску через `python app.py`:
    # with app.app_context():
    #     init_db_command.callback() # Викликаємо функцію команди
    app.run(debug=True)