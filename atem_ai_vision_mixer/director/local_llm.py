"""Local schema-constrained shot advice with private text kept out of prompts."""

import json
import logging
from dataclasses import dataclass
from functools import partial

from atem_ai_vision_mixer.director.async_runner import AsyncRunner
from atem_ai_vision_mixer.director.intent import ShotIntent, shot_intent_schema
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY, ShotPolicy
from atem_ai_vision_mixer.director.prompt_context import world_state_summary
from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.world_state import WorldState

DEFAULT_MODEL = "qwen3:4b"
LOCAL_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 2.0

SYSTEM_PROMPT = (
    "Suggest one connected camera from the supplied observations and policy. "
    "Prefer healthy feeds, a visible subject and good framing. "
    "Policy is advisory; the controller alone decides whether and when to switch. "
    "Return only one JSON object matching this schema, without tools or prose.\n"
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LocalResult:
    intent: ShotIntent | None = None
    unavailable: bool = False


def _remove_authorization(request) -> None:
    # The SDK otherwise forwards OLLAMA_API_KEY even to a loopback server.
    request.headers.pop("authorization", None)


def _request_arguments(state: WorldState, policy: ShotPolicy) -> dict:
    schema = shot_intent_schema(sorted(state.cameras))
    context = {
        "policy": {
            "segment": policy.segment.value,
            "preferred_shot": policy.preferred_shot.value,
            "cut_tempo": policy.cut_tempo.value,
        },
        "world_state": world_state_summary(state),
    }
    return {
        "model": DEFAULT_MODEL,
        "format": schema,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + json.dumps(schema, sort_keys=True),
            },
            {
                "role": "user",
                "content": json.dumps(context, sort_keys=True, allow_nan=False),
            },
        ],
        "options": {"temperature": 0, "num_predict": 128},
        "keep_alive": -1,
        "stream": False,
        # Preserve the small output budget for JSON rather than hidden reasoning.
        "think": False,
    }


class LocalLLMDirector:
    """Suggest shots from a local model while preserving rules and controller safety.

    Use decide, policy replacement and close from the control thread. The caller
    sets decision cadence; layered application scheduling is a later task group.
    """

    def __init__(
        self,
        *,
        policy: ShotPolicy = DEFAULT_POLICY,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Use the default policy and finite deadlines without loading a model SDK."""
        self.policy = policy
        self._timeout_seconds = timeout_seconds
        self._rules = RulesDirector()
        self._runner = AsyncRunner(
            initial_result=_LocalResult(), timeout_seconds=timeout_seconds
        )
        self._reported_unavailable = False

    def decide(self, state: WorldState) -> ShotIntent:
        """Return advice whose camera is still present, or current rules instead."""
        fallback = self._rules.decide(state)
        if not state.cameras:
            return fallback

        arguments = _request_arguments(state, self.policy)
        result = self._runner.tick(partial(self._request, arguments))
        if result.unavailable:
            if not self._reported_unavailable:
                logger.warning("Local Ollama unavailable; using rules.")
            self._reported_unavailable = True
            return fallback

        self._reported_unavailable = False
        if result.intent is None or result.intent.camera not in state.cameras:
            return fallback
        # ShotIntent is mutable; caller edits must not corrupt the retained result.
        return result.intent.model_copy()

    def _request(self, arguments: dict) -> _LocalResult:
        try:
            import httpx
            import ollama
        except ImportError:
            return _LocalResult(unavailable=True)

        try:
            with ollama.Client(
                host=LOCAL_HOST,
                timeout=self._timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"request": [_remove_authorization]},
            ) as client:
                response = client.chat(**arguments)
        except (ConnectionError, httpx.HTTPError, ollama.ResponseError):
            return _LocalResult(unavailable=True)

        intent = ShotIntent.model_validate_json(
            response["message"]["content"], strict=True
        )
        if intent.camera not in arguments["format"]["properties"]["camera"]["enum"]:
            raise ValueError("Model selected a camera outside the request schema")
        return _LocalResult(intent=intent)

    def close(self) -> None:
        """Stop accepting model calls without waiting for an occupied worker."""
        self._runner.close()
