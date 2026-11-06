"""earned-autonomy: per-action-type autonomy for an AI agent, earned from logged human decisions.

Three pieces, kept separate on purpose:

- ``policy``: the tiers, the graduation and demotion rules (config, with 90% / 15 / 2 defaults), and the
  function that replays a ledger into the current status of every action type.
- ``ledger``: an append-only JSONL decision log. Status is never stored; it is derived from the log.
- ``guard``: the execution side. Protected categories, permission lanes for dispatched agent jobs, one-shot
  permission grants, timed grants and a git push block.
"""
from .policy import Policy, Tier, Status, TypeState, evaluate, may_act_autonomously
from .ledger import Ledger, LedgerError

__all__ = ["Policy", "Tier", "Status", "TypeState", "evaluate", "may_act_autonomously", "Ledger", "LedgerError"]
__version__ = "0.1.0"
