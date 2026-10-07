#version 450
// PyroWave decodes to three planes (Y, Cb, Cr) holding normalized code values, the
// way Vibepollo's converter wrote them: limited or full range at 8 or 10 bits, BT.709
// (SDR) or BT.2020 non-constant-luminance (HDR10, PQ-encoded) coefficients. 4:2:0
// chroma is left-cosited, so shift chroma sampling by a quarter chroma texel to line
// it up with centre-sampled luma. The push constants (pyrowave_renderer.cpp,
// CscParams) select the range, the matrix and how the picture reaches the swapchain:
//
//   content_pq=0 output_pq=0  SDR in, SDR swapchain: gamma-encoded BT.709 straight through
//   content_pq=1 output_pq=1  HDR10 in, HDR10 (ST 2084) swapchain: PQ BT.2020 straight through
//   content_pq=1 output_pq=0  HDR10 in, SDR swapchain: PQ -> nits -> tone map -> BT.709 -> sRGB
//   content_pq=0 output_pq=1  10-bit SDR in, HDR10 swapchain: sRGB -> nits at SDR white -> PQ
layout(location = 0) in vec2 v_uv;
layout(location = 0) out vec4 frag;

layout(set = 0, binding = 0) uniform sampler2D u_y;
layout(set = 0, binding = 1) uniform sampler2D u_cb;
layout(set = 0, binding = 2) uniform sampler2D u_cr;

layout(push_constant) uniform Params {
    vec2 y_range;          // (scale, offset): Y' = (y - offset) * scale
    vec2 c_range;          // (scale, offset): C  = (c - offset) * scale
    int matrix;            // 0 = BT.709, 1 = BT.2020 NCL
    int content_pq;        // 1 = samples are PQ-encoded BT.2020 (HDR10)
    int output_pq;         // 1 = swapchain colour space is HDR10 ST 2084
    float peak_nits;       // tone mapping: content peak (MaxCLL or mastering peak)
    float sdr_white_nits;  // where SDR reference white sits on the PQ scale
} p;

// SMPTE ST 2084 constants
const float PQ_M1 = 0.1593017578125;
const float PQ_M2 = 78.84375;
const float PQ_C1 = 0.8359375;
const float PQ_C2 = 18.8515625;
const float PQ_C3 = 18.6875;

// PQ signal (0..1) -> linear light in units of 10000 nits (0..1)
vec3 pq_eotf(vec3 e) {
    vec3 np = pow(max(e, vec3(0.0)), vec3(1.0 / PQ_M2));
    vec3 num = max(np - PQ_C1, vec3(0.0));
    vec3 den = PQ_C2 - PQ_C3 * np;
    return pow(num / den, vec3(1.0 / PQ_M1));
}

// linear light (units of 10000 nits) -> PQ signal
vec3 pq_oetf(vec3 y) {
    vec3 yp = pow(max(y, vec3(0.0)), vec3(PQ_M1));
    return pow((PQ_C1 + PQ_C2 * yp) / (1.0 + PQ_C3 * yp), vec3(PQ_M2));
}

vec3 srgb_eotf(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    vec3 lo = c / 12.92;
    vec3 hi = pow((c + 0.055) / 1.055, vec3(2.4));
    return mix(lo, hi, step(vec3(0.04045), c));
}

vec3 srgb_oetf(vec3 l) {
    l = clamp(l, 0.0, 1.0);
    vec3 lo = l * 12.92;
    vec3 hi = 1.055 * pow(l, vec3(1.0 / 2.4)) - 0.055;
    return mix(lo, hi, step(vec3(0.0031308), l));
}

// Linear BT.2020 -> linear BT.709 primaries (rows apply to a column vector)
const mat3 BT2020_TO_BT709 = mat3(
    1.6605, -0.1246, -0.0182,
   -0.5876,  1.1329, -0.1006,
   -0.0728, -0.0083,  1.1187);

// Linear BT.709 -> linear BT.2020 primaries
const mat3 BT709_TO_BT2020 = mat3(
    0.6274, 0.0691, 0.0164,
    0.3293, 0.9195, 0.0880,
    0.0433, 0.0114, 0.8956);

void main() {
    vec2 cuv = v_uv;
    float cw = float(textureSize(u_cb, 0).x);
    if (cw < float(textureSize(u_y, 0).x)) {
        cuv.x += 0.25 / cw;
    }

    float y = (texture(u_y, v_uv).r - p.y_range.y) * p.y_range.x;
    float cb = (texture(u_cb, cuv).r - p.c_range.y) * p.c_range.x;
    float cr = (texture(u_cr, cuv).r - p.c_range.y) * p.c_range.x;

    // Non-linear R'G'B' in the content's own encoding
    vec3 rgb;
    if (p.matrix == 1) {
        rgb = vec3(
            y + 1.4746 * cr,
            y - 0.16455 * cb - 0.57135 * cr,
            y + 1.8814 * cb);
    } else {
        rgb = vec3(
            y + 1.5748 * cr,
            y - 0.1873 * cb - 0.4681 * cr,
            y + 1.8556 * cb);
    }
    rgb = clamp(rgb, 0.0, 1.0);

    if (p.content_pq == p.output_pq) {
        // Same encoding on both sides: the swapchain colour space carries the meaning.
        frag = vec4(rgb, 1.0);
        return;
    }

    if (p.content_pq == 1) {
        // HDR10 picture on an SDR swapchain: tone map in linear light.
        vec3 lin2020 = pq_eotf(rgb) * 10000.0 / p.sdr_white_nits;   // 1.0 = SDR reference white
        float peak = max(p.peak_nits / p.sdr_white_nits, 1.0);
        // Extended Reinhard on the brightest channel keeps hue; highlights above the
        // content peak clip, the rest rolls off towards it.
        float m = max(lin2020.r, max(lin2020.g, lin2020.b));
        float mapped = m * (1.0 + m / (peak * peak)) / (1.0 + m);
        vec3 lin = lin2020 * (m > 0.0 ? mapped / m : 0.0);
        vec3 lin709 = BT2020_TO_BT709 * lin;
        frag = vec4(srgb_oetf(lin709), 1.0);
    } else {
        // SDR picture on an HDR10 swapchain: place SDR white at sdr_white_nits.
        vec3 lin709 = srgb_eotf(rgb) * p.sdr_white_nits / 10000.0;
        vec3 lin2020 = BT709_TO_BT2020 * lin709;
        frag = vec4(pq_oetf(lin2020), 1.0);
    }
}
