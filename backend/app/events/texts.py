"""#291: human-readable Russian texts for world events.

Rendered at DISPLAY TIME from the stored event row (event_type, actor_id,
target_id, payload) — no schema change, so historical rows get texts
retroactively. Pure display logic: reads Character names in one logical
batched lookup per page (chunked at 400 ids per query), writes nothing.

Contract:
- render_event_texts(session, events) -> {WorldEvent.id: russian text};
- unknown event_type string -> the raw code (fallback);
- missing/None payload data never raises — the phrase just loses its
  clause (historical rows may pre-date payload keys);
- a character id that resolves to no Character row renders as the neutral
  «кто-то» — a raw id never reaches the phrase;
- target_id is NOT always a person (ITEM_CONSUMED carries an object id,
  SUPPLY_ARRIVED an owner id) — resolution is per-type, never blind.
"""

import json
from typing import Any, Dict, Iterable, Optional, Set

from sqlalchemy.orm import Session

from app.db.models import Character

# Payload keys that carry a character id, per event type (verified against
# the log_event call sites). Types not listed here reference characters
# only through actor_id/target_id or nested/list shapes handled below.
_PAYLOAD_CHAR_KEYS = {
    "CHARACTER_MOVED": ("character_id",),
    "SOCIAL_INTERACTION": ("target_id",),
    "RELATIONSHIP_CHANGED": ("target_id",),
    "RELATIONSHIP_FORMED": ("target_id",),
    "CONFLICT": ("target_id",),
    "ELECTION": ("previous_leader_id",),
    "ROLE_ACQUIRED": ("character_id",),
    "ROLE_REVOKED": ("character_id",),
    "PARTNERSHIP_DECLARED": ("initiator_id", "partner_id"),
    "MESSAGE_SENT": ("to",),
    "MARKET_SOLD": ("seller",),
    "FINE_PAID": ("officer",),
    "ARRESTED": ("officer",),
    "RELEASED": ("officer",),
}

# Event types whose target_id column IS a character (ITEM_CONSUMED's
# target_id is an object id; SUPPLY_ARRIVED's is an owner that may be an
# organization — resolving it through the Character table is safe: org ids
# are integers, character ids are "npc_*"/"plr_*" prefixed strings).
_TARGET_IS_PERSON = {
    "SOCIAL_INTERACTION", "RELATIONSHIP_CHANGED", "RELATIONSHIP_FORMED",
    "CONFLICT", "PARTNERSHIP_DECLARED", "SUPPLY_ARRIVED",
}


def _parse_payload(raw: Optional[str]) -> Dict[str, Any]:
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sid(payload: Dict[str, Any], key: str) -> Optional[str]:
    """Payload value only when it is a non-empty str, else None — a
    malformed id/scalar (dict/list/number) must not win an `or` against
    the column id nor reach the phrase as a Python repr."""
    v = payload.get(key)
    return v if isinstance(v, str) and v else None


def _referenced_ids(event_type: str, payload: Dict[str, Any]) -> Set[str]:
    """Character ids referenced by the payload (per-type keys; nested
    from_owner/to_owner for ITEM_TRANSFERRED, lists for pair-shaped
    payloads)."""
    ids: Set[str] = set()

    def take(key: str) -> None:
        v = payload.get(key)
        if isinstance(v, str) and v:
            ids.add(v)

    def take_list(key: str) -> None:
        v = payload.get(key)
        if isinstance(v, list):
            ids.update(x for x in v if isinstance(x, str) and x)

    if event_type == "ITEM_TRANSFERRED":
        for side in ("from_owner", "to_owner"):
            v = payload.get(side)
            if isinstance(v, dict) and isinstance(v.get("character"), str):
                ids.add(v["character"])
    elif event_type == "RECONCILIATION":
        take_list("pair")
        take("mediator_id")
    elif event_type == "CRIME_REPORTED":
        take("suspect")
        take_list("witnesses")
    elif event_type == "MEMORY_CONSOLIDATED":
        take_list("character_ids")
    elif event_type == "ORG_FEAST":
        take_list("boosted_members")
    else:
        for key in _PAYLOAD_CHAR_KEYS.get(event_type, ()):
            take(key)
    return ids


