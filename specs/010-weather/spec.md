# Спецификация функционала: 010 — Погода (§71)

## Outcome

Мир имеет погоду: на каждый игровой день формируется `weather_state` (temperature, wind, precipitation, cloudiness, visibility — §71) из **двух источников**:
- **synthetic** (default, MVP): детерминированная искусственная генерация (seed+день) — разрешена §71.
- **historical**: **реальная погода на острове Рейнеке (42.98°N, 132.55°E, залив Петра Великого) год назад за тот же календарный день**. Игровое время отображается на реальное с модификатором скорости (time_scale — «как в Симс»): игровой день N соответствует реальной дате `start_real_date + N·(1440/time_scale) минут`; погода берётся за эту дату минус 1 год из архива Open-Meteo Historical Weather API (ERA5, бесплатно, без ключа, JSON, timezone=Asia/Vladivostok).

Погода видна в API и в шапке UI, участвует в Flux-descriptor (вместо хардкода «clear») и мягко влияет на needs-декей (MVP: множитель на холод).

## Scope

- **Included**:
  - Таблица `weather_state` (§71): id, world_id, day (unique вместе с world_id), temperature (Float, °C), wind (Float, м/с), precipitation (Float, мм), cloudiness (Float 0-1), visibility (Float, км), `source` (String: synthetic|historical), `real_date` (String ISO, дата реального мира, от которой взята погода). Schema 0.8.0 → **0.9.0** (38 таблиц).
  - Synthetic-генерация `app/simulation/weather.py::generate_weather(day, seed, config) -> WeatherSample`: детерминированный rng (sha256(seed, day) → uniform-потоки), сезонный базовый профиль из конфига (усреднённый климат Приморья: t −18…+24 по сезонам, ветер 0-15, осадки 0-12, облачность 0-1, видимость 0.2-30).
  - Historical-источник `fetch_historical_weather(real_date, config) -> WeatherSample | None`: GET `https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={d}&end_date={d}&daily=temperature_2m_mean,wind_speed_10m_max,precipitation_sum,cloud_cover_mean&wind_speed_unit=ms&timezone=Asia%2FVladivostok` (без ключа; таймаут config). Маппинг: temp ← temperature_2m_mean, wind ← wind_speed_10m_max (м/с), precipitation ← precipitation_sum, cloudiness ← cloud_cover_mean/100; **visibility — дериват** (архив ERA5 не содержит видимость): детерминированно из осадков/облачности (ясно→20км, дождь→8км, снег→2км, туман-риск при штиль+высокая облачность→0.5км) с seed-джиттером.
  - Кэш fetch: `data/weather_cache/{real_date}.json` (сырой ответ) — рестарт не рефетчит; офлайн/таймаут/ошибка → синтетический фолбэк для этого дня (source=synthetic) без падения шага.
  - Маппинг игрового дня на реальную дату `real_date_for_game_day(day, settings)`: `start_real_timestamp + day·1440/time_scale минут` (таймзона Asia/Vladivostok), затем `-1 год` (config year_lag). Пример: time_scale=60 → игровой день = 24 реальных минутам; start 2026-01-01 → день 365 ≈ реальное 2026-01-07 → погода за 2025-01-07.
  - Запись на каждый новый день: при первом обращении к дню (лениво, из `get_or_create_weather`) строка создаётся; headless-дни пишутся движком (step → ensure_weather_for_day). Событие **WEATHER_CHANGED** (28-е) при создании записи нового дня (payload: все поля).
  - Конфиг `WeatherConfig`: enabled=true, seed_offset, seasonal_profile (4 сезона: base_temp, amplitude), cold_need_multiplier (порог t<0: hunger/energy декей ×1.15 — мягкое влияние MVP).
  - API: `GET /weather` (auth) — текущий день; `GET /weather/history?days=N` (≤30).
  - UI: шапка — температура/осадки/ветер текстом (из /weather).
  - Flux-descriptor: `build_scene_descriptor/build_portrait_descriptor` получают `weather` строкой (fetch из мира при наличии записи; фолбэк «clear») — §70-каноничность сохраняется (погода входит в prompt → другой детерминированный стаб-PNG, кейстоун: без weather-записей дескриптор байт-идентичен прежнему).
  - Инвариант `weather_integrity`: для каждого дня с записью — ровно одна строка на (world_id, day); значение в валидных диапазонах.
- **Excluded**: текущая (не архивная) погода и прогнозы; реальные метеостанции (NOAA ISD/RIHMI) — Open-Meteo покрывает MVP; влияние на travel/движение; погодные катастрофы; снежный покров; почасовая детализация (только суточная агрегация).
- **Protected boundaries**:
  - П1: влияние на needs — через штатные decay-расчёты (множитель в config, применяется в needs-фазе), без прямых мутаций.
  - П2: **кейстоун**: при enabled=false или отсутствующих записях — байт-идентичность headless M1-M4 (WEATHER_CHANGED не эмитится, дескриптор без weather-суффикса). **source=synthetic — default** (headless-тесты детерминированы без сети); historical включается конфигом владельца на сервере.
  - П4: n/a.

## Requirements

- **R1: Схема/счётчики**: 38 таблиц, schema 0.9.0, событие 28 (WEATHER_CHANGED).
- **R2: Детерминизм**: `generate_weather(day, seed)` — чистая функция; одинаковые (seed, day) → одинаковые поля; разные дни → разные значения (не константа).
- **R3: Движок**: при пересечении дня (step) — ensure запись + событие; idempotent (повторный step того же дня не дублирует).
- **R4: API**: /weather — запись текущего дня (404 если мир без погодных записей? — нет: лениво создаём от seed мира); history — последние N дней по убыванию.
- **R5: Needs-влияние**: t<0 → hunger/energy decay ×cold_need_multiplier (config, default 1.15); t≥0 → ×1.0. Только в AUTONOMOUS/headless decay-фазе (DIRECT не трогаем — игрок платит сам? — decay единый, влияние на всех).
- **R6: Flux**: дескриптор включает `weather, <описание>`; при отсутствии записи — прежний байт-поток.
- **R7: UI**: шапка показывает «Погода: −5°C, снег, ветер 3 м/с» из GET /weather; при enabled=false — ничего.
- **R8: Инвариант weather_integrity** зелёный на живых мирах.

## Non-Functional Requirements

- **Performance**: генерация O(1) на день; ensure — один SELECT+INSERT.
- **Compatibility**: schema 0.9.0; события 28; UI деградирует без /weather (enabled=false).
- **Security**: n/a (данные мира).

## Acceptance Evidence

- **AE1''''''**: 3 дня headless с enabled=true → 3 записи weather_state, 3 события WEATHER_CHANGED, детерминизм (два прогона одинаковых seed → идентичные строки), инвариант зелёный.
- **AE2''''''**: кейстоун: enabled=false → 0 записей, 0 событий, отчёт байт-идентичен пину; дескриптор Flux без weather-суффикса.
- **AE3''''''**: API: /weather возвращает поля §71; history N дней по убыванию; 401 без auth.
- **AE4''''''**: needs: холодный день (t<0) → декей голода ×1.15 (unit-тест на needs-фазе с подменённой погодой).
- **AE5''''''**: UI: /static/app.js содержит рендер погоды в шапке (grep); index.html нет регрессий.
- **AE6''''''**: Flux: descriptor с записью погоды содержит «weather,» строку; без записи — нет.
