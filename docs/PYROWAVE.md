# PyroWave on this fork

PyroWave is Hans-Kristian Arntzen's intra-only GPU wavelet codec. Every frame is decodable on
its own, encode and decode run as Vulkan compute, and a lost packet costs detail in one frame,
not the frame (see "Packet loss"). The price is bandwidth: a clean picture needs hundreds of Mbps, so it is for wired LANs or a Wi-Fi 7
link in the same room. The host side is Vibepollo 2.0.0 and newer (never auto-selected there).

## What the branch adds
- **Protocol:** `jlobue10/moonlight-common-c` branch `pyrowave` (submodule) carries the negotiation
  from joemossjr16's fork, extended to Vibepollo's four profiles: `VIDEO_FORMAT_PYROWAVE` (0x10000),
  `_444` (0x20000), `_HDR10` (0x40000, 10-bit 4:2:0) and `_HDR10_444` (0x80000, 10-bit 4:4:4), the
  matching `SCM_PYROWAVE*` server bits (0x00800000 .. 0x04000000), the `PYROWAVE/90000` SDP marker
  and `bitStreamFormat 3`, and a larger RTP receive buffer (8192 packets) for the packet rates
  involved. The 10-bit profiles are part of `VIDEO_FORMAT_MASK_10BIT`, so the stock SDP code emits
  `dynamicRangeMode=1` for them; the host then streams HDR10 (BT.2020 PQ) when its display is HDR
  and 10-bit SDR otherwise, and says which through the usual HDR mode control message.
  Since common-c `8880a83` the branch also merges Nonary's `mac-pyrowave` data plane: a PyroWave
  frame with loss that parity could not repair is delivered anyway, one buffer per RTP packet
  with zero-filled `BUFFER_TYPE_LOST` stand-ins for the missing ones, packets that start a record
  are tagged `BUFFER_TYPE_RECORD_START`, `DECODE_UNIT.pyrowaveCriticalPackets` carries the host's
  count of leading packets holding the sequence header and coarsest wavelet level, and the RTP
  queue releases such a frame after a short silence (1-3 ms, shorter once the client's on-time
  deadline has passed) instead of waiting for the next frame. The strict bitstream negotiation
  gate (`pyroWaveFallbackBitrate`) is kept. The new `STREAM_CONFIGURATION` fields
  `pyrowaveCompression`, `pyrowaveLinkMbps` and `pyrowavePaceMbps` stay at zero: Vibepollo
  retired the compressed detail groups and rejects a client that asks for them, and the link and
  pace hints are a later step. `LiSetVideoReassemblyDeadlineCallback` and the PCM haptics callback
  are left unset.
