#pragma once

// IEC 61937 data-burst framing for AC-3 and DTS frames.
//
// A burst is what an S/PDIF or HDMI link carries when it transports a
// compressed frame inside a 16-bit stereo PCM stream: a four-word preamble
// (Pa, Pb, Pc, Pd), the frame as little-endian 16-bit words, then zero
// padding so that the burst lasts exactly as long as the frame it carries
// (AC-3: 1536 samples = 6144 bytes, DTS core: 512 samples = 2048 bytes).
// Android plays such bursts through an ENCODING_IEC61937 AudioTrack.
//
// Kept free of libavcodec so that it can be compiled and checked on a host.

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Pc data-type values (IEC 61937-2 / -5)
#define IEC61937_AC3        0x01
#define IEC61937_DTS_TYPE_I 0x0B

#define IEC61937_PREAMBLE_BYTES 8

// Wraps one compressed frame in a burst of burst_bytes bytes.
//
// base_data_type: IEC61937_AC3 or IEC61937_DTS_TYPE_I. For AC-3 the frame's
// bitstream mode (bsmod, low three bits of byte 5) is copied into bits 8..10
// of Pc, as the IEC 61937-3 header requires.
//
// Returns burst_bytes, or -1 if the arguments are invalid or the frame does
// not fit (the preamble, the frame rounded up to whole words, and at least
// zero padding bytes must fit in burst_bytes).
int iec61937_pack(unsigned base_data_type, const uint8_t* data, int size, uint8_t* out, int burst_bytes);

#ifdef __cplusplus
}
#endif
