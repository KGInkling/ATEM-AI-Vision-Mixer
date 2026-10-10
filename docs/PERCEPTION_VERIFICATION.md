# Perception verification

The final regression sweep extends the existing aggregator test owner. It adds
an independently authored four-camera golden sequence, recorded-file round trip,
cross-requirement properties, forbidden-import checks, and an opt-in full-pipeline
timing checkpoint. The only production change reuses a read-only health
thumbnail when the tile's bytes and dimensions have the same SHA-256. Changed
frames still take the full inspection path; core contracts and CI are unchanged.

## Requirements and named proof

Each row names a regression test in the existing suite; these run locally and
in their applicable CI environment. Model/capture dependencies are optional in
core CI and installed in the macOS integration job.

| Requirement | Test owner and named proof |
|---|---|
| R1 Capture seam/timebase | `test_capture_source.py::test_captured_frame_keeps_video_audio_and_timestamp_together`; `test_audio_metadata_preserves_native_payload_and_describes_its_interval` |
| R2 Recorded decoding/pacing/EOF | `test_file_source.py::test_file_source_reads_a_real_recorded_file`; `test_realtime_file_source_paces_against_recorded_pts`; `test_file_source_stops_cleanly_when_file_has_no_decodable_frames` |
| R3 4-up/7-up composition/audio | `test_make_multiview.py::test_layout_timing_and_selected_audio_round_trip` (both layouts) |
| R4 Configured/discrete tiles | `test_tiles.py::test_extract_tiles_uses_only_configured_rectangles`; `test_discrete_mode_preserves_camera_frames_without_cropping` |
| R5 Feed health/static distinction | `test_health.py::test_black_feed_is_unhealthy_with_an_explanation`; `test_freeze_requires_three_consecutive_identical_frames_and_recovers`; `test_raw_noise_discarded_by_downscaling_does_not_look_frozen` |
| R6 Bounded motion | `test_motion.py::test_motion_is_normalized_without_unsigned_subtraction_wrap`; `test_first_frame_has_no_observed_motion` |
| R7 Stateful local VAD/hysteresis | `test_audio_vad.py::test_hysteresis_requires_three_decisions_in_both_directions`; `test_inference_preserves_recurrent_state_and_context`; `test_packaged_model_runs_offline_and_replays_deterministically` |
| R8 Pause grades | `test_audio_vad.py::test_pause_grade_boundaries` |
| R9 People/selected pose | `test_people.py::test_face_runs_on_all_tiles_with_independent_video_histories`; `test_pose_runs_only_on_live_and_challenger_and_releases_old_selection` |
| R10 Framing/movement | `test_framing.py::test_headroom_and_thirds_are_normalized_by_subject_height`; `test_bottom_cut_near_a_reliable_joint_reports_its_name`; `test_wander_distinguishes_traversing_from_pacing` |
| R11 Valid aggregate state | `test_aggregator.py::test_tick_returns_valid_world_state_with_caller_program_and_frame_time`; `test_recorded_multiview_pipeline_matches_committed_world_state_jsonl` |
| R12 Local steady timing | `test_motion.py::test_four_camera_health_and_motion_under_five_ms`; `test_aggregator.py::test_four_camera_real_pipeline_steady_p95_under_one_hundred_ms` (opt-in) |
| R13 Boundaries/resources | `test_aggregator.py::test_capture_and_perception_have_no_switcher_or_llm_client_imports`; `test_audio_vad.py::test_model_provenance_and_license_are_packaged_without_heavy_dependencies` |

## Golden and property checks

The [golden fixture record](../tests/golden/README.md) declares the expected scene
and audio timeline. Twenty JSONL rows describe speech, pause, framing, black/frozen
feeds, and static live cameras. The file contains only existing WorldState fields;
raw media and private geometry stay local.

`test_four_camera_golden_snapshots_assert_all_six_perception_properties` asserts:

1. Every snapshot passes WorldState validation.
2. Every unhealthy camera has a black/frozen explanation.
3. Static live cameras with differing bytes remain healthy and never frozen.
4. Speaking has zero pause; continuous silence has nondecreasing observed pause.
5. Framing and motion scores remain in `[0, 1]`.
6. Replaying the same inputs produces the identical sequence.

The recorded-file companion writes temporary JSONL after real PyAV decode,
compares the same baseline, and replays again. Deterministic people and speech
predictions make this a pipeline regression baseline, not an ML-accuracy test.
Separate existing tests exercise real ONNX; the real MediaPipe/PyAV runtime
results are recorded in [pipeline verification](PIPELINE.md).

Audio gaps and recording resets start new histories. Pause monotonicity applies
within a continuous audio history; missing audio cannot be treated as measured
silence. Partial-block and near-boundary tests prevent future samples or padding
from manufacturing a cut opportunity.

## Timing checkpoint

The tests follow the existing local-performance marker and are skipped by default:

```bash
ATEM_RUN_PERFORMANCE=1 \
ATEM_FACE_MODEL_PATH=/absolute/path/to/face.tflite \
ATEM_POSE_MODEL_PATH=/absolute/path/to/pose.task \
.venv/bin/python -B -m pytest -p no:cacheprovider \
  tests/test_aggregator.py::test_four_camera_real_pipeline_steady_p95_under_one_hundred_ms \
  tests/test_motion.py::test_four_camera_health_and_motion_under_five_ms -q -s
```

Download the documented local model assets beforehand using [people setup](PEOPLE.md).
The test never downloads them. Missing optional models/dependencies are reported as
skipped, not a passing timing result. On macOS the installed MediaPipe runtime
needs host graphics initialization even for CPU tasks; run outside a restricted
sandbox. All input pixels and PCM in this benchmark are generated locally.

The full-pipeline checkpoint measures 50 steady ticks after ten startup ticks.
It checks p95 under 100 ms and reports cold maximum separately. The health/motion
checkpoint checks 100 steady ticks after ten startup ticks against 5 ms p95.
Frame construction and recorded-file decoding are outside these measurements.
The benchmark uses an empty synthetic scene; positive-pose runtime evidence is
separate. No claim is made that startup, newly loaded pose models, every individual
tick, or a complete live capture path meets 100 ms.

The initial health/motion measurements were 5.107 ms and 5.308 ms p95, above the
unchanged 5 ms target. Profiling showed SHA-256 hashing and resize dominated the
cost. Reusing the already-computed thumbnail for byte-identical repeats brought
the same existing checkpoint to 3.273 ms p95. The exact golden states and health
regressions remained unchanged. This improves repeated-frame work; it does not
claim a general changed-frame timing bound from that repeated-frame benchmark.

Final local checks passed 306 tests with the two opt-in timing tests skipped in
the default suite. Package coverage was 99.85%; health, framing, and all other
production perception modules were 100%. Running the real-model timing test
explicitly after the optimization measured 39.452 ms steady p95 and 313.409 ms
cold maximum. No test threshold, expected golden row, or existing assertion was
weakened to obtain these results.

## Remaining boundaries

Hardware geometry/audio routing, room footage, crowd identity, and framing-weight
tuning remain unverified. Cold startup exceeded 100 ms in prior pipeline proof;
that limitation is retained. Review and deployment evidence are separate from
these local checks. After the final task PR is merged, stop at the perception spec
boundary for the owner's next-step decision.
