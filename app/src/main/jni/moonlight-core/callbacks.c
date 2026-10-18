#include <jni.h>

#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#include <Limelight.h>

#include <opus_multistream.h>
#include <android/log.h>

#include <cpu-features.h>

static OpusMSDecoder* Decoder;
static OPUS_MULTISTREAM_CONFIGURATION OpusConfig;

static JavaVM *JVM;
static pthread_key_t JniEnvKey;
static pthread_once_t JniEnvKeyInitOnce = PTHREAD_ONCE_INIT;
static jclass GlobalBridgeClass;
static jmethodID BridgeDrSetupMethod;
static jmethodID BridgeDrStartMethod;
static jmethodID BridgeDrStopMethod;
static jmethodID BridgeDrCleanupMethod;
static jmethodID BridgeDrSubmitDecodeUnitMethod;
static jmethodID BridgeDrSubmitPyroWaveFrameMethod;
static jmethodID BridgeArInitMethod;
static jmethodID BridgeArStartMethod;
static jmethodID BridgeArStopMethod;
static jmethodID BridgeArCleanupMethod;
static jmethodID BridgeArPlaySampleMethod;
static jmethodID BridgeClStageStartingMethod;
static jmethodID BridgeClStageCompleteMethod;
static jmethodID BridgeClStageFailedMethod;
static jmethodID BridgeClConnectionStartedMethod;
static jmethodID BridgeClConnectionTerminatedMethod;
static jmethodID BridgeClRumbleMethod;
static jmethodID BridgeClConnectionStatusUpdateMethod;
static jmethodID BridgeClSetHdrModeMethod;
static jmethodID BridgeClRumbleTriggersMethod;
static jmethodID BridgeClSetMotionEventStateMethod;
static jmethodID BridgeClSetControllerLEDMethod;
static jmethodID BridgeClSteamHapticMethod;
static jmethodID BridgeClSteamSettingsMethod;
static jbyteArray DecodedFrameBuffer;
static jshortArray DecodedAudioBuffer;

// PyroWave frames keep their RTP packet boundaries: every buffer of the decode
// unit is one packet payload, including zero-filled stand-ins for lost ones.
// The per-packet map goes to Java in a reusable int array (length, flags per
// packet) beside the frame bytes, staged in a C array between frames.
#define PYROWAVE_PACKET_LOST          0x1
#define PYROWAVE_PACKET_RECORD_START  0x2
#define PYROWAVE_PACKET_INTS          2
static bool PyroWaveStream;
static jintArray PyroWavePacketBuffer;
static jint* PyroWavePacketStaging;
static int PyroWavePacketStagingCount;

void DetachThread(void* context) {
    (*JVM)->DetachCurrentThread(JVM);
}

void JniEnvKeyInit(void) {
    // Create a TLS slot for the JNIEnv. We aren't in
    // a pthread during init, so we must wait until we
    // are to initialize this.
    pthread_key_create(&JniEnvKey, DetachThread);
}

JNIEnv* GetThreadEnv(void) {
    JNIEnv* env;

    // First check if this is already attached to the JVM
    if ((*JVM)->GetEnv(JVM, (void**)&env, JNI_VERSION_1_4) == JNI_OK) {
        return env;
    }

    // Create the TLS slot now that we're safely in a pthread
    pthread_once(&JniEnvKeyInitOnce, JniEnvKeyInit);

    // Try the TLS to see if we already have a JNIEnv
    env = pthread_getspecific(JniEnvKey);
    if (env)
        return env;

    // This is the thread's first JNI call, so attach now
    (*JVM)->AttachCurrentThread(JVM, &env, NULL);

    // Write our JNIEnv to TLS, so we detach before dying
    pthread_setspecific(JniEnvKey, env);

    return env;
}

