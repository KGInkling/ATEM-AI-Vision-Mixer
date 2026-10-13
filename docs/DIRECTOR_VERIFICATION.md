# Director requirements and safety proof

This is the LLM-directors group 9 requirement map and group 10 coverage checkpoint.
It records offline implementation proof, with live inference quality, permissions,
actual costs and production latency still unverified. Named tests below live in
[integration tests](../tests/test_integration.py), [app tests](../tests/test_app.py)
and [intent/policy tests](../tests/test_intent.py). Responses are stubbed; real
executor futures and controlled clocks exercise completion, expiry and cadence.

## Requirements R1–R13

| Requirement | Named proof | Remaining boundary |
|---|---|---|
| R1: advice interface and rules fallback | `test_layered_defaults_to_rules_without_importing_or_constructing_model_sdks`; `test_layered_model_outages_recover_without_relabeling_rules` | Existing structural `decide(state) -> ShotIntent`; cloud policy is consumed by the layered director |
| R2: different outputs/cadences and startup policy | `test_default_policy_is_conservative_before_any_cloud_response`; `test_layered_local_cadence_collects_between_submissions_and_rechecks_cameras`; `test_layered_cloud_policy_informs_only_the_local_request_that_used_it` | Local defaults to 3 seconds, cloud to 25 seconds; tuning needs a recorded service |
| R3: closed, immutable policy | `test_policy_schema_exposes_required_fields_and_closed_enums`; `test_policy_rejects_unknown_enum_values`; `test_policy_cannot_be_mutated_while_retained` | Schema/validation proven; useful policy content is not |
| R4: constrained output and camera enum | `test_local_request_is_bounded_loopback_schema_constrained_and_validated`; `test_local_malformed_output_retains_the_previous_valid_intent`; `test_cloud_validation_retains_policy_and_still_reports_numeric_usage` | Requests include schema/enum; live grammar enforcement remains unverified |
| R5: async, skip busy, discard late results | `test_model_delay_does_not_block_shadow_ticks_or_queue_requests`; `test_timeout_retains_result_skips_busy_calls_and_discards_late_completion`; `test_layered_slow_local_keeps_control_ticks_nonblocking_without_queue` | Python cannot cancel running threads; I/O timeouts bound inactivity |
| R6: HTTP local process and outage recovery | `test_local_request_is_bounded_loopback_schema_constrained_and_validated`; `test_local_outage_uses_current_rules_logs_once_and_recovers` | Adapter starts/loads no model; real daemon behavior is a live checkpoint |
| R7: small resident model, bounded generation | `test_local_request_is_bounded_loopback_schema_constrained_and_validated` | Asserts `qwen3:4b`, `keep_alive=-1`, 128 output-token cap, disabled hidden thinking; memory fit on the 16 GB Mac is unmeasured |
| R8: explicit Bedrock routing | `test_cloud_requires_explicit_supported_region_profile_and_slow_cadence`; `test_cloud_explicit_aws_profile_and_close`; `test_cli_invalid_cloud_configuration_fails_before_model_calls` | Supported configuration proven; actual model availability and least-privilege permissions need operator verification |
| R9: stable prefix and cache checkpoint | `test_local_prompt_excludes_free_text_and_keeps_the_stable_prefix`; `test_cloud_prefix_is_stable_and_private_fields_are_not_forwarded` | Local camera-set changes change its enum; Sonnet's 1,024-token cache minimum means a compact stable prefix can have zero reads |
| R10: bounded calls, visible usage | `test_cloud_hour_of_ticks_bounds_requests_and_reports_each_attempt_usage`; `test_cli_cloud_usage_is_visible_without_vendor_info_or_jsonl_contamination`; `test_cloud_validation_retains_policy_and_still_reports_numeric_usage` | Simulated hour yields 144 attempts; usage is logged before policy rejection; actual costs unmeasured |
| R11: actual producer attribution | `test_layered_cloud_policy_informs_only_the_local_request_that_used_it`; `test_cli_explicit_model_flags_preserve_shadow_and_pass_explicit_aws_routing` | `director_tier` follows the actual request policy, not proof the controller acted |
| R12: offline/no-credentials operation | `test_layered_defaults_to_rules_without_importing_or_constructing_model_sdks`; `test_layered_enabled_models_missing_sdks_still_run_the_whole_app_on_rules`; `test_local_missing_sdk_remains_offline_with_rules` | No cloud/model call occurs in this proof |
| R13: controller/privacy boundaries | `test_layered_directors_never_call_any_switcher_method`; `test_each_director_configuration_preserves_safety_for_every_scenario_tick`; `test_local_extra_observation_fields_cannot_enter_the_prompt`; `test_cloud_prefix_is_stable_and_private_fields_are_not_forwarded` | Controller/rules/WorldState/ShotIntent unchanged; credentials and cleanup also need scope inspection |

