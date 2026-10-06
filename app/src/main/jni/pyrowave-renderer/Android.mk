# Android.mk for the PyroWave Vulkan renderer.
#
# libpyrowave-shared.so is built from the vendored codec source by the
# `buildPyroWave` Gradle task (CMakeLists.txt next to this file) into
# prebuilt/<abi>/ before ndk-build runs. PyroWave needs a 64-bit Vulkan 1.3
# GPU, so only arm64-v8a and x86_64 get the renderer; on other ABIs, or when the
# library was not built, the renderer module is absent and the app never offers
# PyroWave (PyroWaveDecoderRenderer.isAvailable() returns false).
LOCAL_PATH := $(call my-dir)

ifneq ($(filter arm64-v8a x86_64,$(TARGET_ARCH_ABI)),)
ifneq ($(wildcard $(LOCAL_PATH)/prebuilt/$(TARGET_ARCH_ABI)/libpyrowave-shared.so),)

include $(CLEAR_VARS)
LOCAL_MODULE := pyrowave-shared
LOCAL_SRC_FILES := prebuilt/$(TARGET_ARCH_ABI)/libpyrowave-shared.so
include $(PREBUILT_SHARED_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE := pyrowave-renderer
LOCAL_SRC_FILES := pyrowave_renderer.cpp
LOCAL_C_INCLUDES := $(LOCAL_PATH)
LOCAL_CPPFLAGS := -std=c++17 -Wall -Wextra -Wno-missing-field-initializers -fno-exceptions -fno-rtti
LOCAL_SHARED_LIBRARIES := pyrowave-shared
# Vulkan is loaded with dlopen at runtime; libvulkan is not linked.
LOCAL_LDLIBS := -llog -landroid -ldl
LOCAL_LDFLAGS += -Wl,-z,max-page-size=16384
include $(BUILD_SHARED_LIBRARY)

endif
endif