JNIEXPORT void JNICALL
Java_com_limelight_nvstream_jni_MoonBridge_init(JNIEnv *env, jclass clazz) {
    (*env)->GetJavaVM(env, &JVM);
    GlobalBridgeClass = (*env)->NewGlobalRef(env, (*env)->FindClass(env, "com/limelight/nvstream/jni/MoonBridge"));
    BridgeDrSetupMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrSetup", "(IIII)I");
    BridgeDrStartMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrStart", "()V");
    BridgeDrStopMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrStop", "()V");
    BridgeDrCleanupMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrCleanup", "()V");
    BridgeDrSubmitDecodeUnitMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrSubmitDecodeUnit", "([BIIIICJJ)I");
    BridgeDrSubmitPyroWaveFrameMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeDrSubmitPyroWaveFrame", "([BI[IIIICJJ)I");
    BridgeArInitMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeArInit", "(III)I");
    BridgeArStartMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeArStart", "()V");
    BridgeArStopMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeArStop", "()V");
    BridgeArCleanupMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeArCleanup", "()V");
    BridgeArPlaySampleMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeArPlaySample", "([S)V");
    BridgeClStageStartingMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClStageStarting", "(I)V");
    BridgeClStageCompleteMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClStageComplete", "(I)V");
    BridgeClStageFailedMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClStageFailed", "(II)V");
    BridgeClConnectionStartedMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClConnectionStarted", "()V");
    BridgeClConnectionTerminatedMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClConnectionTerminated", "(I)V");
    BridgeClRumbleMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClRumble", "(SSS)V");
    BridgeClConnectionStatusUpdateMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClConnectionStatusUpdate", "(I)V");
    BridgeClSetHdrModeMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClSetHdrMode", "(Z[B)V");
    BridgeClRumbleTriggersMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClRumbleTriggers", "(SSS)V");
    BridgeClSetMotionEventStateMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClSetMotionEventState", "(SBS)V");
    BridgeClSetControllerLEDMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClSetControllerLED", "(SBBB)V");
    BridgeClSteamHapticMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClSteamHaptic", "(S[B)V");
    BridgeClSteamSettingsMethod = (*env)->GetStaticMethodID(env, clazz, "bridgeClSteamSettings", "(SB[B)V");
}

// NewGlobalRef can fail independently of NewByteArray. Publish a buffer only
// after both allocations succeed, and never retain the temporary local reference.
static jbyteArray createDecodedFrameBuffer(JNIEnv* env, jsize size) {
    jbyteArray local = (*env)->NewByteArray(env, size);
    if (local == NULL) {
        return NULL;
    }
    jbyteArray global = (*env)->NewGlobalRef(env, local);
    (*env)->DeleteLocalRef(env, local);
    return global;
}

// Same ownership rules for the PyroWave packet map (PYROWAVE_PACKET_INTS ints per packet)
static jintArray createPacketBuffer(JNIEnv* env, jsize packets) {
    jintArray local = (*env)->NewIntArray(env, packets * PYROWAVE_PACKET_INTS);
    if (local == NULL) {
        return NULL;
    }
    jintArray global = (*env)->NewGlobalRef(env, local);
    (*env)->DeleteLocalRef(env, local);
    return global;
}

int BridgeDrSetup(int videoFormat, int width, int height, int redrawRate, void* context, int drFlags) {
    JNIEnv* env = GetThreadEnv();
    int err;

    // Allocate before initializing the decoder so an allocation failure has no
    // decoder resources to unwind. A successful setup starts with a 32K buffer.
    jbyteArray buffer = createDecodedFrameBuffer(env, 32768);
    if (buffer == NULL) {
        (*env)->ExceptionClear(env);
        return -1;
    }

    err = (*env)->CallStaticIntMethod(env, GlobalBridgeClass, BridgeDrSetupMethod, videoFormat, width, height, redrawRate);
    if ((*env)->ExceptionCheck(env)) {
        (*env)->DeleteGlobalRef(env, buffer);
        // This is called on a Java thread, so it's safe to return
        return -1;
    }
    else if (err != 0) {
        (*env)->DeleteGlobalRef(env, buffer);
        return err;
    }

    DecodedFrameBuffer = buffer;

    // PyroWave frames are delivered per packet (see BridgeDrSubmitPyroWaveFrame); the
    // packet map is allocated by the first frame and grown as needed.
    PyroWaveStream = (videoFormat & VIDEO_FORMAT_MASK_PYROWAVE) != 0;
    if (PyroWaveStream) {
        __android_log_print(ANDROID_LOG_INFO, "moonlight-common-c", "Host PyroWave bitstream revision: %s (client %s)",
                            LiGetHostPyroWaveBitstreamId(), PYROWAVE_BITSTREAM_ID);
    }

    return 0;
}

void BridgeDrStart(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeDrStartMethod);
}

void BridgeDrStop(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeDrStopMethod);
}

void BridgeDrCleanup(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->DeleteGlobalRef(env, DecodedFrameBuffer);
    DecodedFrameBuffer = NULL;

    if (PyroWavePacketBuffer != NULL) {
        (*env)->DeleteGlobalRef(env, PyroWavePacketBuffer);
        PyroWavePacketBuffer = NULL;
    }
    free(PyroWavePacketStaging);
    PyroWavePacketStaging = NULL;
    PyroWavePacketStagingCount = 0;
    PyroWaveStream = false;

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeDrCleanupMethod);
}

// PyroWave: one buffer per RTP packet, concatenated in order so byte offsets stay
// packet-aligned (lost stand-ins are zero-filled and keep their real length), plus
// the packet map and the host's critical-packet count. The renderer walks the
// records itself, so nothing is split by buffer type here.
static int BridgeDrSubmitPyroWaveFrame(JNIEnv* env, PDECODE_UNIT decodeUnit) {
    PLENTRY currentEntry;
    int packets = 0;
    int criticalPackets = decodeUnit->pyrowaveCriticalPackets;

    for (currentEntry = decodeUnit->bufferList; currentEntry != NULL; currentEntry = currentEntry->next) {
        packets++;
    }

    if (PyroWavePacketStagingCount < packets) {
        jint* grown = realloc(PyroWavePacketStaging, (size_t)packets * PYROWAVE_PACKET_INTS * sizeof(jint));
        if (grown == NULL) {
            return DR_NEED_IDR;
        }
        PyroWavePacketStaging = grown;
        PyroWavePacketStagingCount = packets;
    }
    if (PyroWavePacketBuffer == NULL || (*env)->GetArrayLength(env, PyroWavePacketBuffer) < packets * PYROWAVE_PACKET_INTS) {
        jintArray grown = createPacketBuffer(env, packets);
        if (grown == NULL) {
            // Out of memory: keep the old map and let the caller request a new frame.
            (*env)->ExceptionClear(env);
            return DR_NEED_IDR;
        }
        if (PyroWavePacketBuffer != NULL) {
            (*env)->DeleteGlobalRef(env, PyroWavePacketBuffer);
        }
        PyroWavePacketBuffer = grown;
    }

    int offset = 0;
    int index = 0;
    for (currentEntry = decodeUnit->bufferList; currentEntry != NULL; currentEntry = currentEntry->next) {
        if (currentEntry->length <= 0) {
            // The depacketizer never queues an empty buffer. Should one appear, it
            // carries no bytes, so leaving it out of the map keeps the offsets right;
            // the critical prefix is counted in buffers, so shrink it with the map.
            if (index < criticalPackets) {
                criticalPackets--;
            }
            continue;
        }
        (*env)->SetByteArrayRegion(env, DecodedFrameBuffer, offset, currentEntry->length, (jbyte*)currentEntry->data);
        offset += currentEntry->length;

        PyroWavePacketStaging[index * PYROWAVE_PACKET_INTS] = currentEntry->length;
        PyroWavePacketStaging[index * PYROWAVE_PACKET_INTS + 1] =
            (currentEntry->bufferType == BUFFER_TYPE_LOST ? PYROWAVE_PACKET_LOST : 0) |
            (currentEntry->bufferType == BUFFER_TYPE_RECORD_START ? PYROWAVE_PACKET_RECORD_START : 0);
        index++;
    }
    (*env)->SetIntArrayRegion(env, PyroWavePacketBuffer, 0, index * PYROWAVE_PACKET_INTS, PyroWavePacketStaging);

    int ret = (*env)->CallStaticIntMethod(env, GlobalBridgeClass, BridgeDrSubmitPyroWaveFrameMethod,
                                          DecodedFrameBuffer, offset, PyroWavePacketBuffer, index, criticalPackets,
                                          decodeUnit->frameNumber, (jchar)decodeUnit->frameHostProcessingLatency,
                                          (jlong)decodeUnit->receiveTimeUs, (jlong)decodeUnit->enqueueTimeUs);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
        return DR_OK;
    }
    return ret;
}

int BridgeDrSubmitDecodeUnit(PDECODE_UNIT decodeUnit) {
    JNIEnv* env = GetThreadEnv();
    int ret;

    // Increase the size of our frame data buffer if our frame won't fit
    if ((*env)->GetArrayLength(env, DecodedFrameBuffer) < decodeUnit->fullLength) {
        jbyteArray grown = createDecodedFrameBuffer(env, decodeUnit->fullLength);
        if (grown == NULL) {
            // Out of memory: keep the old buffer and let the caller request a new frame.
            (*env)->ExceptionClear(env);
            return DR_NEED_IDR;
        }
        (*env)->DeleteGlobalRef(env, DecodedFrameBuffer);
        DecodedFrameBuffer = grown;
    }

    if (PyroWaveStream) {
        return BridgeDrSubmitPyroWaveFrame(env, decodeUnit);
    }

    PLENTRY currentEntry;
    int offset;

    currentEntry = decodeUnit->bufferList;
    offset = 0;
    while (currentEntry != NULL) {
        // Submit parameter set NALUs separately from picture data
        if (currentEntry->bufferType != BUFFER_TYPE_PICDATA) {
            // Use the beginning of the buffer each time since this is a separate
            // invocation of the decoder each time.
            (*env)->SetByteArrayRegion(env, DecodedFrameBuffer, 0, currentEntry->length, (jbyte*)currentEntry->data);

            ret = (*env)->CallStaticIntMethod(env, GlobalBridgeClass, BridgeDrSubmitDecodeUnitMethod,
                                              DecodedFrameBuffer, currentEntry->length, currentEntry->bufferType,
                                              decodeUnit->frameNumber, decodeUnit->frameType, (jchar)decodeUnit->frameHostProcessingLatency,
                                              (jlong)decodeUnit->receiveTimeUs, (jlong)decodeUnit->enqueueTimeUs);
            if ((*env)->ExceptionCheck(env)) {
                // We will crash here
                (*JVM)->DetachCurrentThread(JVM);
                return DR_OK;
            }
            else if (ret != DR_OK) {
                return ret;
            }
        }
        else {
            (*env)->SetByteArrayRegion(env, DecodedFrameBuffer, offset, currentEntry->length, (jbyte*)currentEntry->data);
            offset += currentEntry->length;
        }

        currentEntry = currentEntry->next;
    }

    ret = (*env)->CallStaticIntMethod(env, GlobalBridgeClass, BridgeDrSubmitDecodeUnitMethod,
                                       DecodedFrameBuffer, offset, BUFFER_TYPE_PICDATA,
                                       decodeUnit->frameNumber, decodeUnit->frameType, (jchar)decodeUnit->frameHostProcessingLatency,
                                       (jlong)decodeUnit->receiveTimeUs, (jlong)decodeUnit->enqueueTimeUs);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
        return DR_OK;
    }
    else {
        return ret;
    }
}

int BridgeArInit(int audioConfiguration, POPUS_MULTISTREAM_CONFIGURATION opusConfig, void* context, int flags) {
    JNIEnv* env = GetThreadEnv();
    int err;

    err = (*env)->CallStaticIntMethod(env, GlobalBridgeClass, BridgeArInitMethod, audioConfiguration, opusConfig->sampleRate, opusConfig->samplesPerFrame);
    if ((*env)->ExceptionCheck(env)) {
        // This is called on a Java thread, so it's safe to return
        err = -1;
    }
    if (err == 0) {
        memcpy(&OpusConfig, opusConfig, sizeof(*opusConfig));
        Decoder = opus_multistream_decoder_create(opusConfig->sampleRate,
                                                  opusConfig->channelCount,
                                                  opusConfig->streams,
                                                  opusConfig->coupledStreams,
                                                  opusConfig->mapping,
                                                  &err);
        if (Decoder == NULL) {
            (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArCleanupMethod);
            return -1;
        }

        // We know ahead of time what the buffer size will be for decoded audio, so pre-allocate it.
        // Publish it only once both allocations succeeded and drop the local reference, as the
        // frame buffer does: a NULL here would crash the first GetPrimitiveArrayCritical() instead
        // of failing the audio stage cleanly, and a pending OutOfMemoryError must not escape.
        jshortArray local = (*env)->NewShortArray(env, opusConfig->channelCount * opusConfig->samplesPerFrame);
        jshortArray global = local != NULL ? (*env)->NewGlobalRef(env, local) : NULL;
        if (local != NULL) {
            (*env)->DeleteLocalRef(env, local);
        }
        if (global == NULL) {
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
            }
            opus_multistream_decoder_destroy(Decoder);
            Decoder = NULL;
            (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArCleanupMethod);
            return -1;
        }
        DecodedAudioBuffer = global;
    }

    return err;
}

