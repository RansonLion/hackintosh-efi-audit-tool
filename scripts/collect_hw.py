#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_hw.py — 跨平台硬件采集（只读）

用途：在引导流程的 Phase 1 帮使用者把硬件事实一次性采集出来，
      免去"自己不知道该用什么命令"的问题。输出可直接填进
      templates/intake-checklist.md。

平台支持：
  macOS   — sysctl / system_profiler / ioreg / pmset
  Windows — PowerShell CIM（无需管理员）
  Linux   — lspci / dmidecode / /proc/cpuinfo（dmidecode 通常需 root）

⚠️ 只读：不写任何文件、不改任何设置。
⚠️ 注意：BIOS 设置（CFG Lock / DVMT / Secure Boot 等）**任何脚本都读不到**，
   必须人工进 BIOS 目视确认——本脚本会提示这一点。

用法：
  python3 collect_hw.py            # 自动识别平台
  python3 collect_hw.py --json     # 机器可读输出
"""

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys

INFO = "ℹ️ "


def run(cmd, shell=False):
    """执行命令，失败返回 None（不抛异常）。"""
    try:
        p = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=60)
        return p.stdout.strip() or None
    except Exception:
        return None


def first_match(text, *patterns):
    if not text:
        return None
    for line in text.splitlines():
        for p in patterns:
            m = re.search(p, line, re.I)
            if m:
                return line.strip()
    return None


def sh(*cmds):
    """依次尝试命令，返回第一个有输出的。"""
    for c in cmds:
        out = run(c, shell=True)
        if out:
            return out
    return None


# ---------------------------------------------------------------- macOS

def collect_macos():
    d = {}
    d["system"] = run(["sw_vers", "-productVersion"])
    d["model_identifier"] = run(["sysctl", "-n", "hw.model"])
    d["cpu"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
    disp = sh("system_profiler SPDisplaysDataType")
    d["gpu_raw"] = disp
    d["igpu"] = first_match(disp, r"Chipset Model:")
    d["metal"] = first_match(disp, r"Metal Support|Metal:")
    d["vram"] = first_match(disp, r"VRAM")
    audio = sh("system_profiler SPAudioDataType")
    d["audio"] = "\n".join(l.strip() for l in (audio or "").splitlines()
                           if re.search(r"(Device|Manufacturer|Input|Output|Audio ID)", l)) or None
    net = sh("system_profiler SPNetworkDataType")
    d["nic"] = "\n".join(l.strip() for l in (net or "").splitlines()
                         if re.search(r"(Vendor|Type:|BSD Device|hardware)", l, re.I)) or None
    cam = sh("system_profiler SPCameraDataType")
    d["camera"] = first_match(cam, r"Model ID|Unique ID")
    power = sh("system_profiler SPPowerDataType")
    d["battery"] = "\n".join(l.strip() for l in (power or "").splitlines()
                             if re.search(r"(Cycle Count|Condition|Full Charge|Charge)", l)) or None
    ioreg = sh("ioreg -l | grep -i -E 'VoodooI2C|VoodooPS2|ApplePS2|AppleACPIEC' | head -10")
    d["input_driver"] = ioreg
    layout = sh("ioreg -rxn HDEF | grep -i layout")
    d["audio_layout"] = layout
    assertions = sh("pmset -g assertions | grep -i prevent | head -10")
    d["sleep_blockers"] = assertions
    slog = sh("pmset -g log | grep -E 'Total Sleep/Wakes' | tail -3")
    d["sleep_count"] = slog
    usb = sh("system_profiler SPUSBDataType | grep -E 'Speed|Manufacturer' | head -20")
    d["usb"] = usb
    return d


# ---------------------------------------------------------------- Windows

PS = ["powershell", "-NoProfile", "-Command"]


def ps(cmd):
    return run(PS + [cmd])


def collect_windows():
    d = {}
    d["system"] = run(["cmd", "/c", "ver"])
    d["model_identifier"] = ps(
        "(Get-CimInstance Win32_ComputerSystemProduct).Name + ' | ' + "
        "(Get-CimInstance Win32_ComputerSystem).Model")
    d["cpu"] = ps("(Get-CimInstance Win32_Processor).Name")
    # 显示适配器（含硬件 ID，用于判断核显代次与独显）
    gpu = ps(
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        "$_.Name + '  [ID:' + $_.PNPDeviceID + ']' }")
    d["gpu_raw"] = gpu
    d["igpu"] = first_match(gpu, r"Intel|UHD|HD Graphics|Iris")
    # 声卡 codec（VEN_10EC&DEV_xxxx → ALC 型号）
    d["audio"] = ps(
        "Get-PnpDevice -Class 'MEDIA','System' -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -match 'HDAUDIO|VEN_10EC' } | "
        "ForEach-Object { $_.FriendlyName + '  [ID:' + $_.InstanceId + ']' }")
    # 网卡
    d["nic"] = ps(
        "Get-PnpDevice -Class 'Net' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.FriendlyName + '  [ID:' + $_.InstanceId + ']' }")
    # 触控板总线 / 键盘（判断 I2C vs PS2）
    d["input_driver"] = ps(
        "Get-PnpDevice -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -match 'I2C|PNP0303|PNP0F13|HID\\\\' -and "
        "$_.FriendlyName -match 'Touch|Touchpad|Keyboard|PS/2|I2C' } | "
        "ForEach-Object { $_.FriendlyName + '  [ID:' + $_.InstanceId + ']' }")
    # 摄像头：'Image' 是标准设备类（摄像头/扫描仪）；部分系统另有 'Camera' 类，
    # 拆成两次查询分别容错，避免单条命令因类名不存在而整体失败
    cam1 = ps(
        "Get-PnpDevice -Class 'Image' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.FriendlyName }")
    cam2 = ps(
        "Get-PnpDevice -Class 'Camera' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.FriendlyName }")
    d["camera"] = "\n".join(x for x in (cam1, cam2) if x) or None
    d["storage"] = ps(
        "Get-PhysicalDisk -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.FriendlyName + ' | ' + $_.MediaType + ' | ' + $_.BusType }")
    # 内存总量：必须先汇总再取整（对每条内存做 -join 只会得到逐行标量，不是总量）
    d["memory"] = ps(
        "$m = Get-CimInstance Win32_PhysicalMemory; "
        "\"$([math]::Round((($m | Measure-Object Capacity -Sum).Sum)/1GB))GB\"")
    d["bios"] = ps("(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion")
    return d


# ---------------------------------------------------------------- Linux

def collect_linux():
    d = {}
    d["system"] = sh("lsb_release -ds", "cat /etc/os-release | head -2")
    d["model_identifier"] = sh(
        "sudo dmidecode -s system-product-name",
        "cat /sys/class/dmi/id/product_name 2>/dev/null")
    d["cpu"] = sh("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
    d["gpu_raw"] = sh("lspci | grep -iE 'vga|3d|display'")
    d["igpu"] = first_match(d["gpu_raw"], r"Intel")
    d["audio"] = sh("lspci | grep -i audio")
    d["nic"] = sh("lspci | grep -iE 'network|wireless|ethernet'")
    d["storage"] = sh("lsblk -d -o NAME,SIZE,MODEL | head -10")
    d["memory"] = sh("free -h | head -2")
    return d


# ---------------------------------------------------------------- 输出

FIELDS = [
    ("system", "系统版本"),
    ("model_identifier", "机型标识 / 仿冒机型"),
    ("cpu", "CPU"),
    ("igpu", "核显"),
    ("gpu_raw", "显卡明细（含硬件 ID）"),
    ("metal", "Metal / 加速状态"),
    ("vram", "VRAM"),
    ("audio", "声卡 / codec"),
    ("audio_layout", "声卡 layout（ioreg）"),
    ("nic", "网卡"),
    ("camera", "摄像头"),
    ("input_driver", "触控板 / 键盘驱动线索"),
    ("storage", "存储"),
    ("memory", "内存"),
    ("usb", "USB"),
    ("battery", "电池"),
    ("sleep_blockers", "★ 挡睡断言"),
    ("sleep_count", "★ 睡眠次数（Total Sleep/Wakes）"),
    ("bios", "BIOS 版本"),
]


def render(d, as_json=False):
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print("=" * 68)
    print("硬件采集结果（只读）")
    print("=" * 68)
    for key, label in FIELDS:
        v = d.get(key)
        if not v:
            continue
        print(f"\n{label}:")
        for line in str(v).splitlines():
            print(f"  {line.strip()}")
    print("\n" + "=" * 68)
    print(f"{INFO} 以下项目**任何脚本都读不到**，必须人工进 BIOS 目视确认：")
    print("    CFG Lock / DVMT Pre-Allocated / Secure Boot / CSM(Legacy)")
    print("    Above 4G Decoding / VT-d / Fast Boot")
    print("\n回报模板：")
    for k in ["CFG Lock", "DVMT Pre-Alloc", "Secure Boot", "CSM / Legacy",
              "Above 4G", "VT-d", "Fast Boot"]:
        print(f"    {k:<16}: ?")
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser(description="跨平台硬件采集（只读）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    a = ap.parse_args()

    sysname = platform.system()
    if sysname == "Darwin":
        d = collect_macos()
    elif sysname == "Windows":
        d = collect_windows()
    elif sysname == "Linux":
        d = collect_linux()
    else:
        print(f"{INFO} 未知平台: {sysname}，尝试 Linux 命令集")
        d = collect_linux()

    d["_platform"] = sysname
    render(d, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