## Six correctness properties

| Property | Named proof and limit |
|---|---|
| 1: no switcher access | `test_layered_directors_never_call_any_switcher_method` guards every public fake-switcher method in four enable combinations; directors receive no switcher reference |
| 2: no blocking model tick | `test_model_delay_does_not_block_shadow_ticks_or_queue_requests`; `test_layered_slow_local_keeps_control_ticks_nonblocking_without_queue`; measured with bounded stubs |
| 3: rules without model services or credentials | `test_layered_enabled_models_missing_sdks_still_run_the_whole_app_on_rules` plus outage/recovery cases |
| 4: model targets connected | The 12 cases in `test_each_director_configuration_preserves_safety_for_every_scenario_tick` assert membership each tick; `test_local_drops_a_retained_intent_when_its_camera_disappears` rejects disconnected advice; baseline exception below |
| 5: invalid output retains prior advice | `test_local_malformed_output_retains_the_previous_valid_intent`; `test_cloud_validation_retains_policy_and_still_reports_numeric_usage` |
| 6: cadence-bounded cloud calls | `test_cloud_hour_of_ticks_bounds_requests_and_reports_each_attempt_usage` advances 3,600 one-second ticks: 144 attempts plus 144 numeric usage entries, with SDK retries disabled |

Property 4's literal wording does not hold for every rules fallback. With no
healthy feed, existing rules hold the live input, which may be absent from the
observation map. This baseline is preserved rather than inventing a camera or
changing `ShotIntent`, rules or the controller.
`test_rules_hold_with_no_usable_camera_remains_fail_closed` covers empty
observations and an absent live input with unhealthy feeds; the controller holds
with zero writes. Model-generated selections still require connected-camera
validation. The audit records this exception instead of claiming unrestricted
camera membership.

The scenario matrix runs rules, local and cloud-informed advice through the same
controller across sermon, failed-feed, operator-takeover and never-pausing
timelines. Model stubs challenge the live shot and deliberately select the broken
feed in the failure scenario. Existing per-tick assertions check healthy cut
targets, dwell, cooldown, speech gating, rate limits and shadow zero writes. The
continuous-speech case must stage without cutting.

## Cost-log capture

A fresh CLI previously suppressed INFO cloud attempt/usage records. Selecting
`--enable-cloud` now configures the normal stderr sink and enables the cloud
adapter's INFO records. The default root/SDK level stays WARNING; existing
caller-configured handlers are preserved. JSONL decisions stay on stdout.
Library callers configure their own logging sinks and levels.

For a separately authorized rehearsal, retain both streams:

```bash
.venv/bin/python -B -m atem_ai_vision_mixer.app --scenario sermon \
  --enable-local --enable-cloud --aws-region us-east-1 \
  --inference-profile us.anthropic.claude-sonnet-4-6 \
  > decisions.jsonl 2> usage.log
```

The scenario replay is fast, without wall-clock pacing, and may finish before
model advice arrives. Attempt counts give a conservative invocation bound;
returned usage enables later cost calculation. This does not establish billed
cost or model quality. No live request was performed for this checkpoint.

## Coverage checkpoint

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -B -m coverage run --source=atem_ai_vision_mixer \
  -m pytest -p no:cacheprovider
.venv/bin/python -B -m coverage report
```

Implementation proof stores coverage outside the checkout, then removes it after
retaining the measured summary. Group 10 requires package coverage at least 85%
and director coverage at least 90%; measured results are in the task PR. Existing
opt-in perception timing skips do not hide director tests. Comparative recorded
evaluation (group 11) and real model/shadow verification (group 12) remain separate.