void BridgeArStart(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArStartMethod);
}

void BridgeArStop(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArStopMethod);
}

void BridgeArCleanup() {
    JNIEnv* env = GetThreadEnv();

    opus_multistream_decoder_destroy(Decoder);
    Decoder = NULL;

    (*env)->DeleteGlobalRef(env, DecodedAudioBuffer);
    DecodedAudioBuffer = NULL;

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArCleanupMethod);
}

void BridgeArDecodeAndPlaySample(char* sampleData, int sampleLength) {
    JNIEnv* env = GetThreadEnv();

    jshort* decodedData = (*env)->GetPrimitiveArrayCritical(env, DecodedAudioBuffer, NULL);

    int decodeLen = opus_multistream_decode(Decoder,
                                            (const unsigned char*)sampleData,
                                            sampleLength,
                                            decodedData,
                                            OpusConfig.samplesPerFrame,
                                            0);
    if (decodeLen > 0) {
        // We must release the array elements before making further JNI calls
        (*env)->ReleasePrimitiveArrayCritical(env, DecodedAudioBuffer, decodedData, 0);

        (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeArPlaySampleMethod, DecodedAudioBuffer);
        if ((*env)->ExceptionCheck(env)) {
            // We will crash here
            (*JVM)->DetachCurrentThread(JVM);
        }
    }
    else {
        // We can abort here to avoid the copy back since no data was modified
        (*env)->ReleasePrimitiveArrayCritical(env, DecodedAudioBuffer, decodedData, JNI_ABORT);
    }
}

