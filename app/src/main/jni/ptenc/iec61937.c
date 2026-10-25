#include "iec61937.h"

#include <string.h>

static void put_le16(uint8_t* p, unsigned v) {
    p[0] = v & 0xff;
    p[1] = (v >> 8) & 0xff;
}

int iec61937_pack(unsigned base_data_type, const uint8_t* data, int size, uint8_t* out, int burst_bytes) {
    if (data == NULL || out == NULL || size <= 0 || burst_bytes <= 0) {
        return -1;
    }
    if (base_data_type != IEC61937_AC3 && base_data_type != IEC61937_DTS_TYPE_I) {
        return -1;
    }

    unsigned data_type = base_data_type;
    if (base_data_type == IEC61937_AC3) {
        if (size < 6) {
            return -1;
        }
        // Pc carries the frame's bitstream mode (bsmod)
        data_type |= (unsigned)(data[5] & 0x7) << 8;
    }

    // Whole 16-bit words, with a lone final byte occupying one more word
    int payload = size & ~1;
    int used = IEC61937_PREAMBLE_BYTES + payload + ((size & 1) ? 2 : 0);
    if (used > burst_bytes) {
        return -1;
    }

    put_le16(out + 0, 0xF872);                           // Pa
    put_le16(out + 2, 0x4E1F);                           // Pb
    put_le16(out + 4, data_type);                        // Pc
    put_le16(out + 6, (unsigned)((size + 1) & ~1) << 3); // Pd: payload length in bits

    // The compressed bitstream is big-endian 16-bit words; the burst is little-endian
    for (int i = 0; i < payload; i += 2) {
        out[IEC61937_PREAMBLE_BYTES + i] = data[i + 1];
        out[IEC61937_PREAMBLE_BYTES + i + 1] = data[i];
    }
    int pos = IEC61937_PREAMBLE_BYTES + payload;
    if (size & 1) {
        // A final lone byte is MSB aligned
        put_le16(out + pos, (unsigned)data[size - 1] << 8);
        pos += 2;
    }
    memset(out + pos, 0, (size_t)(burst_bytes - pos));
    return burst_bytes;
}
