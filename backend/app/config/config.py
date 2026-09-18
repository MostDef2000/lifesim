import hashlib
import pathlib
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


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
    SOCIALIZE: Optional[Dict[str, float]] = None

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
    SOCIALIZE: ActionParams = Field(
        default_factory=lambda: ActionParams(
            duration_minutes=30, max_duration_minutes=60,
            base_utility_weight=0.3,
        )
    )
    TRAVEL_EXTERNAL: ActionParams = Field(
        default_factory=lambda: ActionParams(
            duration_minutes=480, max_duration_minutes=960,
            base_utility_weight=0.0,
        )
    )

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

class SocialConfig(BaseModel):
    enabled: bool = False
    interaction_minutes: int = 30
    social_recovery_per_minute: float = 0.5
    social_action_threshold: float = 40.0
    refusal_threshold: float = -60.0
    initial_affection: float = 0.0
    initial_conflicts: int = 2
    hub_location_types: List[str] = ["kitchen", "settlement"]

class LawEntry(BaseModel):
    fine: int
    enact_traits: Dict[str, int]

class LawConfig(BaseModel):
    enabled: bool = True
    catalog: Dict[str, LawEntry] = Field(
        default_factory=lambda: {
            "no_conflict": LawEntry(
                fine=5, enact_traits={"discipline": 1, "sociability": 0, "risk_tolerance": -1}
            ),
            "night_home": LawEntry(
                fine=2, enact_traits={"discipline": 1, "sociability": -1, "risk_tolerance": 1}
            ),
        }
    )

class OrgReconciliationConfig(BaseModel):
    enabled: bool = True
    target_affection: float = -30.0

class OrgConfig(BaseModel):
    enabled: bool = False
    election_interval_days: int = 7
    dues_per_day: int = 1
    feast_interval_days: int = 14
    feast_cost: int = 30
    feast_social_boost: float = 20.0
    reconciliation: OrgReconciliationConfig = Field(default_factory=OrgReconciliationConfig)
    laws: LawConfig = Field(default_factory=LawConfig)

class AiBudgetConfig(BaseModel):
    max_per_day: int = 40
    per_task: Dict[str, int] = Field(
        default_factory=lambda: {
            "decide": 10, "classify": 20, "dialogue": 10, "summarize": 10
        }
    )

class AiMemoryConfig(BaseModel):
    top_k: int = 5
    witnesses: bool = True

class LlmConfig(BaseModel):
    enabled: bool = False
    transport: str = "stub"  # stub | ollama
    min_confidence: float = 0.3
    base_url: str = "http://localhost:11434"
    model_tier1: str = "qwen3:4b"
    model_tier2: str = "qwen3:14b"
    budgets: AiBudgetConfig = Field(default_factory=AiBudgetConfig)
    timeout_sec: int = 30
    retry: int = 1
    memory: AiMemoryConfig = Field(default_factory=AiMemoryConfig)

class ApiConfig(BaseModel):
    """M5 (SPEC §80-81/104): web-api surface. Headless by default (П2)."""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    session_ttl_min: int = 720
    cookie_name: str = "vl1_session"
    ws_interval_s: float = 1.0
    secret_env: str = "VL1_SECRET"  # HMAC secret; dev fallback with warning

class VisualConfig(BaseModel):
    """M6 (SPEC §105/66-70): visual generation. Off by default (П2-аналог)."""
    enabled: bool = False
    transport: str = "stub"  # stub|http
    base_url: str = "http://127.0.0.1:7860"
    model: str = "flux.1-schnell"
    lora: str = ""
    storage_dir: str = "data/visual_assets"
    image_size: str = "512x512"
    timeout_sec: float = 30.0
    default_weather: str = "clear"

class ExternalServiceSpec(BaseModel):
    """M7 (SPEC §38): one service at an external location."""
    service_type: str  # treatment|purchase|visit|registration|transfer
    item_type: Optional[str] = None
    price: int = 0
    heal_amount: float = 0.0
    duration_minutes: int = 30

class ExternalLocationSpec(BaseModel):
    name: str
    ext_type: str
    description: str = ""
    services: List[ExternalServiceSpec] = Field(default_factory=list)

class ExternalConfig(BaseModel):
    """M7 (SPEC §106/38-39): external Vladivostok. Off-island content, no physics."""
    enabled: bool = True
    travel_minutes: int = 480
    travel_cost: int = 50
    npc_utility: bool = False  # MVP: NPCs never choose external travel (П2)
    contact_probability: float = 0.5  # second contact per NPC
    locations: List[ExternalLocationSpec] = Field(default_factory=list)

class AdminConfig(BaseModel):
    """M8 (§85/§27/§107): public alpha operations."""
    registration_enabled: bool = True
    max_players: int = 0  # 0 = unlimited
    rate_limit_enabled: bool = True
    global_rpm: int = 120
    auth_rpm: int = 10
    rate_limit_window_sec: int = 60
    backup_dir: str = "backups"
    backup_keep: int = 7

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
    social: SocialConfig = SocialConfig()
    org: OrgConfig = Field(default_factory=OrgConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)
    external: ExternalConfig = Field(default_factory=ExternalConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)

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
