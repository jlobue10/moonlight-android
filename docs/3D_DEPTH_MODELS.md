# Depth models for the AI SBS 3D modes

The "AI SBS 3D" render modes turn the 2D stream into side-by-side 3D: a monocular depth network
estimates a depth map for each (changed) frame and a fragment shader shifts pixels per eye
(`utils/Stereo3DRenderer.java`, `utils/ShaderUtils.java`). The **3D Depth Model** setting picks
the network. All models produce *relative inverse depth* (larger = nearer); the renderer min-max
normalises the map to 8 bit, smooths it temporally, blurs it, and upsamples it edge-aware against
a model-resolution copy of the frame (joint bilateral upsampling, toggle: *Edge-aware depth
upsampling*).

| Setting | Model | Input | Delivery | Size |
|---|---|---|---|---|
| Fast (default) | MiDaS v2, Qualcomm AI Hub w8a8 export | uint8 256×256 | bundled asset `midas-midas-v2-w8a8.tflite` | 17.7 MB in the APK |
| Quality | Depth Anything V2 Small, 252 px, float16 weights, GPU-delegate graph | float32 252×252 RGB 0..1 | downloaded on first selection | 97,885,536 bytes |
| High | Depth Anything V2 Small, 364 px, float16 weights, GPU-friendly graph | float32 364×364 RGB 0..1 | downloaded on first selection | 49,659,680 bytes |

Tensor shapes and data types are read from the interpreter at load time
(`Stereo3DRenderer.adoptTensorLayout`), so a model only needs a descriptor entry in
`utils/DepthModel.java`: preference value, file name, input edge, URL, SHA-256, size.

## Where the downloaded files come from

The app downloads straight from the releases that published the files and verifies size and
SHA-256 (`utils/DepthModelDownloader.java`) before moving the file into
`<app files>/depth-models/`. A failed check leaves nothing behind and the setting stays on the
previous model.

- **Quality (252):** [coldbricks/finally-models, release `depth-anything-v2-small-252-gpu-v2`](https://github.com/coldbricks/finally-models/releases/tag/depth-anything-v2-small-252-gpu-v2).
  Built for the "Finally" 2D-to-3D player on Quest 3 (Snapdragon XR2 Gen 2, the same Adreno 740
  class as the Galaxy XR); the release notes report 702/702 nodes on the LiteRT GPU delegate at
  16-bit float and about 65 ms per inference. v2 fixes a v1 fp16 softmax overflow that returned a
  flat depth map on bright, high-contrast frames. Contract: input `[1,252,252,3]` float32 RGB in
  0..1 (ImageNet normalisation baked in), output `[1,252,252,1]` float32 inverse depth.
  SHA-256 `5eeaa55d868ca29583e95a9b648e9ee816b7024f6d1821d0cf21269ccc00bee6`.
- **High (364):** [illuminazionetech/VRClip, release `depth-model`](https://github.com/illuminazionetech/VRClip/releases/tag/depth-model),
  file `depth_anything_v2_small_live_364.tflite`, exported by that project's
  `tools/depth-model/export.py`: float16 weights, all tensors ≤ 4-D, pixel-shuffle instead of
  transposed convolutions so the GPU delegate takes the whole graph. Contract: input
  `[1,364,364,3]` float32 RGB in 0..1 (normalisation baked in), output `[1,364,364]` float32
  inverse depth. SHA-256 `875590dad368b1f3c97d1051ca4f3c48d75f51dc48c0c95f9224500028b6e273`.

### Licences

- Depth Anything V2 **Small** weights: Apache-2.0 (The University of Hong Kong / TikTok). The Base,
  Large and Giant variants are CC-BY-NC-4.0 and must not be used here.
- finally-models: Apache-2.0, with a NOTICE describing the GPU-specific graph changes.
- VRClip is GPL-3.0 as an application; the model file is a conversion of the Apache-2.0 weights
  and is distributed by its authors under that licence in the release notes.
- MiDaS: MIT.

The app does not redistribute the downloaded files, it fetches them from the publishers. If a
mirror is ever wanted (for availability or to pin the files independently of the upstream
projects), attach them to a **pre-release** in this repository (a pre-release never becomes
"Latest", so Obtainium, which filters on the APK name, is not confused), copy the NOTICE/LICENSE
files next to them, and change the URLs in `DepthModel.java`; the checksums stay the same.

## Performance expectations (not yet measured on the Galaxy XR)

- Fast (MiDaS 256 int8): the historical default; the depth refresh rate normally exceeds the
  stream rate.
- Quality (252): roughly 65 ms per depth map on the Quest 3 figure above, i.e. about 15 depth
  updates per second. In the non-synced "AI SBS 3D" mode the video still renders at full rate and
  reuses the latest map (the scene-change heuristic already skips static frames). In "Movie
  (Synced)" mode the renderer waits for the frame's own map, now bounded to 100 ms, so the
  effective frame rate follows the depth rate.
- High (364): about 2.1× the tokens of the 252 model; expect roughly half its depth rate.

## Validation still owed

Nothing in this document has been run on the headset. Check, in this order: that the models
download and verify; that the GPU delegate takes the graph (the perf overlay's "Delegate" line
shows `GPU DAv2-252` etc.; `CPU` means a fallback); the depth update rate ("3DFPS" in the
overlay); and visually that depth edges follow object edges with the edge-aware upsampling on
versus off.
