# Stereo performance and correctness follow-up

Baseline: `7bb0a5944533a9e436c4c992918cf955d17f4b6b` (`moonlight-noir`, fork.31).

Integration: this PR is stacked on Android #54 at `ed8877d6`, which also pins
the compatibility fix from common-C #3. It preserves #54's demand-driven draws,
new-video-only readback, late-depth redraws, and upload-on-map-change behavior.
S03's timing arithmetic overlaps #54; this PR adds atomic session-owned counters,
conditional diagnostic formatting and explicit regression cases. Merge common-C
#3, then Android #54, then this PR (#55); do not cherry-pick competing versions of
the draw loop independently.

The earlier four-repository audit covered Android PRs #1-50, Vibepollo PRs
#1-4, and the direct library commits consumed by their dependency pins.
Android #51-53, common-C #1-2, libvirtualgamepad #1-2 and Vibepollo #5 are
now merged. This follow-up preserves those fixes and addresses three additional
defects in the shared stereo path. These are inherited defects exercised by
the new depth-model/XR features, not three newly attributed Claude regressions.

## Findings

### S01 - Cached depth can stay stale during gradual changes (P2)

`onDrawFrame` compared a sampled image with the previous display sample and
updated that reference even if the inference queue rejected the image. The
worker used that difference to decide whether to reuse the last inferred depth
map. A sequence of individually small changes could therefore diverge from the
cached image indefinitely without another model invocation. A changed image
sampled while the queue was busy could also suppress inference once accepted.

The worker now compares each accepted image with its last successfully inferred
image. It advances that reference only after successful inference. The sampling
dimensions belong to the session rather than a replacement renderer. Identical
images still reuse cached depth. Comparison and reference copying move off the
GL thread and run only on accepted input; the reference copy happens only after
a model invocation, rather than after every successful GL readback.

The deterministic production-worker test sends red-channel values 0 through
200, increasing by one per frame. Before the fix: one model invocation, final
depth value 0. After: 67 invocations, final depth 198, within the existing
two-unit threshold. A further 100 identical images invoke no extra inference;
a changed accepted image whose producer-side difference is zero is refreshed.
All owned input/output buffers return on shutdown.

### S02 - Shader objects survive program teardown (P2)

`createProgram` never called `glDeleteShader` for successfully compiled shader
objects. Deleting a program detaches its shaders; without a deletion request,
those objects remain alive in a retained GL context. A fragment compile failure
also leaked the successfully compiled vertex shader.

Both shaders now receive deletion requests in `finally`. An unsuccessful or
exceptional link deletes the program too. GL defers deletion of attached shaders
until their program releases them, preserving the linked executable.

Actual setup methods with a GL boundary fake show 200 outstanding shader
objects after 100 create/delete cycles before the fix, and zero afterward.
Fragment compile failure, program allocation failure, link failure and an
exception during linking all preserve cleanup. These are object-lifetime
checks, not measured device GPU memory savings.

Reference: [Khronos program deletion semantics](https://wikis.khronos.org/opengl/GLAPI/glDeleteProgram).

### S03 - Stereo overlay timing is miscalculated (P2)

The render loop added time since the start of the reporting window once for
every frame, averaged against the prior window's FPS, then produced seconds
for a field labeled milliseconds. It also omitted the reporting frame from
the render count and treated a potentially long reporting interval as exactly
one second. The shared inference counter could lose increments when the GL
thread reset it, or receive contributions from a retired session.

The overlay now averages each draw's actual CPU wall duration in milliseconds,
counts the reporting frame, and normalizes both rates by the measured interval.
Completed depth work uses a session-owned atomic counter. Queue diagnostics
are formatted only when debug mode is enabled. This duration includes CPU waits
inside the draw call and is not GPU execution time or end-to-end latency.

For 51 simulated 2 ms draws spanning 1.002 seconds, the old displayed delay was
0.4267 ms and its rates were 50/51. The fixed delay is 2.0 ms and both rates are
50.8982 per second.

## Validation

- All 12 scripts in `tools/regression/test_*.py` pass after integration; the native renderer check
  retains ASan/UBSan. The two new scripts reproduce failures on the baseline.
- The complete renderer and XR GL host compile with Android 15 framework
  classes, LiteRT 1.4 Java APIs and OpenCV 4.12; only preferences are stubbed.
- The existing `StereoSessionTest` runs the actual production worker and
  teardown with native inference/image boundaries mocked. Both delayed-inference
  and delayed-initialization ownership tests pass.
- The PR workflow runs the full unsigned release APK build (all supported ABIs,
  NDK and R8) and Android unit tests. Consult the PR's run for current results.
- No headset, BLE radio, real Vulkan/GL driver, model output quality, thermal,
  power or end-to-end latency measurement was performed here. Small sustained
  changes correctly cause more inference, so device power should be measured;
  an FPS improvement is not claimed.

## Reproduction

Run the committed host scripts with Python 3, JDK 17+, g++ and vendored Vulkan
headers. For a before/after check while these changes are uncommitted, the two
new scripts accept `--baseline` to read the current committed renderer. After
commit, run a copy of the scripts against the baseline checkout instead.

```sh
python3 tools/regression/test_stereo_scene_cache.py
python3 tools/regression/test_stereo_render_resources.py
ASAN_OPTIONS=detect_leaks=0 python3 tools/regression/test_pyrowave_fences.py
```

LeakSanitizer is disabled for the restricted host's process-inspection limit;
address and undefined-behavior sanitizers remain enabled.
