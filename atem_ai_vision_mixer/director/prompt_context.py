"""Explicit anonymous observation fields shared by both model prompt builders."""

from atem_ai_vision_mixer.world_state import WorldState

# Only fixed perception codes may cross the model boundary, never arbitrary text.
SAFE_DEFECTS = frozenset(
    {
        "black",
        "frozen",
        "soft",
        "blown",
        "crushed",
        "subject_missing",
        "headroom_tight",
        "headroom_excess",
        "off_thirds",
        "subject_too_small",
        "subject_too_large",
        "edge_cut",
        "cut_at_knee",
        "cut_at_elbow",
    }
)


def world_state_summary(state: WorldState) -> dict:
    """Return validated scalars and fixed defect codes without private input text."""
    observation = {
        "timestamp": state.timestamp,
        "program": {
            "live_camera": state.program.live_camera,
            "seconds_live": state.program.seconds_live,
        },
        "audio": {
            "speaking": state.audio.speaking,
            "pause_ms": state.audio.pause_ms,
            "level": state.audio.level,
        },
        "cameras": {
            camera_id: {
                "feed_healthy": camera.feed_healthy,
                "framing_score": camera.framing_score,
                "subject_present": camera.subject_present,
                "motion_score": camera.motion_score,
                "defects": [code for code in camera.defects if code in SAFE_DEFECTS],
            }
            for camera_id, camera in state.cameras.items()
        },
    }
    return WorldState.model_validate(observation).model_dump(mode="json")
