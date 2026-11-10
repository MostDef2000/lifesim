"""#293: Russian display dictionaries for internal codes in event texts.

Event payloads carry internal codes (``food_bread``, ``AUTONOMOUS``,
``theft``, …) that must never reach a rendered phrase as-is. Each domain
below maps its known codes to a Russian display name; unknown codes fall
back to the raw code through :func:`label` — a lookup never raises and
never invents a name for a value outside its domain.

Domains are open where the underlying value domain is open (object_type
comes from config, memory_type/law_key grow with new catalog entries):
the dict covers the values known today, the raw-code fallback covers the
rest. Free-text payload fields (purpose/reason/cause) are NOT dictionary
material and stay as-is.
"""

from typing import Any, Dict, Optional

# World-object types (config-driven, open set; known values today).
# Nominative dictionary forms — every consuming slot is nominative-safe
# («Передача: хлеб ×2», «Сгорело: сарай»).
OBJECT_TYPE = {
    "food_bread": "хлеб",
    "food_canned": "консервы",
    "water_bottle": "бутылка воды",
    "jacket": "куртка",
    "boots": "сапоги",
    "hat": "шляпа",
    "food_groceries": "продукты",
    "tool_kit": "набор инструментов",
    "shed": "сарай",
    "generator": "генератор",
    "wood_pile": "поленница",
    "pier_extension": "пристройка к пирсу",
    "haystack": "стог сена",
    "furniture": "мебель",
    "cloth": "ткань",
    "food_stock": "запасы еды",
    "prepared_meal": "готовая еда",
    "fish": "рыба",
    "berries": "ягоды",
}

# Task types: the action registry (app/actions/registry.py) plus the two
# lifecycle task types outside it (TRAVEL_EXTERNAL, CONSTRUCT).
TASK_TYPE = {
    "SLEEP": "сон",
    "EAT": "еда",
    "DRINK": "питьё",
    "WORK": "работа",
    "MOVE": "перемещение",
    "BUY_ITEM": "покупка",
    "SOCIALIZE": "общение",
    "IDLE": "безделье",
    "TRAVEL_EXTERNAL": "внешняя поездка",
    "CONSTRUCT": "строительство",
}

# Goal types (app/api/intent.py); AI_DECISION's decision domain is the
# decision prompt's available_actions catalog (app/ai/prompts.py
# build_decision_prompt) — currently goal-shaped, so a future prompt change
# that adds action-registry codes (EAT, SLEEP, …) is a conscious update here.
GOAL_TYPE = {
    "acquire_items": "запасы",
    "socialize_with": "общение",
    "travel_to": "поездка",
}

# Player control modes (CONTROL_CHANGED from/to; validated at the API).
CONTROL_MODE = {
    "AUTONOMOUS": "автономный",
    "GUIDED": "наставление",
    "DIRECT": "прямое управление",
}

# Relationship bands (RELATIONSHIP_CHANGED payload band).
BAND = {
    "conflicted": "конфликт",
    "stranger": "незнакомец",
    "acquaintance": "знакомый",
    "friend": "друг",
}

# Crime types (CRIME_COMMITED payload type / CRIME_REPORTED crime_type).
CRIME = {
    "theft": "кража",
    "vandalism": "вандализм",
}

# Visual asset kinds (VISUAL_ASSET_CREATED asset_type; closed set).
ASSET_TYPE = {
    "portrait": "портрет",
    "scene": "сцена",
}

# Supply resources (SUPPLY_ARRIVED resource_key).
RESOURCE_KEY = {
    "water": "вода",
}

# External-service types (config-driven, open set; default.yaml services).
SERVICE_TYPE = {
    "treatment": "лечение",
    "purchase": "покупка",
}

# Law keys (LAW_ENACTED/LAW_VIOLATION law_key; config org.laws.catalog).
LAW_KEY = {
    "no_conflict": "запрет конфликтов",
    "night_home": "домой к ночи",
}

# Memory kinds (MEMORY_CREATED memory_type): captured event types plus the
# crime-witness memory; partially open — unknown values fall back raw.
MEMORY_TYPE = {
    "CONFLICT": "ссора",
    "RECONCILIATION": "примирение",
    "ELECTION": "выборы",
    "LAW_VIOLATION": "нарушение закона",
    "ORG_FEAST": "пир",
    "PARTNERSHIP_DECLARED": "объявление пары",
    "CHARACTER_DIED": "смерть",
    "crime_witness": "свидетель преступления",
}


def label(mapping: Dict[str, str], code: Any) -> Optional[str]:
    """Display name for an internal code; unknown code -> the code itself
    (raw fallback); a non-str code (malformed payload value) -> None so
    the consuming template drops its clause. Never raises."""
    if not isinstance(code, str):
        return None
    return mapping.get(code, code)
