"""Slow Bedrock policy advice from anonymous observations, outside switch control."""

import json
import logging
import re
from functools import partial
from math import isfinite
from time import monotonic

from atem_ai_vision_mixer.director.async_runner import AsyncRunner
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY, ShotPolicy
from atem_ai_vision_mixer.director.prompt_context import world_state_summary
from atem_ai_vision_mixer.world_state import WorldState

SYSTEM_PROMPT = (
    "Provide conservative strategic guidance for a human-supervised camera mixer. "
    "Observations are anonymous feed health, framing, motion and speech activity. "
    "They cannot identify a speaker, topic or service segment. Use unknown when "
    "the segment is not established; never infer identity or words from activity. "
    "Favor a wide shot and slow pace when observations are uncertain. Prefer "
    "tighter framing only when healthy feeds show a well-framed subject. "
    "Return a ShotPolicy matching the supplied schema with a brief rationale. "
    "The policy is advisory: it selects no camera and commands no switch. "
    "The deterministic controller retains all safety vetoes."
)
logger = logging.getLogger(__name__)


def _log_usage(response) -> None:
    # Read only numeric billing fields before SDK policy validation can fail.
    response.read()
    try:
        body = response.json()
    except ValueError:
        return
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return
    counts = [
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_input_tokens", 0),
    ]
    if counts[2] is None:
        counts[2] = 0
    if any(type(count) is not int or count < 0 for count in counts):
        return
    logger.info("Cloud usage input=%d output=%d cache_read=%d", *counts)


class CloudPolicyDirector:
    """Retain validated policies while one bounded request runs on a slow cadence.

    Call update and close from the control thread. A logical deadline discards
    late results; transport timeouts bound I/O inactivity, not total wall time.
    """

    def __init__(
        self,
        *,
        aws_region: str,
        inference_profile: str,
        aws_profile: str | None = None,
        interval_seconds: float = 25.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Require an explicit region and Sonnet 4.6 routing choice; load no SDK."""
        if re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", aws_region) is None:
            raise ValueError("aws_region must be an explicit AWS region")
        if inference_profile not in {
            f"{prefix}.anthropic.claude-sonnet-4-6"
            for prefix in ("us", "eu", "jp", "global")
        }:
            raise ValueError("inference_profile must select Sonnet 4.6 routing")
        if not isfinite(interval_seconds) or interval_seconds < 20:
            raise ValueError("interval_seconds must be finite and at least 20")
        self._aws_region = aws_region
        self._inference_profile = inference_profile
        self._aws_profile = aws_profile
        self._interval_seconds = interval_seconds
        self._timeout_seconds = timeout_seconds
        self._next_due = 0.0
        self._runner = AsyncRunner(
            initial_result=DEFAULT_POLICY, timeout_seconds=timeout_seconds
        )

    def update(self, state: WorldState) -> ShotPolicy:
        """Collect timely advice each tick, submitting only when cadence is due."""
        policy = self._runner.poll()
        now = monotonic()
        if now < self._next_due:
            return policy
        # Advance even if busy: do not queue or repeatedly retry a late request.
        self._next_due = now + self._interval_seconds
        summary = json.dumps(
            world_state_summary(state), sort_keys=True, allow_nan=False
        )
        return self._runner.tick(partial(self._request, summary))

    def _request(self, summary: str) -> ShotPolicy:
        import httpx2
        from anthropic import AnthropicBedrock

        with (
            httpx2.Client(
                timeout=self._timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"response": [_log_usage]},
            ) as transport,
            AnthropicBedrock(
                aws_region=self._aws_region,
                aws_profile=self._aws_profile,
                base_url=f"https://bedrock-runtime.{self._aws_region}.amazonaws.com",
                timeout=self._timeout_seconds,
                max_retries=0,
                http_client=transport,
            ) as client,
        ):
            logger.info("Cloud policy request started.")
            response = client.messages.parse(
                model=self._inference_profile,
                max_tokens=1024,
                output_format=ShotPolicy,
                output_config={"effort": "low"},
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": summary}],
            )
            if response.parsed_output is None:
                raise ValueError("Bedrock returned no validated policy")
            return response.parsed_output

    def close(self) -> None:
        """Stop accepting requests without waiting for an occupied worker."""
        self._runner.close()