# Bound parameters per name query — a page whose payload lists reference
# more ids than this is chunked to stay under the DB (SQLite) limit.
_NAME_CHUNK = 400


def _load_names(session: Session, ids: Set[str]) -> Dict[str, Any]:
    """ONE logical batched lookup for the whole page — no N+1. Id sets too
    large for one query are chunked (sorted for determinism) and unioned;
    still one logical batch, never per-event queries."""
    if not ids:
        return {}
    names: Dict[str, Any] = {}
    ordered = sorted(ids)
    for start in range(0, len(ordered), _NAME_CHUNK):
        rows = (
            session.query(
                Character.id, Character.first_name, Character.last_name,
                Character.sex,
            )
            .filter(Character.id.in_(ordered[start:start + _NAME_CHUNK]))
            .all()
        )
        for r in rows:
            names[r.id] = r
    return names


# ---------- phrase helpers ----------

def _name(names: Dict[str, Any], cid: Optional[str]) -> Optional[str]:
    # Non-str cid (malformed payload, e.g. a list) is unresolvable — a dict
    # lookup on it would raise TypeError (unhashable) and demote the whole
    # row to the raw code; the phrase must only lose its clause.
    row = names.get(cid) if isinstance(cid, str) and cid else None
    if row is None:
        return None
    return f"{row.first_name} {row.last_name}".strip() or None


def _who(names: Dict[str, Any], cid: Optional[str]) -> str:
    return _name(names, cid) or "кто-то"


def _is_f(names: Dict[str, Any], cid: Optional[str]) -> bool:
    row = names.get(cid) if isinstance(cid, str) and cid else None
    sex = (getattr(row, "sex", "") or "").strip().lower()
    return sex == "f"


def _past(names: Dict[str, Any], cid: Optional[str], m: str, f: str) -> str:
    """Gendered past-tense verb form for the subject's sex ("M"/"F" in the
    generator/player factory; anything else reads masculine)."""
    return f if _is_f(names, cid) else m


