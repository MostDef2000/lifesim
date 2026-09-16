import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import bootstrap
from app.economy import get_balance, mint, open_account, transfer


@pytest.fixture
def db_engine(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    return engine

@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()

def test_mint_and_balance(db_session, default_settings):
    world_id = default_settings.world.world_id
    # Open account for character
    acc = open_account(db_session, world_id, "character", "char_1")

    # Mint money
    mint(db_session, world_id, 0, acc.id, 1000, "initial_mint")

    assert get_balance(db_session, acc.id) == 1000

def test_transfer_happy_path(db_session, default_settings):
    world_id = default_settings.world.world_id
    acc_from = open_account(db_session, world_id, "character", "char_1")
    acc_to = open_account(db_session, world_id, "character", "char_2")

    mint(db_session, world_id, 0, acc_from.id, 1000, "mint")

    tx = transfer(db_session, world_id, 10, acc_from.id, acc_to.id, 400, "payment")

    assert get_balance(db_session, acc_from.id) == 600
    assert get_balance(db_session, acc_to.id) == 400
    assert tx.balance_from_after == 600
    assert tx.balance_to_after == 400

def test_transfer_reject_amount_le_zero(db_session, default_settings):
    world_id = default_settings.world.world_id
    acc_from = open_account(db_session, world_id, "character", "char_1")
    acc_to = open_account(db_session, world_id, "character", "char_2")

    with pytest.raises(ValueError, match="Transfer amount must be positive"):
        transfer(db_session, world_id, 10, acc_from.id, acc_to.id, 0, "zero")

    with pytest.raises(ValueError, match="Transfer amount must be positive"):
        transfer(db_session, world_id, 10, acc_from.id, acc_to.id, -100, "negative")

def test_transfer_insufficient_funds(db_session, default_settings):
    world_id = default_settings.world.world_id
    acc_from = open_account(db_session, world_id, "character", "char_1")
    acc_to = open_account(db_session, world_id, "character", "char_2")

    mint(db_session, world_id, 0, acc_from.id, 100, "mint")

    with pytest.raises(ValueError, match="Insufficient funds"):
        transfer(db_session, world_id, 10, acc_from.id, acc_to.id, 200, "too_much")

def test_conservation(db_session, default_settings):
    world_id = default_settings.world.world_id
    accs = []
    for i in range(3):
        accs.append(open_account(db_session, world_id, "character", f"char_{i}").id)

    # Sequence: Mint 1000 to A, transfer 300 A->B, transfer 100 B->C, transfer 50 A->C
    mint(db_session, world_id, 0, accs[0], 1000, "m1")
    transfer(db_session, world_id, 1, accs[0], accs[1], 300, "t1")
    transfer(db_session, world_id, 2, accs[1], accs[2], 100, "t2")
    transfer(db_session, world_id, 3, accs[0], accs[2], 50, "t3")

    total_balance = sum(get_balance(db_session, aid) for aid in accs)
    assert total_balance == 1000
