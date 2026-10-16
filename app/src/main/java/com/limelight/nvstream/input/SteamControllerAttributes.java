package com.limelight.nvstream.input;

import com.limelight.nvstream.jni.MoonBridge;

import java.util.Locale;

/**
 * The real Steam Controller (2026)'s firmware attributes, read by a transport at link setup and
 * forwarded to a Vibepollo host (LI_CCAP_STEAM_ATTRIBUTES), whose virtual Steam Controller echoes
 * them to Steam. Steam then shows the real unit's firmware build and offers a firmware update only
 * when the real controller needs one.
 * <p>
 * Never start that update from Steam while streaming: it would target the virtual controller on
 * the host, not the real one. Update with the controller connected directly to a PC running Steam.
 * <p>
 * Parsers take the payload that follows the feature report's {@code [type][len]} (and, over USB,
 * its report id): GET_ATTRIBUTES_VALUES 0x83 as {@code (tag u8, value u32 LE)} pairs, GET_DEVICE_INFO
 * 0xF2 sub 0 as {@code [00][firmware build u32][board revision u32][build id 16][unit serial 16]},
 * GET_STRING_ATTRIBUTE 0xAE as {@code [tag][chars]}. Strings keep at most 15 printable ASCII
 * characters, which is what the packet carries.
 */
public final class SteamControllerAttributes {
    public static final int MAX_STRING = 15;
    // GET_ATTRIBUTES_VALUES tags (SDL steam/controller_constants.h, confirmed on the wired unit)
    private static final int TAG_CAPABILITIES = 2, TAG_FIRMWARE_BUILD_TIME = 4, TAG_RADIO_FIRMWARE_BUILD_TIME = 5,
            TAG_DONGLE_FIRMWARE_BUILD_TIME = 8, TAG_BOARD_REVISION = 9, TAG_BOOTLOADER_BUILD_TIME = 10;
    private static final int STRING_TAG_BOARD_SERIAL = 0, STRING_TAG_UNIT_SERIAL = 1;

    public final byte transport;   // MoonBridge.LI_SCATTR_TRANSPORT_*
    public int validMask;          // MoonBridge.LI_SCATTR_* bits
    public int firmwareBuildTime, bootloaderBuildTime, boardRevision, capabilities;
    public int radioFirmwareBuildTime, dongleFirmwareBuildTime;
    public String buildId = "", unitSerial = "", boardSerial = "";

    public SteamControllerAttributes(byte transport) {
        this.transport = transport;
    }

    /** True when no query produced a value; nothing is worth sending then. */
    public boolean isEmpty() {
        return validMask == 0;
    }

    /** @return how many (tag, value) pairs were taken from a GET_ATTRIBUTES_VALUES reply payload. */
    public int parseAttributeValues(byte[] p, int offset, int length) {
        int pairs = 0;
        for (int i = offset; i + 5 <= offset + length && i + 5 <= p.length; i += 5, pairs++) {
            int tag = p[i] & 0xFF;
            int value = u32(p, i + 1);
            switch (tag) {
                case TAG_CAPABILITIES:
                    capabilities = value;
                    validMask |= MoonBridge.LI_SCATTR_CAPABILITIES;
                    break;
                case TAG_FIRMWARE_BUILD_TIME:
                    firmwareBuildTime = value;
                    validMask |= MoonBridge.LI_SCATTR_FIRMWARE_BUILD_TIME;
                    break;
                case TAG_RADIO_FIRMWARE_BUILD_TIME:
                    radioFirmwareBuildTime = value;
                    validMask |= MoonBridge.LI_SCATTR_RADIO_FW_BUILD_TIME;
                    break;
                case TAG_DONGLE_FIRMWARE_BUILD_TIME:
                    dongleFirmwareBuildTime = value;
                    validMask |= MoonBridge.LI_SCATTR_DONGLE_FW_BUILD_TIME;
                    break;
                case TAG_BOARD_REVISION:
                    boardRevision = value;
                    validMask |= MoonBridge.LI_SCATTR_BOARD_REVISION;
                    break;
                case TAG_BOOTLOADER_BUILD_TIME:
                    bootloaderBuildTime = value;
                    validMask |= MoonBridge.LI_SCATTR_BOOTLOADER_BUILD_TIME;
                    break;
                default:
                    // 1 product id and 11 connection interval describe the link, not the firmware
                    break;
            }
        }
        return pairs;
    }

