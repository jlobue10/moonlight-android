# Builds libpyrowave-shared.so (the PyroWave C API, 0.6.x) for Android from the
# vendored codec tree in ./pyrowave, invoked by the `buildPyroWave` Gradle task
# with the NDK's CMake toolchain for each 64-bit ABI. The resulting library is
# consumed by Android.mk as a prebuilt. Mirrors third-party/pyrowave/CMakeLists.txt
# in Vibepollo (the host), which builds the same tree as a static C API.
cmake_minimum_required(VERSION 3.20)
project(pyrowave-android LANGUAGES C CXX)

set(PYROWAVE_VENDOR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/pyrowave")
if(NOT EXISTS "${PYROWAVE_VENDOR_DIR}/pyrowave.h")
    message(FATAL_ERROR "Vendored PyroWave is missing at ${PYROWAVE_VENDOR_DIR}; see VENDOR.txt.")
endif()

# Granite is only the Vulkan backend the codec is written against; none of its
# engine, windowing, shader compiler or FFmpeg features are wanted.
set(GRANITE_RENDERER OFF CACHE BOOL "" FORCE)
set(GRANITE_VULKAN_SPIRV_CROSS OFF CACHE BOOL "" FORCE)
set(GRANITE_VULKAN_SHADER_MANAGER_RUNTIME_COMPILER OFF CACHE BOOL "" FORCE)
set(GRANITE_VULKAN_SYSTEM_HANDLES OFF CACHE BOOL "" FORCE)
set(GRANITE_VULKAN_FOSSILIZE OFF CACHE BOOL "" FORCE)
set(GRANITE_VULKAN_DXGI_INTEROP OFF CACHE BOOL "" FORCE)
set(GRANITE_SHADER_COMPILER_OPTIMIZE OFF CACHE BOOL "" FORCE)
set(GRANITE_POSITION_INDEPENDENT ON CACHE BOOL "" FORCE)
set(GRANITE_SHIPPING ON CACHE BOOL "" FORCE)
set(GRANITE_TOOLS OFF CACHE BOOL "" FORCE)
set(GRANITE_FFMPEG OFF CACHE BOOL "" FORCE)
set(GRANITE_FFMPEG_VULKAN OFF CACHE BOOL "" FORCE)
set(GRANITE_AUDIO OFF CACHE BOOL "" FORCE)
set(GRANITE_BULLET OFF CACHE BOOL "" FORCE)
set(GRANITE_INSTALL_TARGETS OFF CACHE BOOL "" FORCE)
set(GRANITE_INSTALL_EXE_TARGETS OFF CACHE BOOL "" FORCE)
set(GRANITE_PLATFORM "null" CACHE STRING "" FORCE)
# Everything but the C API entry points stays out of the dynamic symbol table.
set(GRANITE_HIDDEN ON CACHE BOOL "" FORCE)

add_subdirectory("${PYROWAVE_VENDOR_DIR}/Granite" "${CMAKE_CURRENT_BINARY_DIR}/Granite" EXCLUDE_FROM_ALL)
# Namespace volk's vk* function pointers so they can never interpose on the
# app's own Vulkan loader use (the renderer dlopens libvulkan separately).
set_source_files_properties("${PYROWAVE_VENDOR_DIR}/Granite/third_party/volk/volk.c"
    TARGET_DIRECTORY granite-volk PROPERTIES LANGUAGE CXX)
target_compile_definitions(granite-volk-headers INTERFACE VOLK_NAMESPACE)

add_subdirectory("${PYROWAVE_VENDOR_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/pyrowave" EXCLUDE_FROM_ALL)

add_library(pyrowave-shared SHARED
        "${PYROWAVE_VENDOR_DIR}/pyrowave_c.cpp"
        "${PYROWAVE_VENDOR_DIR}/Granite/video/scaler.cpp")
target_compile_definitions(pyrowave-shared PRIVATE PYROWAVE_EXPORT_SYMBOLS)
target_include_directories(pyrowave-shared PRIVATE
        "${PYROWAVE_VENDOR_DIR}"
        "${PYROWAVE_VENDOR_DIR}/shaders"
        "${PYROWAVE_VENDOR_DIR}/Granite/video")
target_link_libraries(pyrowave-shared PRIVATE pyrowave granite-vulkan granite-math log)
target_compile_options(pyrowave-shared PRIVATE -fvisibility=hidden -Wno-missing-field-initializers)
set_target_properties(pyrowave-shared PROPERTIES
        CXX_STANDARD 14
        POSITION_INDEPENDENT_CODE ON
        LINK_FLAGS "-Wl,-z,max-page-size=16384")