void BridgeClStageStarting(int stage) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClStageStartingMethod, stage);
}

void BridgeClStageComplete(int stage) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClStageCompleteMethod, stage);
}

void BridgeClStageFailed(int stage, int errorCode) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClStageFailedMethod, stage, errorCode);
}

void BridgeClConnectionStarted(void) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClConnectionStartedMethod);
}

void BridgeClConnectionTerminated(int errorCode) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClConnectionTerminatedMethod, errorCode);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClRumble(unsigned short controllerNumber, unsigned short lowFreqMotor, unsigned short highFreqMotor) {
    JNIEnv* env = GetThreadEnv();

    // The seemingly redundant short casts are required in order to convert the unsigned short to a signed short.
    // If we leave it as an unsigned short, CheckJNI will fail when the value exceeds 32767. The cast itself is
    // fine because the Java code treats the value as unsigned even though it's stored in a signed type.
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClRumbleMethod, controllerNumber, (short)lowFreqMotor, (short)highFreqMotor);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClConnectionStatusUpdate(int connectionStatus) {
    JNIEnv* env = GetThreadEnv();

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClConnectionStatusUpdateMethod, connectionStatus);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
        return;
    }
}

void BridgeClSetHdrMode(bool enabled) {
    JNIEnv* env = GetThreadEnv();

    jbyteArray hdrMetadataByteArray = NULL;
    SS_HDR_METADATA hdrMetadata;

    // One snapshot of enabled + metadata: the flag queued with this callback and the
    // metadata of a later packet must not be mixed (that forced a decoder restart).
    if (LiGetHdrState(&enabled, &hdrMetadata)) {
        hdrMetadataByteArray = (*env)->NewByteArray(env, sizeof(SS_HDR_METADATA));
        (*env)->SetByteArrayRegion(env, hdrMetadataByteArray, 0, sizeof(SS_HDR_METADATA), (jbyte*)&hdrMetadata);
    }

    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClSetHdrModeMethod, enabled, hdrMetadataByteArray);
    if (hdrMetadataByteArray != NULL) {
        // The callback thread stays attached for the whole session; every HDR info
        // packet queues this callback, so the local would otherwise accumulate.
        (*env)->DeleteLocalRef(env, hdrMetadataByteArray);
    }
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClRumbleTriggers(unsigned short controllerNumber, unsigned short leftTrigger, unsigned short rightTrigger) {
    JNIEnv* env = GetThreadEnv();

    // The seemingly redundant short casts are required in order to convert the unsigned short to a signed short.
    // If we leave it as an unsigned short, CheckJNI will fail when the value exceeds 32767. The cast itself is
    // fine because the Java code treats the value as unsigned even though it's stored in a signed type.
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClRumbleTriggersMethod, controllerNumber, (short)leftTrigger, (short)rightTrigger);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClSetMotionEventState(uint16_t controllerNumber, uint8_t motionType, uint16_t reportRateHz) {
    JNIEnv* env = GetThreadEnv();

    // Varargs to a Java short: pass real shorts so CheckJNI accepts values >= 0x8000.
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClSetMotionEventStateMethod,
                                 (short)controllerNumber, motionType, (short)reportRateHz);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClSetControllerLED(uint16_t controllerNumber, uint8_t r, uint8_t g, uint8_t b) {
    JNIEnv* env = GetThreadEnv();

    // These jbyte casts are necessary to satisfy CheckJNI
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClSetControllerLEDMethod, controllerNumber, (jbyte)r, (jbyte)g, (jbyte)b);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClSteamHaptic(uint16_t controllerNumber, uint8_t length, const uint8_t *report) {
    JNIEnv* env = GetThreadEnv();

    jbyteArray array = (*env)->NewByteArray(env, length);
    if (array == NULL) {
        if ((*env)->ExceptionCheck(env)) {
            // We will crash here
            (*JVM)->DetachCurrentThread(JVM);
        }
        return;
    }
    (*env)->SetByteArrayRegion(env, array, 0, length, (const jbyte*)report);
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClSteamHapticMethod, (short)controllerNumber, array);
    (*env)->DeleteLocalRef(env, array);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClSteamSettings(uint16_t controllerNumber, uint8_t command, uint8_t count, const uint8_t *triples) {
    JNIEnv* env = GetThreadEnv();

    // Only the triples the write carries: the common-c layer bounded count already.
    jsize length = (jsize)count * 3;
    jbyteArray array = (*env)->NewByteArray(env, length);
    if (array == NULL) {
        if ((*env)->ExceptionCheck(env)) {
            // We will crash here
            (*JVM)->DetachCurrentThread(JVM);
        }
        return;
    }
    if (length != 0) {
        (*env)->SetByteArrayRegion(env, array, 0, length, (const jbyte*)triples);
    }
    (*env)->CallStaticVoidMethod(env, GlobalBridgeClass, BridgeClSteamSettingsMethod, (short)controllerNumber, (jbyte)command, array);
    (*env)->DeleteLocalRef(env, array);
    if ((*env)->ExceptionCheck(env)) {
        // We will crash here
        (*JVM)->DetachCurrentThread(JVM);
    }
}

