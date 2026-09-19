"""Weather (010, §71): synthetic generation + historical (Open-Meteo ERA5).

Synthetic source is the default (П2: headless determinism without network).
Historical source fetches real weather for Reineke island (42.98N, 132.55E)
one year before the calendar date corresponding to the game day (game time
runs with a time_scale modifier, Sims-style).
"""
import hashlib
import json
import struct
from dataclasses import dataclass
from datetime import datetime as _dt
from datetime import timedelta as _td
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.db.models import WeatherState
from app.events.events import EventType, log_event


@dataclass
class WeatherSample:
    temperature: float
    wind: float
    precipitation: float
    cloudiness: float
    visibility: float
    source: str
    real_date: Optional[str]


def _uniform_stream(key: bytes, count: int) -> list[float]:
    """Deterministic [0,1) stream from a key (sha256 blocks)."""
    out = []
    counter = 0
    while len(out) < count:
        digest = hashlib.sha256(key + struct.pack("<I", counter)).digest()
        for i in range(0, 8, 4):
            (chunk,) = struct.unpack("<I", digest[i * 4 : (i + 1) * 4])
            out.append(chunk / 0xFFFFFFFF)
            if len(out) >= count:
                break
        counter += 1
    return out


def generate_weather(day: int, seed: int, config) -> WeatherSample:
    """Pure synthetic sample for a game day (§71: artificial generation)."""
    season = (day // 91) % 4  # spring, summer, autumn, winter
    base = config.seasonal_profile[season]
    s = _uniform_stream(f"weather:{seed}:{day}".encode(), 5)
    temperature = base.base_temp + (s[0] - 0.5) * 2 * base.amplitude
    wind = round(min(15.0, s[1] * 15.0), 1)
    wet = base.wetness * s[2]
    precipitation = round(min(12.0, wet * 12.0), 1)
    cloudiness = round(min(1.0, 0.25 + s[3] * 0.75), 2)
    if precipitation > 5:
        cloudiness = max(cloudiness, 0.85)
    # visibility: clear→20km, rain→8km, fog risk (calm + overcast)
    visibility = 20.0
    if precipitation > 0.5:
        visibility = 8.0
    if temperature < 0 and precipitation > 0.5:
        visibility = 2.0  # snow
    if wind < 1.0 and cloudiness > 0.9 and s[4] < 0.3:
        visibility = 0.5  # fog
    return WeatherSample(
        temperature=round(temperature, 1), wind=wind,
        precipitation=precipitation, cloudiness=cloudiness,
        visibility=visibility, source="synthetic",
        real_date=None,
    )


def real_date_for_game_day(day: int, settings) -> str:
    """Game day N → real calendar date (time_scale modifier, Sims-style),
    minus year_lag. Game minutes advance time_scale× faster than real."""
    start = _dt.fromisoformat(
        settings.world.start_real_timestamp.replace("Z", "+00:00")
    )
    real_dt = start + _td(minutes=day * 1440 / settings.ticks.time_scale)
    shifted = real_dt - _td(days=365 * getattr(settings.weather, "year_lag", 1))
    return shifted.date().isoformat()


def fetch_historical_weather(real_date: str, config) -> WeatherSample | None:
    """Real Reineke weather for a past date via Open-Meteo ERA5 archive.
    Returns None on any failure (caller falls back to synthetic)."""
    cache_dir = Path(getattr(config, "cache_dir", "data/weather_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{real_date}.json"
    payload = None
    if cache_file.exists():
        payload = json.loads(cache_file.read_text())
    else:
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={config.latitude}&longitude={config.longitude}"
            f"&start_date={real_date}&end_date={real_date}"
            "&daily=temperature_2m_mean,wind_speed_10m_max,precipitation_sum,"
            "cloud_cover_mean&wind_speed_unit=ms&timezone=Asia%2FVladivostok"
        )
        try:
            resp = httpx.get(url, timeout=config.fetch_timeout_s)
            resp.raise_for_status()
            payload = resp.json()
            cache_file.write_text(json.dumps(payload))
        except Exception:
            return None
    try:
        daily = payload["daily"]
        temperature = float(daily["temperature_2m_mean"][0])
        wind = float(daily["wind_speed_10m_max"][0])
        precipitation = float(daily["precipitation_sum"][0])
        cloudiness = round(float(daily["cloud_cover_mean"][0]) / 100.0, 2)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    # visibility: ERA5 archive has no visibility field — deterministic derivative
    visibility = 20.0
    if precipitation > 0.5:
        visibility = 8.0
    if temperature < 0 and precipitation > 0.5:
        visibility = 2.0
    if wind < 1.0 and cloudiness > 0.9:
        visibility = 0.5
    return WeatherSample(
        temperature=round(temperature, 1), wind=round(wind, 1),
        precipitation=round(precipitation, 1), cloudiness=cloudiness,
        visibility=visibility, source="historical", real_date=real_date,
    )


def get_or_create_weather(
    session: Session, world_id: str, day: int, settings
) -> WeatherState | None:
    """Ensure weather rows for every crossed day up to `day` (idempotent;
    bulk steps catch up all intermediate days). Emits WEATHER_CHANGED on
    first creation. Offline history → synthetic fallback. Returns the
    current day's row (or None if disabled)."""
    if not settings.weather.enabled:
        return None
    from sqlalchemy import func as _func

    last = (
        session.query(_func.max(WeatherState.day))
        .filter_by(world_id=world_id)
        .scalar()
    )
    start_day = 0 if last is None else last + 1
    current = None
    for d in range(start_day, day + 1):
        row = (
            session.query(WeatherState)
            .filter_by(world_id=world_id, day=d)
            .first()
        )
        if row is None:
            sample = None
            if settings.weather.source == "historical":
                real_date = real_date_for_game_day(d, settings)
                sample = fetch_historical_weather(real_date, settings.weather)
            if sample is None:
                world_seed = int.from_bytes(
                    hashlib.sha256(world_id.encode()).digest()[:4], "big"
                )
                sample = generate_weather(
                    d, world_seed + settings.weather.seed_offset,
                    settings.weather,
                )
                sample.real_date = (
                    real_date_for_game_day(d, settings)
                    if settings.weather.source == "historical" else None
                )
            row = WeatherState(
                world_id=world_id, day=d,
                temperature=sample.temperature, wind=sample.wind,
                precipitation=sample.precipitation,
                cloudiness=sample.cloudiness, visibility=sample.visibility,
                source=sample.source, real_date=sample.real_date,
            )
            session.add(row)
            session.flush()
            log_event(
                session, world_id=world_id,
                event_type=EventType.WEATHER_CHANGED,
                actor_id=None, location_id=None,
                payload={
                    "day": d, "temperature": sample.temperature,
                    "wind": sample.wind, "precipitation": sample.precipitation,
                    "cloudiness": sample.cloudiness,
                    "visibility": sample.visibility, "source": sample.source,
                },
                game_timestamp=d * 1440,
            )
        current = row
    return current


def describe_weather(row: WeatherState) -> str:
    """Human-readable string for UI header and Flux descriptor."""
    temp = f"{row.temperature:+.0f}°C"
    if row.precipitation > 0.5:
        kind = "снег" if row.temperature < 0 else "дождь"
    elif row.visibility < 1.0:
        kind = "туман"
    elif row.cloudiness > 0.7:
        kind = "пасмурно"
    elif row.cloudiness > 0.4:
        kind = "облачно"
    else:
        kind = "ясно"
    return f"{temp}, {kind}, ветер {row.wind:.0f} м/с"


def need_decay_multiplier(row: WeatherState | None, config) -> float:
    """Cold weather slightly accelerates hunger/energy decay (R5)."""
    if row is None or row.temperature >= 0:
        return 1.0
    return getattr(config, "cold_need_multiplier", 1.15)