def _qty(value: Any) -> str:
    """« ×N» suffix, omitted for 1/absent — «ведро ×2», plain «ведро»."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f" ×{n}" if n != 1 else ""


def _num(value: Any) -> Optional[str]:
    try:
        return str(int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural agreement: 1 монета / 2 монеты / 5 монет."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _money(amount: str) -> str:
    """«N монет» with plural agreement («1 монета», «2 монеты»)."""
    return f"{amount} {_plural(int(amount), 'монета', 'монеты', 'монет')}"


def _org(payload: Dict[str, Any]) -> str:
    n = _num(payload.get("organization_id"))
    return f"организация №{n}" if n else "организация"


def _items(items: Any) -> str:
    """type→qty map («food_bread ×2, water_bottle ×1»); tolerates junk."""
    if not isinstance(items, dict):
        return ""
    parts = []
    for k, v in items.items():
        q = _num(v)
        parts.append(f"{k} ×{q}" if q else str(k))
    return ", ".join(parts)


# ---------- templates (event_type string -> phrase) ----------

def _t_character_created(ev, p, names) -> str:
    name = _sid(p, "name") or _who(names, ev.actor_id)
    parts = [f"Новый житель: {name}"]
    age = _num(p.get("age"))
    if age:
        parts.append(f"{age} л.")
    job = _sid(p, "job")
    if job:
        parts.append(str(job))
    text = ", ".join(parts)
    if p.get("type") == "player":
        text += " (игрок)"
    return text


def _t_character_moved(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "переехал", "переехала")
    return f"{_who(names, ev.actor_id)} {verb} в другое место"


def _t_character_died(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "умер", "умерла")
    cause = p.get("cause")
    return f"{_who(names, ev.actor_id)} {verb}" + (f": {cause}" if cause else "")


def _t_item_transferred(ev, p, names) -> str:
    obj = p.get("object_type") or "предмет"
    sides = []
    for side in ("from_owner", "to_owner"):
        v = p.get(side)
        if isinstance(v, dict) and isinstance(v.get("character"), str) \
                and v["character"]:
            sides.append(_who(names, v["character"]))
        elif isinstance(v, dict) and v.get("organization") is not None:
            n = _num(v.get("organization"))
            sides.append(f"организация №{n}" if n else "организация")
        else:
            sides.append(None)
    text = f"Передача: {obj}{_qty(p.get('quantity'))}"
    known = [s for s in sides if s]
    if known:
        text += f" ({' → '.join(known)})"
    return text


def _t_item_consumed(ev, p, names) -> str:
    obj = p.get("object_type") or "что-то"
    return f"{_who(names, ev.actor_id)} ест: {obj}{_qty(p.get('quantity'))}"


def _t_salary_paid(ev, p, names) -> str:
    amount = _num(p.get("amount"))
    verb = _past(names, ev.actor_id, "получил", "получила")
    text = f"{_who(names, ev.actor_id)} {verb} зарплату"
    if amount:
        text += f": {_money(amount)}"
    return text


def _t_supply_arrived(ev, p, names) -> str:
    key = p.get("resource_key") or "ресурс"
    text = f"Поставка: {key}{_qty(p.get('amount'))}"
    owner = _name(names, ev.target_id)
    if owner:
        text += f" (получатель: {owner})"
    return text


def _t_purchase(ev, p, names) -> str:
    price = _num(p.get("price"))
    verb = _past(names, ev.actor_id, "купил", "купила")
    if price:
        return f"{_who(names, ev.actor_id)} {verb} вещь за {_money(price)}"
    return f"{_who(names, ev.actor_id)} что-то {verb}"


def _t_task_failed(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "не справился", "не справилась")
    task = p.get("task_type")
    text = f"{_who(names, ev.actor_id)} {verb}"
    if task:
        text += f" с делом ({task})"
    reason = p.get("reason")
    if reason:
        text += f": {reason}"
    return text


def _t_task_completed(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "справился", "справилась")
    task = p.get("task_type")
    text = f"{_who(names, ev.actor_id)} {verb} с делом"
    if task:
        text += f" ({task})"
    return text


def _t_constructed(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "построил", "построила")
    obj = p.get("object_type") or "сооружение"
    return f"{_who(names, ev.actor_id)} {verb}: {obj}"


def _t_social_interaction(ev, p, names) -> str:
    a = _who(names, ev.actor_id)
    b = _who(names, _sid(p, "target_id") or ev.target_id)
    text = f"{a} и {b} пообщались"
    if dur := _num(p.get("duration")):
        text += f" ({dur} мин)"
    return text


def _t_relationship_changed(ev, p, names) -> str:
    a = _who(names, ev.actor_id)
    b = _who(names, ev.target_id)
    band = p.get("band")
    before, after = _num(p.get("affection_before")), _num(p.get("affection_after"))
    text = f"{a} и {b}: отношение теперь «{band}»" if band \
        else f"{a} и {b}: отношения изменились"
    if before and after:
        text += f" ({before} → {after})"
    return text


def _t_relationship_formed(ev, p, names) -> str:
    a = _who(names, ev.actor_id)
    b = _who(names, ev.target_id)
    return f"{a} и {b} стали ближе"


def _t_conflict(ev, p, names) -> str:
    a = _who(names, ev.actor_id)
    b = _who(names, ev.target_id)
    return f"Ссора: {a} и {b}"


def _t_election(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "возглавил", "возглавила")
    n = _num(p.get("organization_id"))
    org = f"организацию №{n}" if n else "организацию"
    text = f"{_who(names, ev.actor_id)} {verb} {org}"
    score = _num(p.get("score"))
    if score:
        text += f" (голосов: {score})"
    return text


def _t_org_dues(ev, p, names) -> str:
    text = f"Взносы ({_org(p)})"
    total = _num(p.get("total_collected"))
    if total:
        text += f": собрано {_money(total)}"
    parts = []
    paid, skipped = _num(p.get("paid_members")), _num(p.get("skipped_members"))
    if paid is not None:
        parts.append(f"уплатили: {paid}")
    if skipped is not None:
        parts.append(f"пропустили: {skipped}")
    if parts:
        text += f" ({', '.join(parts)})"
    return text


def _t_org_feast(ev, p, names) -> str:
    text = f"Пир ({_org(p)}"
    cost = _num(p.get("cost"))
    if cost:
        text += f", {_money(cost)}"
    text += ")"
    if ev.actor_id:
        verb = _past(names, ev.actor_id, "организовал", "организовала")
        text += f" — {verb} {_who(names, ev.actor_id)}"
    return text


def _t_law_enacted(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "принял", "приняла")
    law = p.get("law_key")
    text = f"{_who(names, ev.actor_id)} {verb} закон"
    if law:
        text += f" «{law}»"
    return text + f" ({_org(p)})"


def _t_law_violation(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "нарушил", "нарушила")
    law = p.get("law_key")
    text = f"{_who(names, ev.actor_id)} {verb} закон"
    if law:
        text += f" «{law}»"
    fine = _num(p.get("fine"))
    if fine:
        text += f" — штраф {_money(fine)}"
        if not p.get("fine_paid"):
            text += " (не уплачен)"
    return text


def _t_reconciliation(ev, p, names) -> str:
    pair = p.get("pair")
    a, b = ("?", "?")
    if isinstance(pair, list) and len(pair) == 2:
        # _who only for str ids — a malformed (non-str) entry keeps the "?"
        # placeholder; a Python repr must never reach the phrase.
        a = _who(names, pair[0]) if isinstance(pair[0], str) and pair[0] else "?"
        b = _who(names, pair[1]) if isinstance(pair[1], str) and pair[1] else "?"
    mediator = _who(names, _sid(p, "mediator_id") or ev.actor_id)
    return f"Примирение: {a} и {b} (посредник — {mediator})"


def _t_role(ev, p, names, acquired: bool) -> str:
    cid = _sid(p, "character_id") or ev.actor_id
    who = _name(names, cid) or _sid(p, "character_name") or "кто-то"
    title = _sid(p, "title") or _sid(p, "role")
    if acquired:
        verb = _past(names, cid, "получил", "получила")
        return f"{who} {verb} роль: {title}" if title \
            else f"{who} {verb} новую роль"
    verb = _past(names, cid, "лишился", "лишилась")
    return f"{who} {verb} роли: {title}" if title else f"{who} {verb} роли"


def _t_partnership(ev, p, names) -> str:
    a = _sid(p, "initiator_name") or _who(names, _sid(p, "initiator_id") or ev.actor_id)
    b = _sid(p, "partner_name") or _who(names, _sid(p, "partner_id") or ev.target_id)
    return f"{a} и {b} объявили пару"


def _t_message_sent(ev, p, names) -> str:
    a = _who(names, ev.actor_id)
    b = _who(names, p.get("to"))
    text = f"Сообщение: {a} → {b}"
    return text + (" (автоответ)" if p.get("auto") else "")


def _t_ai_decision(ev, p, names) -> str:
    who = _who(names, ev.actor_id)
    decision = p.get("decision")
    text = f"{who}: решение «{decision}»" if decision \
        else f"{who} {_past(names, ev.actor_id, 'принял', 'приняла')} решение"
    reason = p.get("reason")
    if reason:
        text += f" — {reason}"
    if p.get("applied") is False:
        text += " (не применено)"
    return text


def _t_control_changed(ev, p, names) -> str:
    frm, to = _sid(p, "from"), _sid(p, "to")
    if frm and to:
        return f"{_who(names, ev.actor_id)}: режим управления {frm} → {to}"
    return f"{_who(names, ev.actor_id)}: изменён режим управления"


def _t_goal_queued(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "поставил", "поставила")
    goal = p.get("goal_type")
    text = f"{_who(names, ev.actor_id)} {verb} цель"
    if goal:
        text += f": {goal}"
    return text


def _t_visual_asset(ev, p, names) -> str:
    kind = p.get("asset_type")
    if ev.actor_id:
        verb = _past(names, ev.actor_id, "создал", "создала")
        text = f"{_who(names, ev.actor_id)} {verb} визуальный ассет"
        if kind:
            text += f" ({kind})"
        return text
    return "Новый визуальный ассет" + (f" ({kind})" if kind else "")


def _t_memory_created(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "запомнил", "запомнила")
    mtype = p.get("memory_type")
    text = f"{_who(names, ev.actor_id)} {verb} событие"
    if mtype:
        text += f" ({mtype})"
    return text


def _t_memory_consolidated(ev, p, names) -> str:
    groups, removed = _num(p.get("groups")), _num(p.get("removed"))
    text = "Обобщение памяти"
    if groups is not None:
        text += f": +{groups} {_plural(int(groups), 'группа', 'группы', 'групп')}"
    if removed is not None:
        text += f", −{removed}"
    return text


def _t_travel_departed(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "уехал", "уехала")
    purpose = p.get("purpose")
    text = f"{_who(names, ev.actor_id)} {verb} из поселения"
    if purpose:
        text += f" ({purpose})"
    return text


def _t_travel_returned(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "вернулся", "вернулась")
    text = f"{_who(names, ev.actor_id)} {verb} из поездки"
    if p.get("purpose"):
        text += f" ({p.get('purpose')})"
    carried = _items(p.get("items"))
    if carried:
        text += f", {_past(names, ev.actor_id, 'привёз', 'привезла')}: {carried}"
    if p.get("healed"):
        text += f", {_past(names, ev.actor_id, 'подлечился', 'подлечилась')}"
    return text


def _t_weather(ev, p, names) -> str:
    parts = []
    temp = p.get("temperature")
    try:
        parts.append(f"{int(round(float(temp)))}°C")
    except (TypeError, ValueError, OverflowError):
        pass
    wind = p.get("wind")
    try:
        parts.append(f"ветер {int(round(float(wind)))}")
    except (TypeError, ValueError, OverflowError):
        pass
    if p.get("precipitation"):
        parts.append("осадки")
    text = "Погода изменилась"
    return f"{text}: {', '.join(parts)}" if parts else text


def _t_object_burning(ev, p, names) -> str:
    return f"Пожар: горит {p.get('object_type') or 'имущество'}"


def _t_object_burned(ev, p, names) -> str:
    return f"Сгорело: {p.get('object_type') or 'имущество'}"


def _t_object_destroyed(ev, p, names) -> str:
    return f"Разрушено: {p.get('object_type') or 'имущество'}"


def _t_market_listed(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "выставил", "выставила")
    price = _num(p.get("price"))
    text = f"{_who(names, ev.actor_id)} {verb} вещь на продажу"
    if price:
        text += f" за {_money(price)}"
    return text


def _t_market_sold(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "купил", "купила")
    text = f"{_who(names, ev.actor_id)} {verb} вещь"
    price = _num(p.get("price"))
    if price:
        text += f" за {_money(price)}"
    seller = _name(names, p.get("seller"))
    if seller:
        text += f", продавец — {seller}"
    return text


def _t_power_outage(ev, p, names) -> str:
    house = _num(p.get("location_id"))
    text = "Отключение света"
    if house:
        text += f" (дом №{house})"
    demand, supply = _num(p.get("demand")), _num(p.get("supply"))
    if demand is not None or supply is not None:
        text += f": спрос {demand or '—'}, подача {supply or '—'}"
    if p.get("storm"):
        text += ", гроза"
    return text


def _t_crime_commited(ev, p, names) -> str:
    who = _who(names, ev.actor_id)
    if p.get("type") == "theft":
        verb = _past(names, ev.actor_id, "украл", "украла")
        obj = p.get("object_type") or "чужую вещь"
        return f"{who} {verb} {obj}"
    verb = _past(names, ev.actor_id, "испортил", "испортила")
    return f"{who} {verb} чужое имущество"


def _t_crime_reported(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "сообщил", "сообщила")
    text = f"{_who(names, ev.actor_id)} {verb} о преступлении"
    if p.get("crime_type"):
        text += f" ({p.get('crime_type')})"
    suspect = _name(names, p.get("suspect"))
    if suspect:
        text += f", подозреваемый — {suspect}"
    return text


def _t_fine_paid(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "уплатил", "уплатила")
    amount = _num(p.get("amount"))
    text = f"{_who(names, ev.actor_id)} {verb} штраф"
    if amount:
        text += f": {_money(amount)}"
    return text


def _t_arrested(ev, p, names) -> str:
    who = _who(names, ev.actor_id)
    word = "арестована" if _is_f(names, ev.actor_id) else "арестован"
    text = f"{who} {word}"
    release = _num(p.get("release_day"))
    if release:
        text += f" (до дня {release})"
    return text


def _t_released(ev, p, names) -> str:
    verb = _past(names, ev.actor_id, "освобождён", "освобождена")
    return f"{_who(names, ev.actor_id)} {verb}"


TEMPLATES = {
    "CHARACTER_CREATED": _t_character_created,
    "CHARACTER_MOVED": _t_character_moved,
    "CHARACTER_DIED": _t_character_died,
    "ITEM_TRANSFERRED": _t_item_transferred,
    "ITEM_CONSUMED": _t_item_consumed,
    "SALARY_PAID": _t_salary_paid,
    "SUPPLY_ARRIVED": _t_supply_arrived,
    "PURCHASE": _t_purchase,
    "TASK_FAILED": _t_task_failed,
    "TASK_COMPLETED": _t_task_completed,
    "CONSTRUCTED": _t_constructed,
    "SOCIAL_INTERACTION": _t_social_interaction,
    "RELATIONSHIP_CHANGED": _t_relationship_changed,
    "RELATIONSHIP_FORMED": _t_relationship_formed,
    "CONFLICT": _t_conflict,
    "ELECTION": _t_election,
    "ORG_DUES": _t_org_dues,
    "ORG_FEAST": _t_org_feast,
    "LAW_ENACTED": _t_law_enacted,
    "LAW_VIOLATION": _t_law_violation,
    "RECONCILIATION": _t_reconciliation,
    "ROLE_ACQUIRED": lambda ev, p, n: _t_role(ev, p, n, acquired=True),
    "ROLE_REVOKED": lambda ev, p, n: _t_role(ev, p, n, acquired=False),
    "PARTNERSHIP_DECLARED": _t_partnership,
    "MESSAGE_SENT": _t_message_sent,
    "AI_DECISION": _t_ai_decision,
    "CONTROL_CHANGED": _t_control_changed,
    "GOAL_QUEUED": _t_goal_queued,
    "VISUAL_ASSET_CREATED": _t_visual_asset,
    "MEMORY_CREATED": _t_memory_created,
    "MEMORY_CONSOLIDATED": _t_memory_consolidated,
    "TRAVEL_EXTERNAL_DEPARTED": _t_travel_departed,
    "TRAVEL_EXTERNAL_RETURNED": _t_travel_returned,
    "WEATHER_CHANGED": _t_weather,
    "OBJECT_BURNING": _t_object_burning,
    "OBJECT_BURNED": _t_object_burned,
    "OBJECT_DESTROYED": _t_object_destroyed,
    "MARKET_LISTED": _t_market_listed,
    "MARKET_SOLD": _t_market_sold,
    "POWER_OUTAGE": _t_power_outage,
    "CRIME_COMMITED": _t_crime_commited,
    "CRIME_REPORTED": _t_crime_reported,
    "FINE_PAID": _t_fine_paid,
    "ARRESTED": _t_arrested,
    "RELEASED": _t_released,
}


def render_event_texts(session: Session, events: Iterable) -> Dict[int, str]:
    """#291: WorldEvent.id -> human-readable Russian text, rendered at
    display time from stored fields (old rows get texts retroactively).
    One logical batched lookup, chunked at 400 ids per query; unknown
    types fall back to the raw code; a template failure falls back to the
    raw code — never raises."""
    events = list(events)
    parsed = [(e, _parse_payload(e.payload)) for e in events]
    ids: Set[str] = set()
    for e, p in parsed:
        if e.actor_id:
            ids.add(e.actor_id)
        if e.event_type in _TARGET_IS_PERSON and e.target_id:
            ids.add(e.target_id)
        ids |= _referenced_ids(e.event_type, p)
    names = _load_names(session, ids)
    texts: Dict[int, str] = {}
    for e, p in parsed:
        template = TEMPLATES.get(e.event_type)
        if template is None:
            texts[e.id] = e.event_type  # unknown type -> raw code
            continue
        try:
            texts[e.id] = template(e, p, names)
        except Exception:
            texts[e.id] = e.event_type  # malformed historical row -> raw code
    return texts