void BridgeClLogMessage(const char* format, ...) {
    va_list va;
    va_start(va, format);
    __android_log_vprint(ANDROID_LOG_INFO, "moonlight-common-c", format, va);
    va_end(va);
}

static DECODER_RENDERER_CALLBACKS BridgeVideoRendererCallbacks = {
        .setup = BridgeDrSetup,
        .start = BridgeDrStart,
        .stop = BridgeDrStop,
        .cleanup = BridgeDrCleanup,
        .submitDecodeUnit = BridgeDrSubmitDecodeUnit,
};

static AUDIO_RENDERER_CALLBACKS BridgeAudioRendererCallbacks = {
        .init = BridgeArInit,
        .start = BridgeArStart,
        .stop = BridgeArStop,
        .cleanup = BridgeArCleanup,
        .decodeAndPlaySample = BridgeArDecodeAndPlaySample,
        .capabilities = CAPABILITY_SUPPORTS_ARBITRARY_AUDIO_DURATION
};

static CONNECTION_LISTENER_CALLBACKS BridgeConnListenerCallbacks = {
        .stageStarting = BridgeClStageStarting,
        .stageComplete = BridgeClStageComplete,
        .stageFailed = BridgeClStageFailed,
        .connectionStarted = BridgeClConnectionStarted,
        .connectionTerminated = BridgeClConnectionTerminated,
        .logMessage = BridgeClLogMessage,
        .rumble = BridgeClRumble,
        .connectionStatusUpdate = BridgeClConnectionStatusUpdate,
        .setHdrMode = BridgeClSetHdrMode,
        .rumbleTriggers = BridgeClRumbleTriggers,
        .setMotionEventState = BridgeClSetMotionEventState,
        .setControllerLED = BridgeClSetControllerLED,
        .steamHaptic = BridgeClSteamHaptic,
        .steamSettings = BridgeClSteamSettings,
};