    /** @return true when a GET_DEVICE_INFO sub 0 payload supplied the build id. */
    public boolean parseDeviceInfo(byte[] p, int offset, int length) {
        // [sub id 00][firmware build u32][board revision u32][build id 16][unit serial 16]
        if (length < 25 || offset + 25 > p.length || p[offset] != 0) {
            return false;
        }
        String id = ascii(p, offset + 9, Math.min(16, length - 9));
        if (id.isEmpty()) {
            return false;
        }
        buildId = id;
        validMask |= MoonBridge.LI_SCATTR_BUILD_ID;
        if ((validMask & MoonBridge.LI_SCATTR_FIRMWARE_BUILD_TIME) == 0) {
            firmwareBuildTime = u32(p, offset + 1);
            validMask |= MoonBridge.LI_SCATTR_FIRMWARE_BUILD_TIME;
        }
        if ((validMask & MoonBridge.LI_SCATTR_BOARD_REVISION) == 0) {
            boardRevision = u32(p, offset + 5);
            validMask |= MoonBridge.LI_SCATTR_BOARD_REVISION;
        }
        if (length >= 41 && offset + 41 <= p.length && unitSerial.isEmpty()) {
            String serial = ascii(p, offset + 25, 16);
            if (!serial.isEmpty()) {
                unitSerial = serial;
                validMask |= MoonBridge.LI_SCATTR_SERIALS;
            }
        }
        return true;
    }

    /** @return true when a GET_STRING_ATTRIBUTE payload ({@code [tag][chars]}) supplied a serial. */
    public boolean parseStringAttribute(byte[] p, int offset, int length) {
        if (length < 2 || offset + 2 > p.length) {
            return false;
        }
        String text = ascii(p, offset + 1, Math.min(length - 1, p.length - offset - 1));
        if (text.isEmpty()) {
            return false;
        }
        switch (p[offset] & 0xFF) {
            case STRING_TAG_UNIT_SERIAL:
                unitSerial = text;
                break;
            case STRING_TAG_BOARD_SERIAL:
                boardSerial = text;
                break;
            default:
                return false;
        }
        validMask |= MoonBridge.LI_SCATTR_SERIALS;
        return true;
    }

    /** The stream-log line: what was read and will reach the host. */
    public String describe() {
        if (isEmpty()) {
            return "no firmware attributes read (the host keeps its defaults)";
        }
        return "firmware build " + date(firmwareBuildTime, MoonBridge.LI_SCATTR_FIRMWARE_BUILD_TIME)
                + String.format(Locale.ROOT, " (0x%08x)", firmwareBuildTime)
                + ", bootloader " + date(bootloaderBuildTime, MoonBridge.LI_SCATTR_BOOTLOADER_BUILD_TIME)
                + ", board rev " + ((validMask & MoonBridge.LI_SCATTR_BOARD_REVISION) != 0 ? Integer.toString(boardRevision) : "n/a")
                + ", capabilities " + ((validMask & MoonBridge.LI_SCATTR_CAPABILITIES) != 0 ? String.format(Locale.ROOT, "0x%x", capabilities) : "n/a")
                + ", build id " + ((validMask & MoonBridge.LI_SCATTR_BUILD_ID) != 0 ? buildId : "n/a")
                + ", serial " + (unitSerial.isEmpty() ? "n/a" : unitSerial)
                + (boardSerial.isEmpty() ? "" : " (board " + boardSerial + ")")
                + ", radio fw " + date(radioFirmwareBuildTime, MoonBridge.LI_SCATTR_RADIO_FW_BUILD_TIME)
                + ", dongle fw " + date(dongleFirmwareBuildTime, MoonBridge.LI_SCATTR_DONGLE_FW_BUILD_TIME);
    }

    private String date(int unixSeconds, int bit) {
        return (validMask & bit) != 0 ? isoDate(unixSeconds) : "n/a";
    }

    /** Unix seconds (unsigned 32-bit) as "2026-07-07 23:04Z"; plain arithmetic, no time-zone data needed. */
    public static String isoDate(int unixSeconds) {
        long seconds = unixSeconds & 0xFFFFFFFFL;
        long days = seconds / 86400, rest = seconds % 86400;
        // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
        long z = days + 719468;
        long era = z / 146097;
        long doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        long mp = (5 * doy + 2) / 153;
        long day = doy - (153 * mp + 2) / 5 + 1;
        long month = mp < 10 ? mp + 3 : mp - 9;
        long year = yoe + era * 400 + (month <= 2 ? 1 : 0);
        return String.format(Locale.ROOT, "%04d-%02d-%02d %02d:%02dZ", year, month, day, rest / 3600, (rest % 3600) / 60);
    }

    /** Printable ASCII up to the first NUL, at most MAX_STRING characters; other bytes become '?'. */
    public static String ascii(byte[] p, int offset, int max) {
        StringBuilder sb = new StringBuilder();
        for (int i = offset; i < offset + max && i < p.length && sb.length() < MAX_STRING; i++) {
            int c = p[i] & 0xFF;
            if (c == 0) {
                break;
            }
            sb.append(c >= 0x20 && c <= 0x7E ? (char) c : '?');
        }
        return sb.toString();
    }

    private static int u32(byte[] p, int i) {
        return (p[i] & 0xFF) | ((p[i + 1] & 0xFF) << 8) | ((p[i + 2] & 0xFF) << 16) | ((p[i + 3] & 0xFF) << 24);
    }
}
