#!/usr/bin/env python3
"""Compile the real renderer and simulate two display-acquire timeouts.

Uses the vendored Vulkan declarations with fake dispatch and Android/JNI edges;
does not require a GPU, NDK or PyroWave shared library. --baseline uses HEAD.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RENDERER = ROOT/'app/src/main/jni/pyrowave-renderer'
STUBS = {
'jni.h': '''#pragma once
#include <cstdint>
#define JNIEXPORT
#define JNICALL
#define JNI_TRUE 1
#define JNI_FALSE 0
using jboolean=bool; using jlong=int64_t; using jint=int; using jfloat=float;
using jobject=void*; using jclass=void*; using jbyteArray=void*; using jbyte=signed char;
struct JNIEnv {int GetArrayLength(jbyteArray){return 0;} void GetByteArrayRegion(jbyteArray,int,int,jbyte*){}};
''',
'android/native_window_jni.h': '''#pragma once
#include <jni.h>
struct ANativeWindow {};
inline ANativeWindow* ANativeWindow_fromSurface(JNIEnv*,jobject){return nullptr;}
inline void ANativeWindow_release(ANativeWindow*){}
''',
'android/log.h': '''#pragma once
#define ANDROID_LOG_INFO 4
#define ANDROID_LOG_WARN 5
#define ANDROID_LOG_ERROR 6
inline int __android_log_print(int,const char*,const char*,...){return 0;}
''',
'sys/system_properties.h': '''#pragma once
#define PROP_VALUE_MAX 92
inline int __system_property_get(const char*,char*){return 0;}
'''}
TEST = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <memory>
#include <mutex>
#include <vector>
#include <cstdio>
#define private public
#include "renderer.cpp"
#undef private
static bool pending=false;
static VkFence pendingFence=VK_NULL_HANDLE;
static int invalidResets=0, submits=0, waits=0;
static uint64_t queryTicks[3];
// The baseline renderer discarded timestampValidBits. Let it run the same
// wrapping-counter fixtures without a test-only change to its implementation.
template<class T> auto setTimestampMask(T& r,uint64_t mask,int) -> decltype(r.timestampMask=mask,void()) {r.timestampMask=mask;}
template<class T> void setTimestampMask(T&,uint64_t,long) {}
extern "C" {
void pyrowave_device_set_command_buffer(pyrowave_device,VkCommandBuffer){}
pyrowave_result pyrowave_decoder_decode_gpu_buffer(pyrowave_decoder,const pyrowave_gpu_sync_operation*,
 const pyrowave_gpu_sync_operation*,const pyrowave_gpu_buffers*){return PYROWAVE_SUCCESS;}
void pyrowave_device_report_performance_stats(pyrowave_device,pyrowave_message_cb,void*,bool){}
void pyrowave_decoder_destroy(pyrowave_decoder){}
void pyrowave_device_destroy(pyrowave_device){}
pyrowave_result pyrowave_decoder_push_packet(pyrowave_decoder,const void*,size_t){return PYROWAVE_SUCCESS;}
}
int main(){
 Renderer renderer;
 auto &vk=renderer.vk;
 vk.CreateCommandPool=[](VkDevice,const VkCommandPoolCreateInfo*,const VkAllocationCallbacks*,VkCommandPool*){return VK_SUCCESS;};
 vk.AllocateCommandBuffers=[](VkDevice,const VkCommandBufferAllocateInfo*,VkCommandBuffer* b){b[0]=(VkCommandBuffer)1;b[1]=(VkCommandBuffer)2;return VK_SUCCESS;};
 vk.CreateFence=[](VkDevice,const VkFenceCreateInfo*,const VkAllocationCallbacks*,VkFence* f){static uintptr_t next=0;*f=(VkFence)++next;return VK_SUCCESS;};
 vk.CreateSemaphore=[](VkDevice,const VkSemaphoreCreateInfo*,const VkAllocationCallbacks*,VkSemaphore*){return VK_SUCCESS;};
 vk.WaitForFences=[](VkDevice,uint32_t count,const VkFence* fences,VkBool32,uint64_t){
   ++waits;for(uint32_t i=0;i<count;i++)if(pendingFence!=VK_NULL_HANDLE && fences[i]==pendingFence)pending=false;
   return VK_SUCCESS;};
 vk.ResetFences=[](VkDevice,uint32_t,const VkFence*){return VK_SUCCESS;};
 vk.ResetCommandBuffer=[](VkCommandBuffer,VkCommandBufferResetFlags){if(pending)++invalidResets;return VK_SUCCESS;};
 vk.BeginCommandBuffer=[](VkCommandBuffer,const VkCommandBufferBeginInfo*){return VK_SUCCESS;};
 vk.EndCommandBuffer=[](VkCommandBuffer){return VK_SUCCESS;};
 vk.CmdPipelineBarrier=[](VkCommandBuffer,VkPipelineStageFlags,VkPipelineStageFlags,VkDependencyFlags,uint32_t,const VkMemoryBarrier*,uint32_t,const VkBufferMemoryBarrier*,uint32_t,const VkImageMemoryBarrier*){};
 vk.QueueSubmit=[](VkQueue,uint32_t,const VkSubmitInfo*,VkFence fence){pending=true;pendingFence=fence;++submits;return VK_SUCCESS;};
 vk.AcquireNextImageKHR=[](VkDevice,VkSwapchainKHR,uint64_t,VkSemaphore,VkFence,uint32_t*){return VK_TIMEOUT;};
 if(!renderer.createFrameResources() || !renderer.present() || !renderer.present())return 2;
 printf("%s two acquire timeouts: %d pending command-buffer resets (%d submits, %d waits)\n",
        invalidResets?"FAIL":"PASS",invalidResets,submits,waits);
 // Exercise all container dispatch paths with deterministic malformed inputs.
 const uint8_t prefixed[]={1,0,0,0,1,0,0,0,42};
 const uint8_t pyrw[]={'P','Y','R','W',1,0,1,0,0,0,0,1,42};
 const uint8_t record[]={0,0,0,128,0,0,0,0};
 if(!renderer.pushFrame(prefixed,sizeof(prefixed)) || !renderer.pushFrame(pyrw,sizeof(pyrw)) ||
    !renderer.pushFrame(record,sizeof(record)))return 3;
 for(size_t n=0;n<sizeof(prefixed);n++)if(renderer.pushFrame(prefixed,n))return 4;
 for(size_t n=0;n<sizeof(pyrw);n++)if(renderer.pushFrame(pyrw,n))return 5;
 uint32_t random=0x61756469;std::vector<uint8_t> input;
 for(unsigned i=0;i<20000;i++){
   input.resize(i%513);
   for(auto &b:input){random=random*1664525u+1013904223u;b=uint8_t(random>>24);}
   if(input.size()>=4 && i%3==0)std::memcpy(input.data(),"PYRW",4);
   renderer.pushFrame(input.data(),input.size());
 }
 printf("PASS container fixtures, truncations and 20000 deterministic malformed frames\n");
 vk.GetQueryPoolResults=[](VkDevice,VkQueryPool,uint32_t,uint32_t,size_t,void* data,VkDeviceSize,VkQueryResultFlags){
   std::memcpy(data,queryTicks,sizeof(queryTicks));return VK_SUCCESS;};
 bool timestampsOk=true;
 for(unsigned bits:{36u,40u,48u,64u}) {
   uint64_t mask=UINT64_MAX>>(64-bits);
   setTimestampMask(renderer,mask,0);
   for(bool wrapDecode:{false,true}) {
     queryTicks[0]=wrapDecode?mask-5999:mask-15999;
     queryTicks[1]=wrapDecode?4000:mask-5999;
     queryTicks[2]=wrapDecode?16000:6000;
     renderer.stats={};renderer.queriesPending=true;renderer.readTimestamps();
     timestampsOk &= renderer.lastGpuDecodeUs==10 && renderer.stats.gpuDecodeUs==10 && renderer.stats.gpuDrawUs==12;
   }
 }
 printf("%s decode/draw timestamp wrap at 36, 40, 48 and 64 valid bits\n",timestampsOk?"PASS":"FAIL");
 // device remains null: these are fake handles, not native allocations.
 return invalidResets!=0 || submits!=2 || waits!=2 || !timestampsOk;
}
'''
with tempfile.TemporaryDirectory(prefix='pyrowave-fences-') as directory:
    work = pathlib.Path(directory)
    for name, source in STUBS.items():
        path = work/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    source = (subprocess.check_output(['git','show','HEAD:app/src/main/jni/pyrowave-renderer/pyrowave_renderer.cpp'],cwd=ROOT,text=True)
              if '--baseline' in sys.argv else (RENDERER/'pyrowave_renderer.cpp').read_text())
    (work/'renderer.cpp').write_text(source)
    (work/'test.cpp').write_text(TEST)
    subprocess.run(['g++','-std=c++17','-O1','-g','-fsanitize=address,undefined',
        '-ffunction-sections','-fdata-sections','-I'+str(work),'-I'+str(RENDERER),
        '-I'+str(RENDERER/'pyrowave/Granite/third_party/khronos/vulkan-headers/include'),
        str(work/'test.cpp'),'-Wl,--gc-sections','-ldl','-pthread','-o',str(work/'test')],check=True)
    raise SystemExit(subprocess.run([str(work/'test')]).returncode)