- **Codec library:** `app/src/main/jni/pyrowave-renderer/pyrowave` is the host's exact vendored
  tree (pyrowave 186f0393 + Vibepollo's three non-bitstream patches, see `VENDOR.txt`), built by
  the `buildPyroWave` Gradle task with the NDK's CMake toolchain into `libpyrowave-shared.so`
  (C API 0.6) for arm64-v8a and x86_64. The bitstream has no version field, so host and client
  must stay on the same commit; the host advertises it as `a=x-ss-pyrowave.bitstream:186f0393`.
- **Renderer:** `pyrowave_renderer.cpp` (from joemossjr16/artemis-android-pyrowave) loads
  libvulkan at runtime, probes for a Vulkan 1.3 device with the needed features, decodes with
  the codec's compute path (fragment path on Adreno) and presents through a swapchain created
  on the stream Surface.
- **Frame framing:** Vibepollo does not use Pyrollo's `PYRW` container. It sends either
  *record framing* (the PyroWave sequence header followed by block records and in-band padding
  records, chosen when the client announces `x-ss-video[0].pyrowaveFeatures=1`, which this fork
  does so the frame's coarsest level gets FEC parity) or *length-prefixed framing* (little-endian
  packet count and lengths) for clients that announce nothing. The renderer detects the container
  per frame and accepts all three; fork.15 only knew `PYRW` and dropped every frame ("Dropping
  frame without a valid PYRW header" in logcat, audio but a black picture). The decoder is cleared
  before every frame, as the host's protocol document prescribes, so a run of lost frames cannot
  trip PyroWave's 3-bit sequence counter. Only record framing survives packet loss (see "Packet
  loss"); the other two containers carry their packet lengths in-band and are dropped whole when
  a packet is missing. See Vibepollo's `docs/pyrowave-protocol.md`.
- **Java:** `PyroWaveDecoderRenderer` plus a branch in `MediaCodecDecoderRenderer.setup()`:
  when the negotiated format is PyroWave the MediaCodec path is bypassed and frames are decoded
  and presented synchronously on the submitting thread. Direct submit is disabled while PyroWave
  is offered so the receive thread only drains the socket. Bitrate follows resolution × fps ×
  a bits-per-pixel target (default 1.6 bpp, cap optional) instead of the slider.
- **Settings (Video):** "Use PyroWave on fast local networks", "PyroWave 4:4:4", "PyroWave
  quality" (bpp) and "PyroWave bitrate cap". With "Enable HDR" on, the 10-bit profiles are offered
  too (see "HDR10" below). The main bitrate slider now reaches 800 Mbps.

## Packet loss
With common-c `8880a83` a PyroWave frame is no longer dropped when parity cannot repair it:

- **Delivery:** `BridgeDrSubmitDecodeUnit` (callbacks.c) does not split a PyroWave decode unit by
  buffer type. It concatenates every buffer in order, lost stand-ins included so byte offsets stay
  packet-aligned, and calls `MoonBridge.bridgeDrSubmitPyroWaveFrame(data, length, packets,
  packetCount, criticalPackets, ...)` with a reusable `int[]` of two values per packet: the payload
  length and flags (`PYROWAVE_PACKET_LOST` 0x1, `PYROWAVE_PACKET_RECORD_START` 0x2). The old
  `submitDecodeUnit` branch stays as the legacy single-buffer path.
- **Walk:** `PyroWaveDecoderRenderer.submitFrame(data, length, packets, packetCount,
  criticalPackets)` reaches `Renderer::parseRecordFrame` in `pyrowave_renderer.cpp`, a port of
  moonlight-qt's `PyroWaveFraming::parse`. It walks the record headers, strips padding records,
  drops any record that lost a byte with its packet and, when a record header itself was lost,
  resumes at the next received packet the host flagged as starting with a record (hosts without
  flags resync at the next received packet once the finer levels' ordinary records are reached).
  Every record that reaches the decoder is validated: a block shorter than its header or running
  past the frame, a sequence header for another size or chroma mode, an out-of-range block index
  or, for an intact frame, a block count that differs from the announced one rejects the frame.
  The surviving records are pushed in one piece, so an intact frame feeds the decoder exactly the
  bytes the strict parser did.
- **Decision:** a frame that lost its sequence header or any packet of the critical prefix (or,
  when the host announced no count, any packet before the first finer-level block) cannot be
  decoded and returns `SUBMIT_SKIPPED_LOSS`; Java reports it as dropped, not as a renderer error,
  so it never counts toward the 30-error rebuild. A frame that lost only detail is decoded with
  `pyrowave_decoder_decode_is_ready_with_sideband(allow_partial = true, 0 pristine bands)` and
  presented with blur where the loss was; intact frames keep the readiness check they had.
- **Visibility:** the stats overlay adds "PyroWave packet loss: N frames decoded partially, M
  dropped" when either is non-zero in the last two windows, the crash diagnostic string carries
  the session totals ("PyroWave frames with packet loss: ..."), and logcat gets `Frame lost X of
  Y packets, decoded without them (N lossy frames so far)` for the first lossy frame and then one
  in 300. `BridgeDrSetup` logs the host's advertised bitstream revision once.
- **Harnesses:** `tools/regression/test_pyrowave_framing.py` feeds synthetic Vibepollo record
  frames through the walk; `test_jni_frame_buffer.py` covers the per-packet delivery.

## HDR10
PyroWave's bitstream carries no bit depth or colour metadata (the sequence header has the fields,
but neither Vibepollo nor this client sets or reads them); everything comes from negotiation:

- **Offer:** with "Enable HDR" on, `Game` adds `VIDEO_FORMAT_PYROWAVE_HDR10` (and `_HDR10_444` with
  the 4:4:4 setting) whenever PyroWave is offered at all, even if the display reports no HDR10
  mode, because this renderer can tone-map. moonlight-common-c picks the best mutual profile
  (10-bit 4:4:4, 10-bit, 4:4:4, 8-bit). `NvConnection` budgets 1.25x the bits per pixel for 10-bit.
- **Planes:** a 10-bit profile decodes into `R16_UNORM` planes (PyroWave writes any UNORM
  format); the host wrote normalized 10-bit code values, limited range (this client always asks
  for limited range), so the shader expands with 64/876 (luma) and 512/896 (chroma) on 1023.
- **HDR or 10-bit SDR:** the host's HDR mode message (`setHdrMode`) switches the renderer between
  BT.2020 PQ and the BT.709 matrix at runtime; MaxCLL (or the mastering peak) from the metadata
  feeds the tone mapper. The decoder bypasses MediaCodec, so the HDR metadata never reaches
  MediaFormat; it is only read for that peak.
- **Output:** when the 10-bit stream starts, the renderer asks for `VK_EXT_swapchain_colorspace`
  and prefers a swapchain in `VK_COLOR_SPACE_HDR10_ST2084_EXT` (`A2B10G10R10`/`A2R10G10B10`/`RGBA16F`).
  Then PQ passes straight through and SDR frames (10-bit SDR, or an HDR session the host switched
  to SDR) are placed at 203 nits in PQ. Surfaces without an HDR10 format (the XR stereo path's
  8-bit `SurfaceTexture`, SDR displays) get an sRGB swapchain and the shader tone-maps: PQ EOTF,
  normalise to 203-nit SDR white, extended Reinhard on the brightest channel up to the content
  peak, BT.2020 to BT.709 primaries, sRGB OETF. `planar_csc.frag` takes all of this as push
  constants (`CscParams`); `adb shell setprop debug.pyrowave.hdr_output sdr|hdr` forces the output
  path for testing. Log lines: `Swapchain format N, colour space HDR10 ST2084 (HDR10 output)` or
  `Surface offers no HDR10 swapchain; HDR will be tone-mapped to SDR`, and `HDR mode on: BT.2020 PQ
  (peak N nits, swapchain ...)` when the host switches.

## XR stereo path
In the XR and flat-stereo modes the decoder's render target is the `Surface` of the
`Stereo3DRenderer`'s `SurfaceTexture` (handed over by `onStereo3DSurfaceReady`). The PyroWave
renderer creates its Vulkan swapchain on that same Surface, so the GL stereo/depth pipeline and
the SurfaceEntity path are untouched; the cost is one extra copy through the SurfaceTexture.
On the Galaxy XR (fork.15, 2026-10-07) the Vulkan device, swapchain (MAILBOX) and fragment decode
path all came up on this Surface; the picture stayed black only because of the framing mismatch
above. This Surface is an 8-bit GL texture, so HDR10 streams in the stereo modes are always
tone-mapped (see "HDR10"); only the flat path can present PQ.

## Testing
1. Host: Vibepollo 2.0.0+, wired Ethernet on the host side; optionally run its bandwidth probe.
2. Client: Settings → Video → enable PyroWave; start at 720p60 with the default 1.6 bpp
   (≈ 90 Mbps) and raise the quality until the link drops frames. 4:4:4 costs ~1.6×, 10-bit 1.25×.
4. HDR: enable HDR in the client with the host display in HDR. The stats overlay names the decoder
   "PyroWave 10-bit (Vulkan)", the host log says `PyroWave encoder session: ... 10-bit HDR`, and
   the stream log shows the swapchain colour space line and `HDR mode on: BT.2020 PQ`. Compare a
   known HDR scene against the HEVC Main10 path on the same display; with an SDR swapchain the
   tone-mapped picture should keep highlight detail instead of clipping. Then set the host display
   to SDR with HDR still enabled: the stream becomes 10-bit SDR (`HDR mode off: 10-bit SDR`) and
   must look like the 8-bit stream.
3. The stream log reports `Using PyroWave Vulkan renderer for WxH` and the stats overlay names
   the decoder "PyroWave (Vulkan)". A Galaxy XR field report (Themaister/pyrowave #17) measured
   3–4 ms decode per 1984x896 4:4:4 frame on the stock driver (Vulkan 1.3.295).

## Provenance and licences
Codec and Granite: MIT; vulkan-headers: Apache-2.0; renderer and Java glue: GPL-3.0 (same as
this app), from joemossjr16/artemis-android-pyrowave; protocol commits: joemossjr16/moonlight-common-c.
