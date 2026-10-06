# PyroWave on this fork

PyroWave is Hans-Kristian Arntzen's intra-only GPU wavelet codec. Every frame is decodable on
its own, encode and decode run as Vulkan compute, and a lost packet costs at most one frame. The
price is bandwidth: a clean picture needs hundreds of Mbps, so it is for wired LANs or a Wi-Fi 7
link in the same room. The host side is Vibepollo 2.0.0 and newer (never auto-selected there).

## What the branch adds
- **Protocol:** `jlobue10/moonlight-common-c` branch `pyrowave` (submodule) carries the negotiation
  from joemossjr16's fork: `VIDEO_FORMAT_PYROWAVE` (0x10000), `_444` (0x20000), `_HDR10`
  (0x40000, unused here), `SCM_PYROWAVE`/`_444` server bits, the `PYROWAVE/90000` SDP marker and
  `bitStreamFormat 3`, and a larger RTP receive buffer (8192 packets) for the packet rates involved.
- **Codec library:** `app/src/main/jni/pyrowave-renderer/pyrowave` is the host's exact vendored
  tree (pyrowave 186f0393 + Vibepollo's three non-bitstream patches, see `VENDOR.txt`), built by
  the `buildPyroWave` Gradle task with the NDK's CMake toolchain into `libpyrowave-shared.so`
  (C API 0.6) for arm64-v8a and x86_64. The bitstream has no version field, so host and client
  must stay on the same commit; the host advertises it as `a=x-ss-pyrowave.bitstream:186f0393`.
- **Renderer:** `pyrowave_renderer.cpp` (from joemossjr16/artemis-android-pyrowave) loads
  libvulkan at runtime, probes for a Vulkan 1.3 device with the needed features, decodes with
  the codec's compute path and presents through a swapchain created on the stream Surface.
- **Java:** `PyroWaveDecoderRenderer` plus a branch in `MediaCodecDecoderRenderer.setup()`:
  when the negotiated format is PyroWave the MediaCodec path is bypassed and frames are decoded
  and presented synchronously on the submitting thread. Direct submit is disabled while PyroWave
  is offered so the receive thread only drains the socket. Bitrate follows resolution × fps ×
  a bits-per-pixel target (default 1.6 bpp, cap optional) instead of the slider.
- **Settings (Video):** "Use PyroWave on fast local networks", "PyroWave 4:4:4", "PyroWave
  quality" (bpp) and "PyroWave bitrate cap". SDR only; the main bitrate slider now reaches 800 Mbps.

## XR stereo path
In the XR and flat-stereo modes the decoder's render target is the `Surface` of the
`Stereo3DRenderer`'s `SurfaceTexture` (handed over by `onStereo3DSurfaceReady`). The PyroWave
renderer creates its Vulkan swapchain on that same Surface, so the GL stereo/depth pipeline and
the SurfaceEntity path are untouched; the cost is one extra copy through the SurfaceTexture.
Untested on the Galaxy XR until a build runs there.

## Testing
1. Host: Vibepollo 2.0.0+, wired Ethernet on the host side; optionally run its bandwidth probe.
2. Client: Settings → Video → enable PyroWave; start at 720p60 with the default 1.6 bpp
   (≈ 90 Mbps) and raise the quality until the link drops frames. 4:4:4 costs ~1.6×.
3. The stream log reports `Using PyroWave Vulkan renderer for WxH` and the stats overlay names
   the decoder "PyroWave (Vulkan)". A Galaxy XR field report (Themaister/pyrowave #17) measured
   3–4 ms decode per 1984x896 4:4:4 frame on the stock driver (Vulkan 1.3.295).

## Provenance and licences
Codec and Granite: MIT; vulkan-headers: Apache-2.0; renderer and Java glue: GPL-3.0 (same as
this app), from joemossjr16/artemis-android-pyrowave; protocol commits: joemossjr16/moonlight-common-c.
