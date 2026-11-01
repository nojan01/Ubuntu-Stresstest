"""Herstellerneutraler GPU-Lasttest über Vulkan (AMD, Intel, NVIDIA).

Nutzt nur den Vulkan-Loader ``libvulkan.so.1`` (ctypes) und die Treiber der
Grafikkarte (Mesa RADV/ANV/NVK oder proprietär). Die Compute-Shader liegen als
vorübersetztes SPIR-V vor (``vulkan_spirv.py``). Je GPU läuft ein eigener
Prozess, der abwechselnd

* eine Rechenlast (FMA-Ketten, jedes Ergebnis muss identisch sein) und
* einen Speichertest (Muster in den Grafikspeicher schreiben und auf der GPU prüfen)

ausführt. Jeder Dispatch bleibt kurz (< 1 s), damit der Watchdog des
Grafiktreibers bei angeschlossenem Monitor nicht greift.

``--list`` gibt die gefundenen GPUs als JSON aus (Software-Renderer wie
llvmpipe werden ausgeblendet).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import json
import os
import signal
import sys
import time
from typing import Dict, List, Optional

try:
    from hardwaretest.tests.vulkan_spirv import SPIRV
except ImportError:  # direkt mit System-Python gestartet (siehe vulkan_runner.worker_command)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vulkan_spirv import SPIRV

# --- Vulkan-Konstanten (vulkan_core.h) ---------------------------------------
ST_APPLICATION_INFO = 0
ST_INSTANCE_CREATE_INFO = 1
ST_DEVICE_QUEUE_CREATE_INFO = 2
ST_DEVICE_CREATE_INFO = 3
ST_SUBMIT_INFO = 4
ST_MEMORY_ALLOCATE_INFO = 5
ST_FENCE_CREATE_INFO = 8
ST_BUFFER_CREATE_INFO = 12
ST_SHADER_MODULE_CREATE_INFO = 16
ST_PIPELINE_SHADER_STAGE_CREATE_INFO = 18
ST_COMPUTE_PIPELINE_CREATE_INFO = 29
ST_PIPELINE_LAYOUT_CREATE_INFO = 30
ST_DESCRIPTOR_SET_LAYOUT_CREATE_INFO = 32
ST_DESCRIPTOR_POOL_CREATE_INFO = 33
ST_DESCRIPTOR_SET_ALLOCATE_INFO = 34
ST_WRITE_DESCRIPTOR_SET = 35
ST_COMMAND_POOL_CREATE_INFO = 39
ST_COMMAND_BUFFER_ALLOCATE_INFO = 40
ST_COMMAND_BUFFER_BEGIN_INFO = 42
ST_MEMORY_BARRIER = 46
ST_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2 = 1000059006
ST_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT = 1000237000

VK_SUCCESS = 0
VK_TIMEOUT = 2
VK_ERROR_OUT_OF_HOST_MEMORY = -1
VK_ERROR_OUT_OF_DEVICE_MEMORY = -2
VK_ERROR_DEVICE_LOST = -4

DEVICE_TYPES = {0: "other", 1: "integrated", 2: "discrete", 3: "virtual", 4: "cpu"}
TYPE_INTEGRATED = 1
TYPE_CPU = 4
QUEUE_COMPUTE = 0x2
MEM_DEVICE_LOCAL = 0x1
MEM_HOST_VISIBLE = 0x2
MEM_HOST_COHERENT = 0x4
HEAP_DEVICE_LOCAL = 0x1
BUFFER_USAGE_STORAGE = 0x20
DESCRIPTOR_STORAGE_BUFFER = 7
STAGE_COMPUTE = 0x20
BIND_POINT_COMPUTE = 1
PIPELINE_STAGE_COMPUTE = 0x800
PIPELINE_STAGE_HOST = 0x4000
ACCESS_SHADER_READ = 0x20
ACCESS_SHADER_WRITE = 0x40
ACCESS_HOST_READ = 0x2000
ACCESS_HOST_WRITE = 0x4000
COMMAND_POOL_RESET_BUFFER = 0x2
COMMAND_BUFFER_ONE_TIME = 0x1
API_VERSION_1_1 = (1 << 22) | (1 << 12)

VENDORS = {0x1002: "AMD", 0x8086: "Intel", 0x10DE: "NVIDIA", 0x13B5: "ARM", 0x5143: "Qualcomm"}

BLOCK = 256
COMPUTE_GROUPS = 1024
MAX_GROUPS = 65535
CHUNK_BYTES = 256 * 1024 * 1024
FENCE_TIMEOUT_NS = 60 * 1_000_000_000
INTEGRATED_SHARE = 25  # höchstens 25 % des gemeinsam genutzten Arbeitsspeichers

c_u32 = ctypes.c_uint32
c_u64 = ctypes.c_uint64
c_ptr = ctypes.c_void_p
c_str = ctypes.c_char_p


def _struct(name: str, fields) -> type:
    return type(name, (ctypes.Structure,), {"_fields_": fields})


ApplicationInfo = _struct("ApplicationInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("pApplicationName", c_str), ("applicationVersion", c_u32),
    ("pEngineName", c_str), ("engineVersion", c_u32), ("apiVersion", c_u32)])
InstanceCreateInfo = _struct("InstanceCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("pApplicationInfo", c_ptr),
    ("enabledLayerCount", c_u32), ("ppEnabledLayerNames", c_ptr),
    ("enabledExtensionCount", c_u32), ("ppEnabledExtensionNames", c_ptr)])
QueueFamilyProperties = _struct("QueueFamilyProperties", [
    ("queueFlags", c_u32), ("queueCount", c_u32), ("timestampValidBits", c_u32),
    ("granularity", c_u32 * 3)])
MemoryType = _struct("MemoryType", [("propertyFlags", c_u32), ("heapIndex", c_u32)])
MemoryHeap = _struct("MemoryHeap", [("size", c_u64), ("flags", c_u32)])
MemoryProperties = _struct("MemoryProperties", [
    ("memoryTypeCount", c_u32), ("memoryTypes", MemoryType * 32),
    ("memoryHeapCount", c_u32), ("memoryHeaps", MemoryHeap * 16)])
MemoryProperties2 = _struct("MemoryProperties2", [
    ("sType", c_u32), ("pNext", c_ptr), ("memoryProperties", MemoryProperties)])
MemoryBudget = _struct("MemoryBudget", [
    ("sType", c_u32), ("pNext", c_ptr), ("heapBudget", c_u64 * 16), ("heapUsage", c_u64 * 16)])
ExtensionProperties = _struct("ExtensionProperties", [
    ("extensionName", ctypes.c_char * 256), ("specVersion", c_u32)])
DeviceQueueCreateInfo = _struct("DeviceQueueCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("queueFamilyIndex", c_u32),
    ("queueCount", c_u32), ("pQueuePriorities", ctypes.POINTER(ctypes.c_float))])
DeviceCreateInfo = _struct("DeviceCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("queueCreateInfoCount", c_u32),
    ("pQueueCreateInfos", c_ptr), ("enabledLayerCount", c_u32), ("ppEnabledLayerNames", c_ptr),
    ("enabledExtensionCount", c_u32), ("ppEnabledExtensionNames", c_ptr), ("pEnabledFeatures", c_ptr)])
BufferCreateInfo = _struct("BufferCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("size", c_u64), ("usage", c_u32),
    ("sharingMode", c_u32), ("queueFamilyIndexCount", c_u32), ("pQueueFamilyIndices", c_ptr)])
MemoryRequirements = _struct("MemoryRequirements", [
    ("size", c_u64), ("alignment", c_u64), ("memoryTypeBits", c_u32)])
MemoryAllocateInfo = _struct("MemoryAllocateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("allocationSize", c_u64), ("memoryTypeIndex", c_u32)])
ShaderModuleCreateInfo = _struct("ShaderModuleCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("codeSize", ctypes.c_size_t), ("pCode", c_ptr)])
DescriptorSetLayoutBinding = _struct("DescriptorSetLayoutBinding", [
    ("binding", c_u32), ("descriptorType", c_u32), ("descriptorCount", c_u32),
    ("stageFlags", c_u32), ("pImmutableSamplers", c_ptr)])
DescriptorSetLayoutCreateInfo = _struct("DescriptorSetLayoutCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("bindingCount", c_u32), ("pBindings", c_ptr)])
PushConstantRange = _struct("PushConstantRange", [("stageFlags", c_u32), ("offset", c_u32), ("size", c_u32)])
PipelineLayoutCreateInfo = _struct("PipelineLayoutCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("setLayoutCount", c_u32), ("pSetLayouts", c_ptr),
    ("pushConstantRangeCount", c_u32), ("pPushConstantRanges", c_ptr)])
PipelineShaderStageCreateInfo = _struct("PipelineShaderStageCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("stage", c_u32), ("module", c_u64),
    ("pName", c_str), ("pSpecializationInfo", c_ptr)])
ComputePipelineCreateInfo = _struct("ComputePipelineCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("stage", PipelineShaderStageCreateInfo),
    ("layout", c_u64), ("basePipelineHandle", c_u64), ("basePipelineIndex", ctypes.c_int32)])
DescriptorPoolSize = _struct("DescriptorPoolSize", [("type", c_u32), ("descriptorCount", c_u32)])
DescriptorPoolCreateInfo = _struct("DescriptorPoolCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("maxSets", c_u32),
    ("poolSizeCount", c_u32), ("pPoolSizes", c_ptr)])
DescriptorSetAllocateInfo = _struct("DescriptorSetAllocateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("descriptorPool", c_u64), ("descriptorSetCount", c_u32),
    ("pSetLayouts", c_ptr)])
DescriptorBufferInfo = _struct("DescriptorBufferInfo", [("buffer", c_u64), ("offset", c_u64), ("range", c_u64)])
WriteDescriptorSet = _struct("WriteDescriptorSet", [
    ("sType", c_u32), ("pNext", c_ptr), ("dstSet", c_u64), ("dstBinding", c_u32),
    ("dstArrayElement", c_u32), ("descriptorCount", c_u32), ("descriptorType", c_u32),
    ("pImageInfo", c_ptr), ("pBufferInfo", c_ptr), ("pTexelBufferView", c_ptr)])
CommandPoolCreateInfo = _struct("CommandPoolCreateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("queueFamilyIndex", c_u32)])
CommandBufferAllocateInfo = _struct("CommandBufferAllocateInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("commandPool", c_u64), ("level", c_u32),
    ("commandBufferCount", c_u32)])
CommandBufferBeginInfo = _struct("CommandBufferBeginInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32), ("pInheritanceInfo", c_ptr)])
MemoryBarrier = _struct("MemoryBarrier", [
    ("sType", c_u32), ("pNext", c_ptr), ("srcAccessMask", c_u32), ("dstAccessMask", c_u32)])
SubmitInfo = _struct("SubmitInfo", [
    ("sType", c_u32), ("pNext", c_ptr), ("waitSemaphoreCount", c_u32), ("pWaitSemaphores", c_ptr),
    ("pWaitDstStageMask", c_ptr), ("commandBufferCount", c_u32), ("pCommandBuffers", c_ptr),
    ("signalSemaphoreCount", c_u32), ("pSignalSemaphores", c_ptr)])
FenceCreateInfo = _struct("FenceCreateInfo", [("sType", c_u32), ("pNext", c_ptr), ("flags", c_u32)])
PushConstants = _struct("PushConstants", [("pat", c_u32), ("base", c_u32), ("n", c_u32), ("iters", c_u32)])

# VkPhysicalDeviceProperties wird als Rohbytes gelesen (Offsets laut Spezifikation).
PROPS_SIZE = 1024
OFF_API, OFF_DRIVER, OFF_VENDOR, OFF_DEVICE, OFF_TYPE, OFF_NAME = 0, 4, 8, 12, 16, 20
OFF_MAX_STORAGE_RANGE = 296 + 28

_V = c_ptr  # Handles von Instanz, Gerät, Queue und Command-Buffer sind Zeiger
_FUNCTIONS = {
    "vkCreateInstance": (c_ptr, c_ptr, c_ptr),
    "vkDestroyInstance": (_V, c_ptr),
    "vkEnumeratePhysicalDevices": (_V, c_ptr, c_ptr),
    "vkGetPhysicalDeviceProperties": (_V, c_ptr),
    "vkGetPhysicalDeviceMemoryProperties": (_V, c_ptr),
    "vkGetPhysicalDeviceMemoryProperties2": (_V, c_ptr),
    "vkGetPhysicalDeviceQueueFamilyProperties": (_V, c_ptr, c_ptr),
    "vkEnumerateDeviceExtensionProperties": (_V, c_str, c_ptr, c_ptr),
    "vkCreateDevice": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyDevice": (_V, c_ptr),
    "vkDeviceWaitIdle": (_V,),
    "vkGetDeviceQueue": (_V, c_u32, c_u32, c_ptr),
    "vkCreateBuffer": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyBuffer": (_V, c_u64, c_ptr),
    "vkGetBufferMemoryRequirements": (_V, c_u64, c_ptr),
    "vkAllocateMemory": (_V, c_ptr, c_ptr, c_ptr),
    "vkFreeMemory": (_V, c_u64, c_ptr),
    "vkBindBufferMemory": (_V, c_u64, c_u64, c_u64),
    "vkMapMemory": (_V, c_u64, c_u64, c_u64, c_u32, c_ptr),
    "vkCreateShaderModule": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyShaderModule": (_V, c_u64, c_ptr),
    "vkCreateDescriptorSetLayout": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyDescriptorSetLayout": (_V, c_u64, c_ptr),
    "vkCreatePipelineLayout": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyPipelineLayout": (_V, c_u64, c_ptr),
    "vkCreateComputePipelines": (_V, c_u64, c_u32, c_ptr, c_ptr, c_ptr),
    "vkDestroyPipeline": (_V, c_u64, c_ptr),
    "vkCreateDescriptorPool": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyDescriptorPool": (_V, c_u64, c_ptr),
    "vkAllocateDescriptorSets": (_V, c_ptr, c_ptr),
    "vkUpdateDescriptorSets": (_V, c_u32, c_ptr, c_u32, c_ptr),
    "vkCreateCommandPool": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyCommandPool": (_V, c_u64, c_ptr),
    "vkAllocateCommandBuffers": (_V, c_ptr, c_ptr),
    "vkBeginCommandBuffer": (_V, c_ptr),
    "vkEndCommandBuffer": (_V,),
    "vkResetCommandBuffer": (_V, c_u32),
    "vkCmdBindPipeline": (_V, c_u32, c_u64),
    "vkCmdBindDescriptorSets": (_V, c_u32, c_u64, c_u32, c_u32, c_ptr, c_u32, c_ptr),
    "vkCmdPushConstants": (_V, c_u64, c_u32, c_u32, c_u32, c_ptr),
    "vkCmdDispatch": (_V, c_u32, c_u32, c_u32),
    "vkCmdPipelineBarrier": (_V, c_u32, c_u32, c_u32, c_u32, c_ptr, c_u32, c_ptr, c_u32, c_ptr),
    "vkCreateFence": (_V, c_ptr, c_ptr, c_ptr),
    "vkDestroyFence": (_V, c_u64, c_ptr),
    "vkResetFences": (_V, c_u32, c_ptr),
    "vkWaitForFences": (_V, c_u32, c_ptr, c_u32, c_u64),
    "vkQueueSubmit": (_V, c_u32, c_ptr, c_u64),
}
_VOID = {name for name in _FUNCTIONS if name.startswith(("vkDestroy", "vkGet", "vkCmd", "vkFree", "vkUpdate"))}


class VulkanError(RuntimeError):
    def __init__(self, name: str, result: int) -> None:
        text = {VK_ERROR_DEVICE_LOST: "Gerät verloren (GPU-Absturz/Reset)",
                VK_ERROR_OUT_OF_DEVICE_MEMORY: "kein Grafikspeicher frei",
                VK_ERROR_OUT_OF_HOST_MEMORY: "kein Arbeitsspeicher frei",
                VK_TIMEOUT: "Zeitüberschreitung, GPU antwortet nicht"}.get(result, "")
        super().__init__(f"{name} fehlgeschlagen ({result}) {text}".strip())
        self.result = result


class Vulkan:
    """Minimaler ctypes-Wrapper um den Vulkan-Loader."""

    def __init__(self, lib=None) -> None:
        self.lib = lib if lib is not None else ctypes.CDLL("libvulkan.so.1")
        for name, argtypes in _FUNCTIONS.items():
            function = getattr(self.lib, name, None)
            if function is None:
                continue
            function.argtypes = list(argtypes)
            function.restype = None if name in _VOID else ctypes.c_int32

    def call(self, name: str, *args) -> int:
        result = getattr(self.lib, name)(*args)
        if result is not None and result < 0:
            raise VulkanError(name, result)
        return result or 0

    def void(self, name: str, *args) -> None:
        getattr(self.lib, name)(*args)


def _say(gpu: int, text: str) -> None:
    sys.stdout.write(f"GPU {gpu}: {text}\n")
    sys.stdout.flush()


def _create_instance(vk: Vulkan) -> ctypes.c_void_p:
    app = ApplicationInfo(ST_APPLICATION_INFO, None, b"Hardwaretest", 1, b"Hardwaretest", 1, API_VERSION_1_1)
    info = InstanceCreateInfo(ST_INSTANCE_CREATE_INFO, None, 0, ctypes.cast(ctypes.pointer(app), c_ptr), 0, None, 0, None)
    instance = ctypes.c_void_p()
    vk.call("vkCreateInstance", ctypes.byref(info), None, ctypes.byref(instance))
    return instance


def _physical_devices(vk: Vulkan, instance) -> List[ctypes.c_void_p]:
    count = c_u32()
    vk.call("vkEnumeratePhysicalDevices", instance, ctypes.byref(count), None)
    devices = (ctypes.c_void_p * count.value)()
    if count.value:
        vk.call("vkEnumeratePhysicalDevices", instance, ctypes.byref(count), devices)
    return [ctypes.c_void_p(devices[i]) for i in range(count.value)]


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset:offset + 4], "little")


def _version(value: int) -> str:
    return f"{(value >> 22) & 0x7F}.{(value >> 12) & 0x3FF}.{value & 0xFFF}"


def _has_extension(vk: Vulkan, device, name: bytes) -> bool:
    count = c_u32()
    try:
        vk.call("vkEnumerateDeviceExtensionProperties", device, None, ctypes.byref(count), None)
        props = (ExtensionProperties * count.value)()
        vk.call("vkEnumerateDeviceExtensionProperties", device, None, ctypes.byref(count), props)
    except VulkanError:
        return False
    return any(prop.extensionName == name for prop in props)


def describe_device(vk: Vulkan, device, index: int) -> Dict:
    raw_buffer = ctypes.create_string_buffer(PROPS_SIZE)
    vk.void("vkGetPhysicalDeviceProperties", device, raw_buffer)
    raw = raw_buffer.raw
    memory = MemoryProperties()
    vk.void("vkGetPhysicalDeviceMemoryProperties", device, ctypes.byref(memory))
    local = [memory.memoryHeaps[i].size for i in range(memory.memoryHeapCount)
             if memory.memoryHeaps[i].flags & HEAP_DEVICE_LOCAL]
    vendor_id = _u32(raw, OFF_VENDOR)
    return {
        "index": index,
        "name": raw[OFF_NAME:OFF_NAME + 256].split(b"\0", 1)[0].decode(errors="replace"),
        "type": DEVICE_TYPES.get(_u32(raw, OFF_TYPE), "other"),
        "vendor": VENDORS.get(vendor_id, f"0x{vendor_id:04x}"),
        "vendor_id": vendor_id,
        "device_id": _u32(raw, OFF_DEVICE),
        "api": _version(_u32(raw, OFF_API)),
        "memory": max(local, default=0),
        "max_storage_range": _u32(raw, OFF_MAX_STORAGE_RANGE),
    }


def list_gpus(vk: Optional[Vulkan] = None) -> List[Dict]:
    """Alle Vulkan-GPUs ohne Software-Renderer (llvmpipe/lavapipe)."""
    vk = vk or Vulkan()
    instance = _create_instance(vk)
    try:
        gpus = [describe_device(vk, device, index) for index, device in enumerate(_physical_devices(vk, instance))]
    finally:
        vk.void("vkDestroyInstance", instance, None)
    return [gpu for gpu in gpus if gpu["type"] != DEVICE_TYPES[TYPE_CPU]]


def test_size(free: int, heap: int, integrated: bool, memory_percent: int) -> int:
    """Größe des Speichertests: Anteil des freien Speichers, bei iGPUs höchstens 25 % des Heaps."""
    size = free * memory_percent // 100
    if integrated:
        size = min(size, heap * INTEGRATED_SHARE // 100)
    return max(0, size) & ~(1024 * 1024 - 1)


class GpuTest:
    """Ressourcen für eine GPU; ``close`` gibt alles wieder frei."""

    def __init__(self, vk: Vulkan, gpu: int) -> None:
        self.vk = vk
        self.gpu = gpu
        self.instance = _create_instance(vk)
        self.device = None
        self.objects: List[tuple] = []
        self.memories: List[int] = []
        self.chunks: List[tuple] = []  # (buffer, words, descriptor set)
        devices = _physical_devices(vk, self.instance)
        if gpu >= len(devices):
            raise RuntimeError(f"GPU {gpu} nicht gefunden ({len(devices)} Vulkan-Geräte)")
        self.physical = devices[gpu]
        self.info = describe_device(vk, self.physical, gpu)
        if self.info["type"] == DEVICE_TYPES[TYPE_CPU]:
            raise RuntimeError(f"{self.info['name']} ist ein Software-Renderer, keine GPU")
        self.memory = MemoryProperties()
        vk.void("vkGetPhysicalDeviceMemoryProperties", self.physical, ctypes.byref(self.memory))
        self._create_device()

    # --- Aufbau ---------------------------------------------------------------
    def _create_device(self) -> None:
        vk = self.vk
        count = c_u32()
        vk.void("vkGetPhysicalDeviceQueueFamilyProperties", self.physical, ctypes.byref(count), None)
        families = (QueueFamilyProperties * count.value)()
        vk.void("vkGetPhysicalDeviceQueueFamilyProperties", self.physical, ctypes.byref(count), families)
        compute = [i for i in range(count.value) if families[i].queueFlags & QUEUE_COMPUTE]
        if not compute:
            raise RuntimeError("keine Compute-Queue")
        self.family = compute[0]
        priority = ctypes.c_float(1.0)
        queue_info = DeviceQueueCreateInfo(ST_DEVICE_QUEUE_CREATE_INFO, None, 0, self.family, 1, ctypes.pointer(priority))
        info = DeviceCreateInfo(ST_DEVICE_CREATE_INFO, None, 0, 1, ctypes.cast(ctypes.pointer(queue_info), c_ptr),
                                0, None, 0, None, None)
        self.device = ctypes.c_void_p()
        vk.call("vkCreateDevice", self.physical, ctypes.byref(info), None, ctypes.byref(self.device))
        self.queue = ctypes.c_void_p()
        vk.void("vkGetDeviceQueue", self.device, self.family, 0, ctypes.byref(self.queue))

        pool_info = CommandPoolCreateInfo(ST_COMMAND_POOL_CREATE_INFO, None, COMMAND_POOL_RESET_BUFFER, self.family)
        self.pool = self._create("vkCreateCommandPool", "vkDestroyCommandPool", pool_info)
        alloc = CommandBufferAllocateInfo(ST_COMMAND_BUFFER_ALLOCATE_INFO, None, self.pool, 0, 1)
        self.command = ctypes.c_void_p()
        vk.call("vkAllocateCommandBuffers", self.device, ctypes.byref(alloc), ctypes.byref(self.command))
        self.fence = self._create("vkCreateFence", "vkDestroyFence", FenceCreateInfo(ST_FENCE_CREATE_INFO, None, 0))

        bindings = (DescriptorSetLayoutBinding * 2)(
            DescriptorSetLayoutBinding(0, DESCRIPTOR_STORAGE_BUFFER, 1, STAGE_COMPUTE, None),
            DescriptorSetLayoutBinding(1, DESCRIPTOR_STORAGE_BUFFER, 1, STAGE_COMPUTE, None))
        self.set_layout = self._create(
            "vkCreateDescriptorSetLayout", "vkDestroyDescriptorSetLayout",
            DescriptorSetLayoutCreateInfo(ST_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, None, 0, 2,
                                          ctypes.cast(bindings, c_ptr)))
        set_layouts = c_u64(self.set_layout)
        push = PushConstantRange(STAGE_COMPUTE, 0, ctypes.sizeof(PushConstants))
        self.layout = self._create(
            "vkCreatePipelineLayout", "vkDestroyPipelineLayout",
            PipelineLayoutCreateInfo(ST_PIPELINE_LAYOUT_CREATE_INFO, None, 0, 1, ctypes.addressof(set_layouts),
                                     1, ctypes.addressof(push)))
        self.pipelines = {name: self._pipeline(name) for name in ("fill", "verify", "compute")}

    def _create(self, create: str, destroy: str, info) -> int:
        handle = c_u64()
        self.vk.call(create, self.device, ctypes.byref(info), None, ctypes.byref(handle))
        self.objects.append((destroy, handle.value))
        return handle.value

    def _pipeline(self, name: str) -> int:
        code = base64.b64decode("".join(SPIRV[name]))
        words = (c_u32 * (len(code) // 4)).from_buffer_copy(code)
        module = self._create("vkCreateShaderModule", "vkDestroyShaderModule",
                              ShaderModuleCreateInfo(ST_SHADER_MODULE_CREATE_INFO, None, 0, len(code),
                                                     ctypes.addressof(words)))
        stage = PipelineShaderStageCreateInfo(ST_PIPELINE_SHADER_STAGE_CREATE_INFO, None, 0, STAGE_COMPUTE,
                                              module, b"main", None)
        info = ComputePipelineCreateInfo(ST_COMPUTE_PIPELINE_CREATE_INFO, None, 0, stage, self.layout, 0, -1)
        handle = c_u64()
        self.vk.call("vkCreateComputePipelines", self.device, 0, 1, ctypes.byref(info), None, ctypes.byref(handle))
        self.objects.append(("vkDestroyPipeline", handle.value))
        return handle.value

    def _memory_type(self, bits: int, required: int, avoid: int = 0) -> int:
        best, best_size = -1, -1
        for i in range(self.memory.memoryTypeCount):
            flags = self.memory.memoryTypes[i].propertyFlags
            if not bits & (1 << i) or flags & required != required:
                continue
            size = self.memory.memoryHeaps[self.memory.memoryTypes[i].heapIndex].size
            # Bevorzugt Typen ohne ``avoid``-Flags (z. B. kleines BAR-Fenster), dann den größten Heap.
            key = (0 if flags & avoid else 1, size)
            if best < 0 or key > best_size:
                best, best_size = i, key
        if best < 0:
            raise RuntimeError("kein passender Speichertyp")
        return best

    def _buffer(self, size: int, required: int, avoid: int = 0) -> tuple:
        vk = self.vk
        info = BufferCreateInfo(ST_BUFFER_CREATE_INFO, None, 0, size, BUFFER_USAGE_STORAGE, 0, 0, None)
        buffer = c_u64()
        vk.call("vkCreateBuffer", self.device, ctypes.byref(info), None, ctypes.byref(buffer))
        requirements = MemoryRequirements()
        vk.void("vkGetBufferMemoryRequirements", self.device, buffer, ctypes.byref(requirements))
        alloc = MemoryAllocateInfo(ST_MEMORY_ALLOCATE_INFO, None, requirements.size,
                                   self._memory_type(requirements.memoryTypeBits, required, avoid))
        memory = c_u64()
        try:
            vk.call("vkAllocateMemory", self.device, ctypes.byref(alloc), None, ctypes.byref(memory))
        except VulkanError:
            vk.void("vkDestroyBuffer", self.device, buffer, None)
            raise
        self.objects.append(("vkDestroyBuffer", buffer.value))
        self.memories.append(memory.value)
        vk.call("vkBindBufferMemory", self.device, buffer, memory, 0)
        return buffer.value, memory.value

    def _host_buffer(self, size: int):
        buffer, memory = self._buffer(size, MEM_HOST_VISIBLE | MEM_HOST_COHERENT)
        pointer = ctypes.c_void_p()
        self.vk.call("vkMapMemory", self.device, memory, 0, size, 0, ctypes.byref(pointer))
        return buffer, pointer.value

    def free_memory(self) -> tuple:
        """(freier Speicher, Heap-Größe) des größten gerätelokalen Heaps."""
        heaps = [i for i in range(self.memory.memoryHeapCount) if self.memory.memoryHeaps[i].flags & HEAP_DEVICE_LOCAL]
        if not heaps:
            heaps = list(range(self.memory.memoryHeapCount))
        heap = max(heaps, key=lambda i: self.memory.memoryHeaps[i].size)
        size = self.memory.memoryHeaps[heap].size
        free = size * 9 // 10
        if _has_extension(self.vk, self.physical, b"VK_EXT_memory_budget") and \
                getattr(self.vk.lib, "vkGetPhysicalDeviceMemoryProperties2", None) is not None:
            budget = MemoryBudget(ST_PHYSICAL_DEVICE_MEMORY_BUDGET_PROPERTIES_EXT, None)
            props = MemoryProperties2(ST_PHYSICAL_DEVICE_MEMORY_PROPERTIES_2, ctypes.cast(ctypes.pointer(budget), c_ptr))
            self.vk.void("vkGetPhysicalDeviceMemoryProperties2", self.physical, ctypes.byref(props))
            if budget.heapBudget[heap]:
                free = max(0, budget.heapBudget[heap] - budget.heapUsage[heap])
        return free, size

    def allocate(self, size: int) -> int:
        """Legt Speicherblöcke bis ``size`` Bytes an; liefert die tatsächlich belegte Größe."""
        self.errors_buffer, self.errors_ptr = self._host_buffer(4)
        self.threads = COMPUTE_GROUPS * BLOCK
        self.out_buffer, self.out_ptr = self._host_buffer(self.threads * 4)
        chunk = CHUNK_BYTES
        if self.info["max_storage_range"]:
            chunk = min(chunk, self.info["max_storage_range"] & ~(1024 * 1024 - 1))
        buffers = []
        remaining = size
        while remaining >= 1024 * 1024:
            part = min(chunk, remaining)
            try:
                buffer, _memory = self._buffer(part, MEM_DEVICE_LOCAL, avoid=MEM_HOST_VISIBLE)
            except VulkanError as exc:
                if exc.result in (VK_ERROR_OUT_OF_DEVICE_MEMORY, VK_ERROR_OUT_OF_HOST_MEMORY) and buffers:
                    break
                raise
            buffers.append((buffer, part))
            remaining -= part

        sets_needed = len(buffers) + 1
        pool_size = DescriptorPoolSize(DESCRIPTOR_STORAGE_BUFFER, 2 * sets_needed)
        self.descriptor_pool = self._create(
            "vkCreateDescriptorPool", "vkDestroyDescriptorPool",
            DescriptorPoolCreateInfo(ST_DESCRIPTOR_POOL_CREATE_INFO, None, 0, sets_needed, 1,
                                     ctypes.addressof(pool_size)))
        self.compute_set = self._descriptor_set(self.out_buffer, self.threads * 4)
        self.chunks = [(buffer, part // 4, self._descriptor_set(buffer, part)) for buffer, part in buffers]
        return sum(part for _buffer, part in buffers)

    def _descriptor_set(self, buffer: int, size: int) -> int:
        layouts = c_u64(self.set_layout)
        info = DescriptorSetAllocateInfo(ST_DESCRIPTOR_SET_ALLOCATE_INFO, None, self.descriptor_pool, 1,
                                         ctypes.addressof(layouts))
        handle = c_u64()
        self.vk.call("vkAllocateDescriptorSets", self.device, ctypes.byref(info), ctypes.byref(handle))
        infos = (DescriptorBufferInfo * 2)(DescriptorBufferInfo(buffer, 0, size),
                                           DescriptorBufferInfo(self.errors_buffer, 0, 4))
        writes = (WriteDescriptorSet * 2)(*[
            WriteDescriptorSet(ST_WRITE_DESCRIPTOR_SET, None, handle.value, binding, 0, 1, DESCRIPTOR_STORAGE_BUFFER,
                               None, ctypes.addressof(infos) + binding * ctypes.sizeof(DescriptorBufferInfo), None)
            for binding in (0, 1)])
        self.vk.void("vkUpdateDescriptorSets", self.device, 2, writes, 0, None)
        return handle.value

    # --- Ausführung -----------------------------------------------------------
    def dispatch(self, pipeline: str, descriptor_set: int, groups: int, push: PushConstants) -> None:
        vk = self.vk
        command = self.command
        vk.call("vkResetCommandBuffer", command, 0)
        begin = CommandBufferBeginInfo(ST_COMMAND_BUFFER_BEGIN_INFO, None, COMMAND_BUFFER_ONE_TIME, None)
        vk.call("vkBeginCommandBuffer", command, ctypes.byref(begin))
        before = MemoryBarrier(ST_MEMORY_BARRIER, None, ACCESS_SHADER_WRITE | ACCESS_HOST_WRITE,
                               ACCESS_SHADER_READ | ACCESS_SHADER_WRITE)
        vk.void("vkCmdPipelineBarrier", command, PIPELINE_STAGE_COMPUTE | PIPELINE_STAGE_HOST, PIPELINE_STAGE_COMPUTE,
                0, 1, ctypes.byref(before), 0, None, 0, None)
        vk.void("vkCmdBindPipeline", command, BIND_POINT_COMPUTE, self.pipelines[pipeline])
        sets = c_u64(descriptor_set)
        vk.void("vkCmdBindDescriptorSets", command, BIND_POINT_COMPUTE, self.layout, 0, 1, ctypes.byref(sets), 0, None)
        vk.void("vkCmdPushConstants", command, self.layout, STAGE_COMPUTE, 0, ctypes.sizeof(push), ctypes.byref(push))
        vk.void("vkCmdDispatch", command, groups, 1, 1)
        after = MemoryBarrier(ST_MEMORY_BARRIER, None, ACCESS_SHADER_WRITE, ACCESS_HOST_READ)
        vk.void("vkCmdPipelineBarrier", command, PIPELINE_STAGE_COMPUTE, PIPELINE_STAGE_HOST,
                0, 1, ctypes.byref(after), 0, None, 0, None)
        vk.call("vkEndCommandBuffer", command)
        commands = ctypes.c_void_p(command.value)
        submit = SubmitInfo(ST_SUBMIT_INFO, None, 0, None, None, 1, ctypes.addressof(commands), 0, None)
        fence = c_u64(self.fence)
        vk.call("vkResetFences", self.device, 1, ctypes.byref(fence))
        vk.call("vkQueueSubmit", self.queue, 1, ctypes.byref(submit), self.fence)
        if vk.call("vkWaitForFences", self.device, 1, ctypes.byref(fence), 1, FENCE_TIMEOUT_NS) == VK_TIMEOUT:
            raise VulkanError("vkWaitForFences", VK_TIMEOUT)

    def compute(self, iterations: int) -> int:
        """Rechenlast; Rückgabe: Anzahl Threads mit abweichendem Ergebnis."""
        self.dispatch("compute", self.compute_set, COMPUTE_GROUPS, PushConstants(0, 0, 0, iterations))
        raw = ctypes.string_at(self.out_ptr, self.threads * 4)
        if raw == raw[:4] * self.threads:
            return 0
        values = memoryview(raw).cast("I")
        return sum(1 for value in values if value != values[0])

    def memory_test(self, pattern: int) -> int:
        """Füllt alle Blöcke und prüft sie danach; Rückgabe: Anzahl falscher Wörter."""
        ctypes.memset(self.errors_ptr, 0, 4)
        for step in ("fill", "verify"):
            base = 0
            for _buffer, words, descriptor_set in self.chunks:
                groups = min(MAX_GROUPS, max(1, (words + BLOCK - 1) // BLOCK))
                self.dispatch(step, descriptor_set, groups,
                              PushConstants(pattern & 0xFFFFFFFF, base & 0xFFFFFFFF, words, 0))
                base += words
        return c_u32.from_address(self.errors_ptr).value

    def close(self) -> None:
        vk = self.vk
        if self.device is not None:
            vk.lib.vkDeviceWaitIdle(self.device)
            for destroy, handle in reversed(self.objects):
                vk.void(destroy, self.device, handle, None)
            for memory in self.memories:
                vk.void("vkFreeMemory", self.device, memory, None)
            vk.void("vkDestroyDevice", self.device, None)
            self.device = None
        vk.void("vkDestroyInstance", self.instance, None)


def run_gpu(gpu: int, duration: float, memory_percent: int, vk: Optional[Vulkan] = None) -> int:
    """Belastet eine GPU für ``duration`` Sekunden. Rückgabe: Anzahl Fehler."""
    test = GpuTest(vk or Vulkan(), gpu)
    errors = 0
    cycle = 0
    checked = 0
    try:
        free, heap = test.free_memory()
        integrated = test.info["type"] == DEVICE_TYPES[TYPE_INTEGRATED]
        wanted = test_size(free, heap, integrated, memory_percent)
        size = test.allocate(wanted)
        kind = "integriert, gemeinsamer Arbeitsspeicher" if integrated else "Grafikspeicher"
        _say(gpu, f"{test.info['name']} ({test.info['vendor']}, Vulkan {test.info['api']}), "
                  f"teste {size / 2**30:.1f} GiB von {heap / 2**30:.1f} GiB {kind}")
        if size < wanted:
            _say(gpu, f"Hinweis: nur {size / 2**30:.1f} von {wanted / 2**30:.1f} GiB belegbar")

        iterations = 1 << 14
        deadline = time.monotonic() + duration
        next_report = time.monotonic() + 10
        while time.monotonic() < deadline:
            cycle += 1
            # Rechenlast: Laufzeit je Dispatch auf ca. 0,1-0,4 s einregeln.
            start = time.monotonic()
            wrong = test.compute(iterations)
            elapsed = time.monotonic() - start
            if elapsed < 0.1 and iterations < (1 << 28):
                iterations *= 2
            elif elapsed > 0.4 and iterations > 1024:
                iterations //= 2
            if wrong:
                errors += wrong
                _say(gpu, f"FEHLER: Rechenfehler in {wrong} von {test.threads} Threads (Zyklus {cycle})")

            if test.chunks:
                bad = test.memory_test(cycle * 0x5BD1E995)
                checked += size
                if bad:
                    errors += bad
                    _say(gpu, f"FEHLER: {bad} Speicherfehler (Zyklus {cycle})")

            if time.monotonic() >= next_report:
                next_report += 10
                _say(gpu, f"Zyklus {cycle}, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    finally:
        test.close()
    _say(gpu, f"Ende nach {cycle} Zyklen, {checked / 2**30:.0f} GiB geprüft, Fehler {errors}")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="GPUs als JSON ausgeben")
    parser.add_argument("--gpus", help="Vulkan-Geräteindizes, z. B. 0,1")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--memory-percent", type=int, default=80)
    args = parser.parse_args(argv)
    if args.list:
        try:
            print(json.dumps(list_gpus()), flush=True)
        except (OSError, VulkanError) as exc:
            print(json.dumps({"error": str(exc)}), flush=True)
            return 1
        return 0
    if not args.gpus:
        parser.error("--gpus oder --list angeben")
    gpus = [int(part) for part in args.gpus.split(",") if part.strip()]
    percent = max(10, min(95, args.memory_percent))

    # Je GPU ein Prozess; fork erfolgt vor dem Laden des Vulkan-Treibers.
    children: Dict[int, int] = {}
    parent = os.getpid()

    def forward(signum, _frame):
        for child in children:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGTERM)
        os._exit(128 + signum)

    for gpu in gpus:
        pid = os.fork()
        if pid == 0:
            code = 2
            try:
                # Endet der Elternprozess (z. B. per SIGKILL), soll die GPU-Last mitenden.
                with contextlib.suppress(OSError, AttributeError):
                    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
                if os.getppid() != parent:
                    os._exit(2)
                code = 1 if run_gpu(gpu, args.duration, percent) else 0
            except Exception as exc:
                _say(gpu, f"FEHLER: {exc}")
            finally:
                sys.stdout.flush()
                os._exit(code)
        children[pid] = gpu
    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    failed = []
    for pid, gpu in children.items():
        _, status = os.waitpid(pid, 0)
        if os.waitstatus_to_exitcode(status) != 0:
            failed.append(gpu)
    if failed:
        print(f"ERGEBNIS: FEHLGESCHLAGEN (GPU {', '.join(map(str, failed))})", flush=True)
        return 1
    print("ERGEBNIS: BESTANDEN", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