static bool
hasFastAes() {
    if (android_getCpuCount() <= 2) {
        return false;
    }

    switch (android_getCpuFamily()) {
        case ANDROID_CPU_FAMILY_ARM:
            return !!(android_getCpuFeatures() & ANDROID_CPU_ARM_FEATURE_AES);
        case ANDROID_CPU_FAMILY_ARM64:
            return !!(android_getCpuFeatures() & ANDROID_CPU_ARM64_FEATURE_AES);
        case ANDROID_CPU_FAMILY_X86:
        case ANDROID_CPU_FAMILY_X86_64:
            return !!(android_getCpuFeatures() & ANDROID_CPU_X86_FEATURE_AES_NI);
        case ANDROID_CPU_FAMILY_MIPS:
        case ANDROID_CPU_FAMILY_MIPS64:
            return false;
        default:
            // Assume new architectures will all have crypto acceleration (RISC-V will)
            return true;
    }
}

JNIEXPORT jint JNICALL
Java_com_limelight_nvstream_jni_MoonBridge_startConnection(JNIEnv *env, jclass clazz,
                                                           jstring address, jstring appVersion, jstring gfeVersion,
                                                           jstring rtspSessionUrl, jint serverCodecModeSupport,
                                                           jint width, jint height, jint fps,
                                                           jint bitrate, jint pyroWaveFallbackBitrate,
                                                           jint packetSize, jint streamingRemotely,
                                                           jint audioConfiguration, jint supportedVideoFormats,
                                                           jint clientRefreshRateX100,
                                                           jbyteArray riAesKey, jbyteArray riAesIv,
                                                           jint videoCapabilities,
                                                           jint colorSpace, jint colorRange) {
    SERVER_INFORMATION serverInfo = {
            .address = (*env)->GetStringUTFChars(env, address, 0),
            .serverInfoAppVersion = (*env)->GetStringUTFChars(env, appVersion, 0),
            .serverInfoGfeVersion = gfeVersion ? (*env)->GetStringUTFChars(env, gfeVersion, 0) : NULL,
            .rtspSessionUrl = rtspSessionUrl ? (*env)->GetStringUTFChars(env, rtspSessionUrl, 0) : NULL,
            .serverCodecModeSupport = serverCodecModeSupport,
    };
    STREAM_CONFIGURATION streamConfig = {
            .width = width,
            .height = height,
            .fps = fps,
            .bitrate = bitrate,
            .pyroWaveFallbackBitrate = pyroWaveFallbackBitrate,
            .packetSize = packetSize,
            .streamingRemotely = streamingRemotely,
            .audioConfiguration = audioConfiguration,
            .supportedVideoFormats = supportedVideoFormats,
            .clientRefreshRateX100 = clientRefreshRateX100,
            .encryptionFlags = ENCFLG_AUDIO,
            .colorSpace = colorSpace,
            .colorRange = colorRange
    };

    jbyte* riAesKeyBuf = (*env)->GetByteArrayElements(env, riAesKey, NULL);
    memcpy(streamConfig.remoteInputAesKey, riAesKeyBuf, sizeof(streamConfig.remoteInputAesKey));
    (*env)->ReleaseByteArrayElements(env, riAesKey, riAesKeyBuf, JNI_ABORT);

    jbyte* riAesIvBuf = (*env)->GetByteArrayElements(env, riAesIv, NULL);
    memcpy(streamConfig.remoteInputAesIv, riAesIvBuf, sizeof(streamConfig.remoteInputAesIv));
    (*env)->ReleaseByteArrayElements(env, riAesIv, riAesIvBuf, JNI_ABORT);

    BridgeVideoRendererCallbacks.capabilities = videoCapabilities;

    // Enable all encryption features if the platform has fast AES support
    if (hasFastAes()) {
        streamConfig.encryptionFlags = ENCFLG_ALL;
    }

    int ret = LiStartConnection(&serverInfo,
                                &streamConfig,
                                &BridgeConnListenerCallbacks,
                                &BridgeVideoRendererCallbacks,
                                &BridgeAudioRendererCallbacks,
                                NULL, 0,
                                NULL, 0);

    (*env)->ReleaseStringUTFChars(env, address, serverInfo.address);
    (*env)->ReleaseStringUTFChars(env, appVersion, serverInfo.serverInfoAppVersion);
    if (gfeVersion != NULL) {
        (*env)->ReleaseStringUTFChars(env, gfeVersion, serverInfo.serverInfoGfeVersion);
    }
    if (rtspSessionUrl != NULL) {
        (*env)->ReleaseStringUTFChars(env, rtspSessionUrl, serverInfo.rtspSessionUrl);
    }

    return ret;
}
