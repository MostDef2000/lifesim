import hashlib
import pathlib
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel


class WorldConfig(BaseModel):
    world_id: str
    initial_population: int
    start_real_timestamp: str
    config_sha256: str = "unknown"
    source_path: str = ""

class TicksConfig(BaseModel):
    time_scale: float
    persistence_commit_interval_game_minutes: int
    snapshot_interval_game_days: int

class NeedsDecay(BaseModel):
    hunger: float
    thirst: float
    energy: float
    social: float

class NeedsRecovery(BaseModel):
    SLEEP: Dict[str, float]
    EAT: Dict[str, float]
    DRINK: Dict[str, float]

class NeedsConfig(BaseModel):
    decay_rates: NeedsDecay
    recovery_rates: NeedsRecovery
    critical_thresholds: Dict[str, float]
    health_decay_rate: float

class UtilityWeights(BaseModel):
    hunger: float
    thirst: float
    energy: float
    social: float

class UtilityConfig(BaseModel):
    weights: UtilityWeights

class ActionParams(BaseModel):
    duration_minutes: int
    max_duration_minutes: int
    base_utility_weight: float

class ActionsConfig(BaseModel):
    SLEEP: ActionParams
    EAT: ActionParams
    DRINK: ActionParams
    WORK: ActionParams
    MOVE: ActionParams
    BUY_ITEM: ActionParams
    IDLE: ActionParams

class EconomyConfig(BaseModel):
    starting_balance: int
    default_salary_day: int
    shop_price_multiplier: float
    prices: Dict[str, int]
    shop_stock: Dict[str, int]
    kitchen_stock: Dict[str, int]
    water_daily_supply_amount: float
    water_per_drink: float = 0.5
    water_initial_quantity: float
    salaries: Dict[str, int]
    organizations: Dict[str, Dict[str, Any]]

class MovementConfig(BaseModel):
    default_travel_minutes: int
    max_capacity_default: int
    edges: List[Dict[str, Any]]

class PopulationConfig(BaseModel):
    min_age: int
    max_age: int
    first_names: List[str]
    last_names: List[str]
    trait_keys: List[str]
    jobs: List[Dict[str, Any]]
    birthplace: str = "Vladivostok"

class PersistenceConfig(BaseModel):
    db_path: str
    wal_mode: bool
    synchronous: str
    snapshot_dir: str

class InvariantsConfig(BaseModel):
    max_duration_minutes: int
    min_balance: int
    health_range: List[float]
    needs_range: List[float]
    max_death_rate_per_day: float

class GenerationConfig(BaseModel):
    trait_range: List[int]
    initial_items: List[str]

class LocationParams(BaseModel):
    name: str
    type: str
    parent: Optional[str] = None
    capacity: Optional[int] = None

class LocationsConfig(BaseModel):
    locations: Dict[str, LocationParams]
    houses_count: int = 24

class Settings(BaseModel):
    world: WorldConfig
    ticks: TicksConfig
    needs: NeedsConfig
    utility: UtilityConfig
    actions: ActionsConfig
    economy: EconomyConfig
    movement: MovementConfig
    population: PopulationConfig
    persistence: PersistenceConfig
    invariants: InvariantsConfig
    generation: GenerationConfig
    locations: LocationsConfig

def load_config(path: str) -> Settings:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(p, 'rb') as f:
        bytes_data = f.read()
        sha256 = hashlib.sha256(bytes_data).hexdigest()

    data = yaml.safe_load(bytes_data)

    # Attach sha256 and path to the world config
    if 'world' in data:
        data['world']['config_sha256'] = sha256
        data['world']['source_path'] = str(p.absolute())

    return Settings(**data)

def get_config_sha256(path: str) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()
