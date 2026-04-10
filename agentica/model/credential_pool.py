# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Credential pooling with exhaustion tracking and automatic failover.

Supports multiple API keys per provider with automatic rotation when
keys are rate-limited or quota-exhausted. Strategies: round_robin,
fill_first, least_used.
"""
import time
from dataclasses import dataclass
from typing import List, Optional

from agentica.utils.log import logger


@dataclass
class PooledCredential:
    """A single credential with usage and exhaustion tracking."""

    api_key: str
    base_url: Optional[str] = None
    label: str = ""
    exhausted_until: float = 0.0  # Unix timestamp; 0 = available
    request_count: int = 0

    @property
    def is_available(self) -> bool:
        """Check if credential is currently available (not in cooldown)."""
        return time.time() >= self.exhausted_until


class CredentialPool:
    """Multi-credential failover with exhaustion tracking.

    When a credential is rate-limited or quota-exhausted, it enters cooldown.
    The pool automatically selects the next available credential based on
    the configured strategy.

    Usage:
        pool = CredentialPool(strategy="round_robin")
        pool.add("sk-key1", label="primary")
        pool.add("sk-key2", label="backup")

        cred = pool.next()
        try:
            result = call_api(cred.api_key)
            pool.mark_success(cred)
        except RateLimitError:
            pool.mark_exhausted(cred, cooldown=3600)
            cred = pool.next()  # automatic failover
    """

    STRATEGIES = ("round_robin", "fill_first", "least_used")

    def __init__(self, strategy: str = "round_robin"):
        """Initialize credential pool.

        Args:
            strategy: Selection strategy.
                - "round_robin": Rotate through credentials evenly.
                - "fill_first": Use first credential until exhausted.
                - "least_used": Pick credential with lowest request count.
        """
        if strategy not in self.STRATEGIES:
            raise ValueError(
                f"Unknown strategy: {strategy}. Choose from {self.STRATEGIES}"
            )
        self.strategy = strategy
        self.credentials: List[PooledCredential] = []
        self._index = 0

    def add(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        label: str = "",
    ) -> None:
        """Register a credential in the pool.

        Args:
            api_key: API key string.
            base_url: Optional base URL override for this credential.
            label: Human-readable label for logging.
        """
        self.credentials.append(
            PooledCredential(
                api_key=api_key,
                base_url=base_url,
                label=label or f"cred_{len(self.credentials)}",
            )
        )

    def next(self) -> Optional[PooledCredential]:
        """Get next available credential based on strategy.

        Returns:
            Next available credential, or least-exhausted if all are
            in cooldown, or None if pool is empty.
        """
        if not self.credentials:
            return None

        available = [c for c in self.credentials if c.is_available]
        if not available:
            # All exhausted; return least-exhausted (soonest to recover)
            available = sorted(
                self.credentials, key=lambda c: c.exhausted_until
            )
            logger.warning("All credentials exhausted; using least-exhausted")
            return available[0]

        if self.strategy == "fill_first":
            return available[0]
        elif self.strategy == "round_robin":
            cred = available[self._index % len(available)]
            self._index += 1
            return cred
        elif self.strategy == "least_used":
            return min(available, key=lambda c: c.request_count)
        return available[0]

    def mark_success(self, cred: PooledCredential) -> None:
        """Record a successful API call for this credential."""
        cred.request_count += 1

    def mark_exhausted(
        self,
        cred: PooledCredential,
        cooldown: float = 3600,
    ) -> None:
        """Mark credential as exhausted with cooldown period.

        Args:
            cred: The credential to mark.
            cooldown: Seconds until the credential becomes available again.
        """
        cred.exhausted_until = time.time() + cooldown
        logger.warning(
            f"Credential '{cred.label}' exhausted for {cooldown}s"
        )

    def __len__(self) -> int:
        return len(self.credentials)

    def __repr__(self) -> str:
        available = sum(1 for c in self.credentials if c.is_available)
        return (
            f"CredentialPool(strategy={self.strategy!r}, "
            f"total={len(self.credentials)}, available={available})"
        )
