# Bundled Silero VAD

- Upstream: [Silero VAD v6.2.3](https://github.com/snakers4/silero-vad/releases/tag/v6.2.3).
- Release commit: `5cd7945676eb32225748052e2e6a0580e4686a08`.
- Model: [silero_vad.onnx at that commit](https://raw.githubusercontent.com/snakers4/silero-vad/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx).
- Size: 2,327,524 bytes.
- SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- License: MIT; the unmodified upstream notice is in `LICENSE` beside the model.

This is a model asset, not a recording. It is shipped inside the package and its
checksum is verified before loading. There is no runtime download or fallback to
an unpinned model. No upstream Python package is installed.

The NumPy adapter follows the [pinned upstream ONNX wrapper contract](https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/utils_vad.py):
32 ms blocks (512 samples at 16 kHz, 256 at 8 kHz), preceding context of 64/32
samples, float32 recurrent state of shape `(2, 1, 128)`, and an int64 sample rate.
Inference uses ONNX Runtime's CPU provider with one intra/inter-op thread.

To update the model, pin a reviewed upstream release, replace the model and license
if needed, update this record and the loader checksum together, and re-run offline
inference, stream-state, and installed-package tests. Never fetch a moving branch
as a runtime dependency.
