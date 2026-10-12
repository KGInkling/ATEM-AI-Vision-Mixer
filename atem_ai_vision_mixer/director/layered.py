"""Compose model advice behind the same camera-suggestion interface as rules."""

from math import isfinite
from time import monotonic

from atem_ai_vision_mixer.director.cloud_llm import CloudPolicyDirector
from atem_ai_vision_mixer.director.intent import ShotIntent
from atem_ai_vision_mixer.director.local_llm import LocalLLMDirector
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY
from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.world_state import WorldState


class LayeredDirector:
    """Return advice and its actual tier while keeping models explicitly opt-in.

    Call decide and close from the control thread. Cloud controls its own slower
    cadence; local requests default to three seconds with polling on every tick.
    """

    def __init__(
        self,
        *,
        local_enabled: bool = False,
        cloud_enabled: bool = False,
        local_interval_seconds: float = 3.0,
        aws_region: str | None = None,
        inference_profile: str | None = None,
        aws_profile: str | None = None,
    ) -> None:
        """Construct only enabled adapters, without loading SDKs or calling models."""
        if not isfinite(local_interval_seconds) or local_interval_seconds <= 0:
            raise ValueError("local_interval_seconds must be positive and finite")
        if cloud_enabled and (not aws_region or not inference_profile):
            raise ValueError(
                "cloud requires an explicit AWS region and inference profile"
            )
        self._cloud = (
            CloudPolicyDirector(
                aws_region=aws_region,
                inference_profile=inference_profile,
                aws_profile=aws_profile,
            )
            if cloud_enabled
            else None
        )
        self._local = LocalLLMDirector() if local_enabled else None
        self._rules = RulesDirector()
        self._local_interval_seconds = local_interval_seconds
        self._next_local_due = 0.0
        self._closed = False
        self.tier = "rules"

    def decide(self, state: WorldState) -> ShotIntent:
        """Poll model results, apply request cadence and report actual provenance."""
        if self._closed:
            raise RuntimeError("LayeredDirector is closed")
        self.tier = "rules"
        policy = (
            self._cloud.update(state) if self._cloud is not None else DEFAULT_POLICY
        )
        if self._local is None:
            return self._rules.decide(state)

        self._local.policy = policy
        now = monotonic()
        request_due = now >= self._next_local_due
        if request_due:
            self._next_local_due = now + self._local_interval_seconds
        intent = self._local.decide(state, request_due=request_due)
        policy_used = self._local.policy_used
        if policy_used is not None:
            self.tier = "local" if policy_used is DEFAULT_POLICY else "cloud_informed"
        return intent

    def close(self) -> None:
        """Stop both adapters without waiting for their occupied workers."""
        if self._closed:
            return
        self._closed = True
        if self._local is not None:
            self._local.close()
        if self._cloud is not None:
            self._cloud.close()
