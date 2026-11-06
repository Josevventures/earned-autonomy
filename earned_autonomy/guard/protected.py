"""Protected categories: targets an action must never touch, even after the user approved the action.

A human approves "trash these 12 messages"; one of them is from a bank. The approval was for the batch, not for
that sender. The guard runs at execution time, per target, and turns the action into its configured fallback
(``trash`` becomes ``archive``) for anything in a protected category, naming the reason. Fails closed: a target
it cannot parse is protected.

Categories are config. A category matches by exact address, by domain (a subdomain matches its parent), or by
domain suffix such as ``.gov``. The bundled example uses invented, reserved names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class Category:
    name: str
    addresses: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardConfig:
    categories: tuple[Category, ...]
    fallbacks: dict[str, str] = field(default_factory=dict)    # guarded action -> what to do instead

    @classmethod
    def from_dict(cls, data: dict) -> "GuardConfig":
        cats = tuple(Category(name=c["name"],
                              addresses=frozenset(a.lower() for a in c.get("addresses", [])),
                              domains=frozenset(d.lower().lstrip(".") for d in c.get("domains", [])),
                              suffixes=tuple(s.lower() for s in c.get("suffixes", [])))
                     for c in data.get("categories", []))
        return cls(categories=cats, fallbacks=dict(data.get("fallbacks", {})))

    @classmethod
    def from_file(cls, path: str | Path) -> "GuardConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _parents(domain: str) -> set[str]:
    parts = domain.split(".")
    return {".".join(parts[i:]) for i in range(len(parts) - 1)}


def protected(target: str, config: GuardConfig) -> str | None:
    """The reason the target is protected, or None if the action may proceed."""
    m = _ADDRESS.search((target or "").strip().lower())
    if not m:
        return "unparseable target"
    address = m.group(0)
    domain = address.rsplit("@", 1)[1]
    for cat in config.categories:
        if address in cat.addresses:
            return cat.name
        if _parents(domain) & cat.domains:
            return cat.name
        if any(domain.endswith(s) for s in cat.suffixes):
            return cat.name
    return None


@dataclass(frozen=True)
class GuardedAction:
    action: str                 # what will actually run
    reason: str | None = None   # why it changed, when it did


def guard(action: str, target: str, config: GuardConfig) -> GuardedAction:
    """Apply the guard to one approved action on one target. Actions the config does not guard pass through."""
    if action not in config.fallbacks:
        return GuardedAction(action)
    reason = protected(target, config)
    if reason is None:
        return GuardedAction(action)
    return GuardedAction(config.fallbacks[action], f"protected ({reason}): {action} became {config.fallbacks[action]}")
