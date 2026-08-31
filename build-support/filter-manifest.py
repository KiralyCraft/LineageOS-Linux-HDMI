#!/usr/bin/env python3
"""Create a build-only exact manifest without unrelated filesystem/test projects."""

import pathlib
import sys
import xml.etree.ElementTree as ET

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
root = ET.parse(source).getroot()

always_paths = {
    "android",
    "art",
    "bionic",
    "build/bazel",
    "build/bazel_common_rules",
    "build/blueprint",
    "build/make",
    "build/release",
    "build/soong",
    "external/abseil-cpp",
    "external/boringssl",
    "external/expat",
    "external/fmtlib",
    "external/go-cmp",
    "external/googletest",
    "external/jsoncpp",
    "external/kernel-headers",
    "external/libcap",
    "external/libcxx",
    "external/libcxxabi",
    "external/libdrm",
    "external/lz4",
    "external/pcre",
    "external/protobuf",
    "external/selinux",
    "external/zlib",
    "frameworks/av",
    "frameworks/native",
    "hardware/interfaces",
    "hardware/libhardware",
    "hardware/libhardware_legacy",
    "hardware/qcom-caf/common",
    "hardware/qcom-caf/sm8550/display",
    "kernel/sony/sm8550",
    "kernel/sony/sm8550-modules",
    "libnativehelper",
    "prebuilts/build-tools",
    "prebuilts/clang/host/linux-x86",
    "prebuilts/go/linux-x86",
    "prebuilts/jdk/jdk21",
    "prebuilts/misc",
    "prebuilts/ndk",
    "prebuilts/rust",
    "prebuilts/sdk",
    "prebuilts/tools-lineage",
    "system/core",
    "system/libbase",
    "system/libfmq",
    "system/libhidl",
    "system/libhwbinder",
    "system/libprocinfo",
    "system/libsysprop",
    "system/libvintf",
    "system/logging",
    "system/media",
    "system/sepolicy",
    "system/tools/aidl",
    "system/tools/hidl",
    "system/tools/sysprop",
    "vendor/qcom/opensource/commonsys/display",
    "vendor/qcom/opensource/commonsys-intf/display",
    "vendor/qcom/opensource/display",
}

for remote in root.findall("remote"):
    if remote.get("name") == "github" and remote.get("fetch") == "..":
        remote.set("fetch", "https://github.com")

kept = 0
for project in list(root.findall("project")):
    path = project.get("path", project.get("name", ""))
    if path not in always_paths:
        root.remove(project)
    else:
        kept += 1

ET.indent(root, space="  ")
output.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
print(f"selected {kept} exact projects")
