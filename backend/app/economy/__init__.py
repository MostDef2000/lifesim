from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Account, Transaction


def open_account(session: Session, world_id: str, owner_type: str, owner_id: str) -> Account:
    """
    Opens an account for a character, organization, or world.
    Ensures UNIQUE(owner_type, owner_id) is honored.
    """
    account = session.query(Account).filter_by(owner_type=owner_type, owner_id=owner_id).first()
    if account:
        return account

    # Generate a unique ID for the account
    # In a real system this would be a UUID or a sequence.
    # Here we use a predictable format for M1.
    account_id = f"acc_{owner_type}_{owner_id}"

    account = Account(
        id=account_id,
        world_id=world_id,
        owner_type=owner_type,
        owner_id=owner_id,
        balance=0,
        created_at=0 # Default to 0, updated by bootstrap/simulation
    )
    session.add(account)
    session.flush()
    return account

def get_balance(session: Session, account_id: str) -> int:
    """Returns the current balance of an account."""
    account = session.query(Account).filter_by(id=account_id).one()
    return account.balance

def transfer(
    session: Session,
    world_id: str,
    game_timestamp: int,
    from_account_id: Optional[str],
    to_account_id: str,
    amount: int,
    reason: str,
    event_id: Optional[int] = None
) -> Transaction:
    """
    Transfers funds from one account to another.
    - from_account_id=None means minting (from world).
    - amount <= 0 is rejected.
    - Insufficient funds are rejected.
    - Atomically updates balances and writes Transaction.
    """
    if amount <= 0:
        raise ValueError("Transfer amount must be positive")

    # Update and verify 'from' account
    balance_from_after = None
    if from_account_id:
        from_acc = session.query(Account).filter_by(id=from_account_id).one()
        if from_acc.balance < amount:
            raise ValueError("Insufficient funds")
        from_acc.balance -= amount
        balance_from_after = from_acc.balance

    # Update 'to' account
    to_acc = session.query(Account).filter_by(id=to_account_id).one()
    to_acc.balance += amount
    balance_to_after = to_acc.balance

    tx = Transaction(
        world_id=world_id,
        game_timestamp=game_timestamp,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=amount,
        reason=reason,
        event_id=event_id,
        balance_from_after=balance_from_after,
        balance_to_after=balance_to_after
    )
    session.add(tx)
    session.flush()
    return tx

def mint(
    session: Session,
    world_id: str,
    game_timestamp: int,
    to_account_id: str,
    amount: int,
    reason: str,
    event_id: Optional[int] = None
) -> Transaction:
    """Mints money to an account (from_account_id is NULL)."""
    return transfer(
        session, world_id, game_timestamp, None, to_account_id, amount, reason, event_id
    )

def burn(
    session: Session,
    world_id: str,
    game_timestamp: int,
    from_account_id: str,
    amount: int,
    reason: str,
    event_id: Optional[int] = None
) -> Transaction:
    """Burns money from an account (to_account_id is NULL)."""
    # Support symmetric signature as requested, though not used in M1
    if amount <= 0:
        raise ValueError("Burn amount must be positive")

    from_acc = session.query(Account).filter_by(id=from_account_id).one()
    if from_acc.balance < amount:
        raise ValueError("Insufficient funds")

    from_acc.balance -= amount
    balance_from_after = from_acc.balance

    tx = Transaction(
        world_id=world_id,
        game_timestamp=game_timestamp,
        from_account_id=from_account_id,
        to_account_id=None,
        amount=amount,
        reason=reason,
        event_id=event_id,
        balance_from_after=balance_from_after,
        balance_to_after=None
    )
    session.add(tx)
    session.flush()
    return tx
