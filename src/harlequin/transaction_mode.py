from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class HarlequinTransactionMode:
    """
    A container for a database connection transaction mode. A mode must
    have a label, and may also define a zero-arg callable that commits
    or rolls back a transaction (if these callables are defined, Harlequin
    will present buttons for committing and rolling back transactions).

    Args:
        label (str): A short label for this mode, to be shown in the UI
            as f"Tx: {label}"
        commit (Callable[[], None] | None): A callable to commit an open
            transaction.
        rollback (Callable[[], None] | None): A callable to roll back an
            open transaction.
    """

    label: str
    commit: Callable[[], None] | None = None
    rollback: Callable[[], None] | None = None
