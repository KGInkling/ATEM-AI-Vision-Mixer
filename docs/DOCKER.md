# Docker development environment

Docker provides a repeatable Linux environment for the portable parts of the project. It is
useful for building the Python package and running the same tests on any Docker host.

## Build and test

From the repository root, build the test image:

```bash
docker build --target test --tag atem-ai-vision-mixer:test .
```

The build runs the full test suite and fails if a test or the total coverage gate fails. Run the
same suite again from the completed image with:

```bash
docker run --rm atem-ai-vision-mixer:test
```

The image installs the `core`, `perception`, `llm`, and `dev` dependency groups. It deliberately
does not install the `capture` group.

The perception group installs ONNX Runtime but not the `silero-vad` Python package. Later
perception work will package a pinned Silero ONNX model and load it directly, which keeps the
Docker image free of PyTorch, torchaudio, and CUDA dependencies.

## What can run in Docker

These parts are portable and belong in the image:

- The offline switching core and rule-based director.
- File-based perception, including reading ordinary media files with PyAV.
- The local and cloud LLM director clients.
- The complete automated test suite.

## What must run on the Mac host

Live DeckLink capture and CoreML/Apple Neural Engine inference are host-native only on macOS.
The boundary is physical, not a missing Docker setting:

- The DeckLink Duo 2 is a PCIe card.
- Blackmagic Desktop Video is a macOS system extension.
- Docker Desktop runs Linux containers inside a virtual machine with no PCIe passthrough.
- Docker Desktop 4.35 and newer can pass some USB devices through to Linux. That does not help a
  PCIe DeckLink card.
- CoreML and the Apple Neural Engine are not available inside a Linux container.

The `capture` dependency group supplies the Python-side PyAV dependency for a host installation.
The host also needs a native ffmpeg build configured with `--enable-decklink`; a Python package
cannot install that Blackmagic-enabled binary or its system driver.

Docker makes the decision logic and tests portable. It cannot make the hardware edge portable,
because that edge depends on macOS drivers and a PCIe device attached to the production machine.
