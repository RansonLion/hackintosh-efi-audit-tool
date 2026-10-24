#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
efi_config_audit.py — 黑苹果 OpenCore config.plist 审计工具（笔记本 / 台式机通用）

逐项核对高频故障点相关配置，输出 ✅ / ⚠️ / ❌ 报告。**平台感知**：
台式机用 iMac/Macmini/MacPro 仿冒属正常，且会自动跳过/降级笔记本专属检查
（盒盖睡眠 / Optimus / PNLF 内屏亮度 / I2C 触控板 / SMCBatteryManager）。
只读，不修改任何文件。

⚠️ 注意：不要用 `plutil -convert json` 解析 config.plist！
   config.plist 含 <data>（二进制）字段，plutil 转 JSON 会报
   "invalid object in plist for destination format"。
   本脚本改用 plistlib，<data> 原生读作 bytes，无需 base64 解码。

历史：v1.0.1 前文件名是 efi_laptop_audit.py；旧名保留为兼容入口
（同目录 efi_laptop_audit.py 仅为转发，无逻辑）。

用法：
  python3 efi_config_audit.py                       # 自动探测 /Volumes/EFI/EFI/OC/config.plist
  python3 efi_config_audit.py /path/to/config.plist
"""

import argparse
import contextlib
import glob
import io
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATE_PATHS = [
    "/Volumes/EFI/EFI/OC/config.plist",
    "/Volumes/EFI/efi/OC/config.plist",
]

# 期望值（Comet Lake-U / Gen9.5 平台已验证）
EXPECT_IGPU = {
    "device-id": 0x3E9B,
    "AAPL,ig-platform-id": 0x3E9B0000,
}

REQUIRED_KEXTS = [
    "Lilu.kext",
    "WhateverGreen.kext",
    "AppleALC.kext",
    "VirtualSMC.kext",
]

# 仅笔记本必需（台式机无电池，缺了不报 FAIL）
LAPTOP_ONLY_KEXTS = ["SMCBatteryManager.kext"]

RECOMMENDED_KEXTS = ["NVMeFix.kext"]

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"

# ---- JSON 输出模式（--json）：检查逻辑不变，emit/sec 改记录结构化结果 ----
JSON_MODE = False
FINDINGS = []
CUR_SECTION = "(全局)"


def audit_section(title):
    """段头：文本模式打印；JSON 模式只记录当前段名。"""
    global CUR_SECTION
    CUR_SECTION = title
    if not JSON_MODE:
        print(f"\n【{title}】")
        print("-" * 64)


def load_model_sets():
    """从单一数据源 models.json 读 TAHOE_OK / T2_MODELS / 机型代次表
    （与 audit_opcore_result.py 共用）。"""
    p = Path(__file__).resolve().parent / "models.json"
    if not p.is_file():
        print(f"{FAIL} 缺少机型数据源: {p}（应与脚本同目录分发）")
        sys.exit(1)
    models = json.loads(p.read_text(encoding="utf-8")).get("models", [])
    tahoe_ok = {m["model"] for m in models if m.get("tahoe")}
    t2 = {m["model"] for m in models if m.get("t2")}
    gen_of = {m["model"]: m.get("igpu_gen") for m in models}
    return tahoe_ok, t2, gen_of


# 轴三：macOS 26 Tahoe 是最后一个支持 Intel Mac 的版本（Apple 官方支持列表；
# 台式机含 iMac20,1/20,2、MacPro7,1）。T2 机型在新系统下 SecureBootModel 应给 Default。
TAHOE_OK, T2_MODELS, MODEL_GEN = load_model_sets()


def load_plist(path: Path):
    """用 plistlib 读 plist（<data> 原生为 bytes）。"""
    with open(path, "rb") as f:
        return plistlib.load(f)


def dget(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def fmt_data(val):
    """把 <data> 的 bytes 渲染成 '字节 + 小端整数 + MB'。"""
    if not isinstance(val, (bytes, bytearray)):
        return str(val), None
    raw = bytes(val)
    hexs = " ".join(f"{b:02X}" for b in raw)
    num = int.from_bytes(raw, "little")
    mb = f" ≈ {num / (1024 * 1024):.0f} MB"
    return f"{hexs}  (小端=0x{num:08X}{mb})", num


def find_key(d, needle):
    if not isinstance(d, dict):
        return None
    for k in d.keys():
        if needle.lower() in k.lower():
            return k
    return None


def emit(status, label, detail=""):
    if JSON_MODE:
        FINDINGS.append({"section": CUR_SECTION, "level": status,
                         "label": label, "detail": detail})
        return
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "info": "ℹ️ "}[status]
    print(f"  {icon} {label}" + (f"\n       {detail}" if detail else ""))


def platform_of(model: str):
    """由机型前缀判定平台：笔记本 / 台式机 / 未知。
    'iMac20,1' 的家族前缀是 'iMac20'，要取字母部分再比对。"""
    if not model:
        return "unknown"
    fam = model.split(",")[0]          # 'iMac20,1' → 'iMac20'
    if fam.startswith("MacBook"):
        return "laptop"
    if (fam.startswith("iMac") or fam.startswith("Macmini")
            or fam.startswith("MacPro") or fam.startswith("Xserve")):
        return "desktop"
    return "unknown"


def check_smbios(cfg):
    audit_section("SMBIOS 机型")
    m = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="(未设置)")
    plat = platform_of(m)
    if plat == "laptop":
        emit(OK, f"机型: {m}", "属于笔记本平台族（MacBook / MacBookAir / MacBookPro），语义正确")
    elif plat == "desktop":
        emit(OK, f"机型: {m}", "属于台式机平台族（iMac / Macmini / MacPro），桌面仿冒语义正确")
    else:
        emit(FAIL, f"机型: {m}",
             "★ 非 Mac 平台机型！用 iMac/MacPro/Macmini 仿冒笔记本会导致背光/电池/电源管理异常；\n"
             "       用 MacBook 仿冒台式机会缺桌面供电/显卡语义。请确认机型与平台匹配。")
    emit(INFO, "board-id 建议交给 OpenCore 自动填充",
         "按 SystemProductName 自动生成整套 SMBIOS 可避免手抄 board-id 出错；\n"
         "       手抄值与机型不匹配会引发难以定位的异常。")
    # 轴三：macOS 版本资格（独立于核显代次）
    if plat in ("laptop", "desktop"):
        ok = m in TAHOE_OK
        emit(INFO, f"轴三（macOS 版本资格）：{m} 的 Tahoe(macOS 26) 资格 = {'✅ 支持' if ok else '❌ 最高 Sequoia'}",
             "macOS 26 Tahoe 是最后一个支持 Intel Mac 的版本，受支持机型：\n"
             "       笔记本 16,1/16,2/16,4；台式机 iMac20,1/20,2、MacPro7,1。\n"
             "       请按目标系统版本确认机型资格；轴三与轴一不挂钩（同为 Gen11 的 Air9,1 不在列表，16,2 在）。")


def check_update_mode(cfg):
    audit_section("UpdateSMBIOSMode")
    v = dget(cfg, "PlatformInfo", "UpdateSMBIOSMode", default="(未设置)")
    if v in ("Override", "Custom"):
        emit(OK, f"UpdateSMBIOSMode = {v}", "倾向保留厂商原厂表，Windows 双系统读取正常")
    else:
        emit(WARN, f"UpdateSMBIOSMode = {v}",
             "若为 Windows 双系统且希望 Windows 读到 Dell 原厂表，建议核对所用 OC 版本文档中\n"
             "       Create / Override / Custom 的语义后，调整为保留原厂表的那一档")


def check_rtc(cfg):
    m = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="(未设置)")
    plat = platform_of(m)
    title = "【RTC / 睡眠唤醒】" if plat == "desktop" else "【RTC / 盒盖睡眠】(★ 笔记本最高频故障)"
    print(f"\n{title}")
    print("-" * 64)
    v = dget(cfg, "Kernel", "Quirks", "DisableRtcChecksum", default=None)
    if plat == "desktop":
        if v is True:
            emit(OK, "DisableRtcChecksum = true", "已关闭 RTC 校验和写入 → 规避部分主板「软睡眠→唤醒报 Sleep Wake failure」")
        else:
            emit(INFO, f"DisableRtcChecksum = {v}",
                 "台式机无盒盖，但部分主板 BIOS 仍校验 RTC/CMOS 校验和，macOS 写入会致其失效 → 唤醒强重启。\n"
                 "   若出现睡眠唤醒失败，设 Kernel → Quirks → DisableRtcChecksum = true 后 Reset NVRAM。")
    else:
        if v is True:
            emit(OK, "DisableRtcChecksum = true", "已关闭 RTC 校验和写入 → 已规避 Dell 等机型「盒盖睡眠→次日提示重启」")
        else:
            emit(FAIL, f"DisableRtcChecksum = {v}",
                 "★ 风险：BIOS 校验 RTC/CMOS 校验和，macOS 写入会致其失效 → Sleep Wake failure in EFI → 强制重启\n"
                 "   修法: Kernel → Quirks → DisableRtcChecksum = true，然后 Reset NVRAM")

    adds = dget(cfg, "ACPI", "Add", default=[]) or []
    names = [a.get("Path", "") for a in adds if isinstance(a, dict)]
    if any("RTCAWAC" in n.upper() for n in names):
        emit(INFO, "SSDT-RTCAWAC 已加载",
             "注意：它只解决 RTC 设备存在性（AWAC→RTC），**不解决校验区被写坏**——仍需 DisableRtcChecksum")
    else:
        emit(INFO, "未发现 SSDT-RTCAWAC",
             "仅当平台有 AWAC 时才需要（Skylake 及更新常见；Haswell / Broadwell 通常没有，属正常）")

    # PowerTimeoutKernelPanic：修复（而非触发）电源状态切换/睡眠唤醒时的 panic，
    # Dortania 对全部平台推荐 true（历史版本曾误传 false，已按官方口径修正）
    pt = dget(cfg, "Kernel", "Quirks", "PowerTimeoutKernelPanic", default=None)
    if pt is True:
        emit(OK, "PowerTimeoutKernelPanic = true",
             "修复电源状态切换/睡眠唤醒时的 kernel panic（Dortania 全平台推荐值）")
    else:
        emit(WARN, f"PowerTimeoutKernelPanic = {pt}",
             "★ Dortania 对全部平台推荐 true：该 quirk 修复电源管理超时 panic（最常见于数字音频）。\n"
             "   建议 Kernel → Quirks → PowerTimeoutKernelPanic = true。")


def check_igpu(cfg):
    audit_section("核显 DeviceProperties (Pci(0x2,0x0))")
    add = dget(cfg, "DeviceProperties", "Add", default={}) or {}
    path = find_key(add, "Pci(0x2,0x0)")
    if not path:
        emit(FAIL, "未找到 Pci(0x2,0x0) 注入", "核显驱动靠 DeviceProperties 注入，缺失会导致无加速或黑屏")
        return
    d = add[path]
    emit(INFO, f"注入路径: {path}")
    plat = platform_of(dget(cfg, "PlatformInfo", "Generic", "SystemProductName",
                            default="(未设置)"))

    for key, expect in EXPECT_IGPU.items():
        if key not in d:
            if key == "device-id" and plat == "desktop":
                emit(INFO, "未设置 device-id",
                     "台式机 UHD630 原生 0x9BC8 可被驱动直接识别，通常**不需要**伪装\n"
                     "       （device-id 伪装是 Comet Lake-U 0x9B41 等笔记本场景的做法）")
            else:
                emit(WARN, f"未设置 {key}",
                     "参考值（★ 仅适用于 Comet Lake-U 0x9B41 需伪装的平台）："
                     f"0x{expect:08X}；其它平台原生 ID 不同，不一定需要伪装")
        else:
            _, num = fmt_data(d[key])
            if num == expect:
                emit(OK, f"{key} = 0x{num:08X}")
            else:
                emit(INFO, f"{key} = 0x{num:08X}" if num is not None else f"{key} = {d[key]}",
                     f"注意：0x{expect:08X} 是 **Comet Lake-U 平台** 的验证值，"
                     "并非普适值——其它平台（如 Broadwell 伪装 0x1626）按各自平台判断即可")

    print()
    for key, desc in [
        ("framebuffer-fbmem", "帧缓冲 surface 内存 ★ 高分辨率卡顿的主要杠杆"),
        ("framebuffer-stolenmem", "开机预留的连续 stolen 内存"),
        ("framebuffer-unifiedmem", "动态共享内存上限"),
    ]:
        if key not in d:
            if plat == "desktop":
                emit(INFO, f"未设置 {key}",
                     f"{desc}——但这是**笔记本面板/外接场景**的杠杆；\n"
                     "       台式机 iGPU 直出物理端口（DP/HDMI）通常不需要，高分辨率外接卡顿时再考虑")
            else:
                emit(FAIL, f"缺少 {key}", f"未配置 {desc} —— 这正是外接 2K/4K 卡顿的典型根因")
        else:
            txt, _ = fmt_data(d[key])
            emit(OK, f"{key}: {txt}", desc)

    if plat == "desktop":
        print()
        emit(INFO, "桌面 UHD630 的 ig-platform-id 三值语义（按 iGPU-only / +dGPU 选）",
             "· 0x3E9B0007（07009B3E）★ iGPU 驱动显示器的首选值\n"
             "· 0x3E9B0000（00009B3E）  首选值黑屏/花屏时的备选\n"
             "· 0x3E920003 / 0x9BC80003  仅独显输出时的 headless 值\n"
             "300/400 系主板 iGPU 直出黑屏时，优先排查 BusID 与端口补丁\n"
             "（framebuffer-conX-alldata / busid=00），不要反复换 ig-platform-id。")

    print()
    emit(INFO, "★ 增大 stolenmem ≠ 提升性能",
         "实测：stolenmem 由 32MB(00 00 00 02) 降到 19MB(00 00 30 01)、同时正确设置 fbmem 后，\n"
         "       2K 反而变顺。iGPU 实际用的是动态 unifiedmem(2048MB)，stolenmem 只是早期预留。\n"
         "       盲目加大 stolenmem 无收益，过大会触发 stolen memory 启动 panic 并浪费 RAM。")


def check_audio(cfg):
    audit_section("声卡 DeviceProperties (Pci(0x1f,0x3) / HDEF)")
    add = dget(cfg, "DeviceProperties", "Add", default={}) or {}
    # ★ 音频控制器路径随平台变化，不能只认 Pci(0x1f,0x3)：
    #   Skylake 及更新 = Pci(0x1f,0x3)；Haswell / Broadwell = Pci(0x1b,0x0)
    path = find_key(add, "Pci(0x1f,0x3)") or find_key(add, "Pci(0x1b,0x0)")
    if not path:
        emit(WARN, "未找到音频控制器注入",
             "Skylake 及更新走 Pci(0x1f,0x3)；Haswell/Broadwell 走 Pci(0x1b,0x0)。\n"
             "       如需要板载声卡，请在对应路径注入 alc-layout-id")
        return
    d = add[path]
    emit(INFO, f"注入路径: {path}")

    if "alc-layout-id" in d:
        emit(OK, f"alc-layout-id = {d['alc-layout-id']}", "★ 优先级高于 layout-id，可锁定布局、杜绝残留幽灵值")
    elif "layout-id" in d:
        emit(WARN, f"layout-id = {d['layout-id']}", "建议改用 alc-layout-id（优先级更高）")
    else:
        emit(FAIL, "未注入 layout 属性", "声卡将使用默认/回退布局，可能无声")

    ba = dget(cfg, "NVRAM", "Add", "7C436110-AB2A-4BBB-A880-FE41995C9F82",
             "boot-args", default="")
    if "layout-id" in ba:
        emit(FAIL, "boot-args 中出现 layout-id", "★ 反模式：AppleALC 只认 DeviceProperties")
    else:
        emit(OK, "boot-args 未误用 layout-id",
         f"当前 boot-args: {ba}" if ba else "当前 boot-args: (空)")


# 危险启动参数分级表（P1-1）：比「调试参数」更醒目——这些参数直接改变图形/显卡行为
DANGEROUS_BOOT_ARGS = [
    (r"-igfxvesa", "fail",
     "核显强制 VESA：**Metal 完全无加速**（等于白丢核显），最常见于误信「修复花屏」偏方。\n"
     "       正解是修 framebuffer / ig-platform-id / BusID，不是关加速。"),
    (r"-radvesa", "fail",
     "AMD 独显强制 VESA：**Metal 完全无加速**，删除。"),
    (r"nv_disable=1", "warn",
     "禁用 NVIDIA 独显属性注入（10.13 前的老写法）。确认独显确实要禁用才有意义；\n"
     "       现代做法是 WhateverGreen + SSDT 屏蔽，两者留一即可。"),
    (r"-wegnoegpu", "info",
     "禁用独显：Optimus 笔记本属合理操作；台式机带独显直出时不应加。"),
]


# T2_MODELS / TAHOE_OK 均来自单一数据源 models.json（文件顶部 load_model_sets）。

# 已知「SIP 被放宽」的 csr-active-config 值（OCLP 根补丁/未签名 kext/调试场景）
CSR_RELAXED = {0x67, 0x6F, 0x77, 0x7F, 0xFF, 0x803, 0xA03}


def check_security(cfg):
    """P1-4：SecureBootModel 与 SIP（csr-active-config）检查。"""
    audit_section("Misc → Security / SIP")
    m = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="(未设置)")
    sec = dget(cfg, "Misc", "Security", default={}) or {}
    sbm = sec.get("SecureBootModel")
    if m in T2_MODELS:
        if sbm == "Default":
            emit(OK, "SecureBootModel = Default（T2 机型推荐值）")
        elif sbm in (None, "Disabled"):
            emit(WARN, f"SecureBootModel = {sbm}（机型 {m} 带 T2）",
                 "★ 仿冒 T2 机型建议 SecureBootModel = Default；Disabled 会关掉 Apple 安全启动策略。")
        else:
            emit(INFO, f"SecureBootModel = {sbm}", "T2 机型一般 Default 即可；其它取值须与 Dortania 机型表对应。")
    else:
        if sbm in (None, "Disabled"):
            emit(OK, f"SecureBootModel = {sbm}（非 T2 机型，Disabled 正确）")
        else:
            emit(WARN, f"SecureBootModel = {sbm}（非 T2 机型）",
                 "非 T2 机型设非 Disabled 值可能启动失败；除非按 Dortania 表格精确选值，否则用 Disabled。")

    csr_raw = dget(cfg, "NVRAM", "Add", "7C436610-AB2A-4BBB-A880-FE41995C9F82",
                   "csr-active-config", default=None)
    if csr_raw is None:
        emit(OK, "csr-active-config 未设置（SIP 默认全开）", "无 OCLP/根补丁需求时的正确状态")
    else:
        val = int.from_bytes(bytes(csr_raw), "little") if isinstance(csr_raw, (bytes, bytearray)) else csr_raw
        if val == 0:
            emit(OK, "csr-active-config = 0x00（SIP 完全启用）")
        elif isinstance(val, int) and val in CSR_RELAXED:
            emit(WARN, f"csr-active-config = 0x{val:X}（SIP 被放宽）",
                 "★ 该值对应 OCLP 根补丁 / 未签名 kext / 调试场景。确认确有此类计划；\n"
                 "   否则建议删掉该键恢复默认 SIP。")
        else:
            emit(INFO, f"csr-active-config = 0x{val:X}", "非标准放宽值，请按所用工具（OCLP 等）文档核对")


def check_boot_args(cfg):
    audit_section("boot-args 启动参数")
    ba = dget(cfg, "NVRAM", "Add", "7C436110-AB2A-4BBB-A880-FE41995C9F82",
              "boot-args", default="")
    if not ba:
        emit(INFO, "boot-args 为空", "未配置任何启动参数")
        return
    hit_danger = False
    icon_of = {"fail": FAIL, "warn": WARN, "info": INFO}
    for pat, level, note in DANGEROUS_BOOT_ARGS:
        if re.search(pat, ba, re.I):
            hit_danger = True
            emit(icon_of.get(level, FAIL), f"危险启动参数命中：{pat}",
                 f"{note}\n       当前 boot-args: {ba}")
    if re.search(r"(^|\s)-v(\s|$)", ba) or "debug=0x" in ba:
        emit(WARN, f"boot-args 含调试参数：{ba}",
             "-v / debug=0x100 会刷屏并拖慢启动，日常使用建议去掉；keepsyms=1 可保留")
    elif not hit_danger:
        emit(OK, f"boot-args 未见危险/调试参数：{ba}")
    # alcid/layout-id 反模式（AppleALC 只认 DeviceProperties）
    if re.search(r"(^|\s)(alcid|layout-id)=", ba):
        emit(FAIL, "boot-args 中出现 alcid/layout-id",
             "★ 反模式：AppleALC 只读 DeviceProperties 的 alc-layout-id，boot-arg 写法无效")


def check_kexts(cfg):
    audit_section("Kext 清单")
    adds = dget(cfg, "Kernel", "Add", default=[]) or []
    present = set()
    for k in adds:
        if isinstance(k, dict):
            for f in ("BundlePath", "Path"):
                if f in k:
                    present.add(k[f].split("/")[-1])
    m = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="(未设置)")
    plat = platform_of(m)
    for name in REQUIRED_KEXTS:
        emit(OK, name) if name in present else emit(FAIL, f"缺少 {name}", "核显/声卡/电源管理必需")
    for name in LAPTOP_ONLY_KEXTS:
        if plat == "desktop":
            emit(INFO, f"无需 {name}", "台式机无电池，SMCBatteryManager 不适用（已跳过）")
        else:
            emit(OK, name) if name in present else emit(FAIL, f"缺少 {name}", "笔记本电池管理必需")
    for name in RECOMMENDED_KEXTS:
        emit(OK, name) if name in present else emit(INFO, f"未启用 {name}", "建议启用以优化 NVMe 电源管理与睡眠")

    # 新 GPU 路线互斥检查（2026-10 起）：WhateverGreen 与 WhateverXe 不能同开
    low = {p.lower() for p in present}
    if "whatevergreen.kext" in low and "whateverxe.kext" in low:
        emit(FAIL, "WhateverGreen 与 WhateverXe 同时启用",
             "★ 项目明文互斥（多显卡机器也不例外）——这是配置错误，不是兼容性问题。\n"
             "       走 Xe 路线 → 关 WG；走原生路线 → 删 WX。详见 references/gen12-xe.md")
    elif "whateverxe.kext" in low:
        emit(OK, "已启用 WhateverXe（与 WhateverGreen 互斥检查通过）")
        emit(INFO, "WhateverXe 前置条件需人工确认",
             "Lilu 必须先加载；boot-args 需 -allow3d -disablegfxfirmware；\n"
             "       依赖修改版 OCLP root patch + AMFIPass Mod（静态审计看不到）")


def check_enabled_files(cfg, oc_dir):
    """P1-2：Enabled 与文件一致性（Kernel/Add · ACPI/Add · UEFI/Drivers）——
    「有条目但文件缺失」「Enabled=false 残留」「有文件但没引用」三类。"""
    audit_section("Enabled × 文件一致性")
    if not oc_dir or not oc_dir.is_dir():
        emit(INFO, "未定位到 OC 目录", "跳过条目×文件核对（需要 EFI/OC 目录树）")
        return

    for section, sub, ext in (("Kernel", "Kexts", (".kext",)),
                              ("ACPI", "ACPI", (".aml",)),
                              ("UEFI", "Drivers", (".efi",))):
        adds = dget(cfg, section, "Add", default=[]) or []
        disabled, missing = [], []
        for k in adds:
            if not isinstance(k, dict):
                continue
            name = (k.get("BundlePath") or k.get("Path") or "").split("/")[-1]
            if not name:
                continue
            if k.get("Enabled") is False:
                disabled.append(name)
                continue
            if not (oc_dir / sub / name).exists() and not list(oc_dir.rglob(name))[:1]:
                missing.append(name)
        label = {"Kernel": "kext", "ACPI": "SSDT", "UEFI": "UEFI 驱动"}[section]
        if disabled:
            emit(WARN, f"{section}/Add 有 Enabled=false 残留：" + "、".join(disabled),
                 "要么删条目，要么确认有意关闭")
        if missing:
            emit(FAIL, f"{section}/Add 有条目但文件缺失：" + "、".join(missing),
                 f"★ {label}文件缺失 = 实际不加载/没打上（静默失败），补文件或删条目")
        if not disabled and not missing and adds:
            emit(OK, f"{section}/Add 条目与文件一致（{len(adds)} 条）")


def check_acpi(cfg):
    m = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="(未设置)")
    plat = platform_of(m)
    audit_section("ACPI / SSDT")
    adds = dget(cfg, "ACPI", "Add", default=[]) or []
    names = " ".join(a.get("Path", "") for a in adds if isinstance(a, dict)).upper()
    for key, desc, req in [
        ("EC", "嵌入式控制器（USB 供电/电源管理基础）", True),
        ("PLUG", "启用 XCPM 原生电源管理", True),
        ("PNLF", "内屏亮度调节" + ("（台式机外接显示器不需要）" if plat == "desktop" else ""), True),
        ("RTCAWAC", "AWAC→RTC 设备切换（注意：不解决校验区）", False),
    ]:
        if key in names:
            emit(OK, f"含 {key}: {desc}")
        else:
            # PNLF 在台式机不强制（外接显示器走 DP/HDMI，亮度由显示器物理按键控制）
            if key == "PNLF" and plat == "desktop":
                emit(INFO, f"缺少 {key}（台式机可忽略）", desc)
            else:
                emit(FAIL if req else INFO, f"缺少 {key}", desc)


def main():
    global JSON_MODE
    ap = argparse.ArgumentParser(
        description="黑苹果 OpenCore config.plist 审计（笔记本/台式机通用，只读，平台自动判定）")
    ap.add_argument("config", nargs="?",
                    help="config.plist 路径（省略则自动探测 EFI 分区）")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON（findings 列表 + 汇总），抑制文本报告")
    a = ap.parse_args()

    path = Path(a.config) if a.config else None
    if path is None:
        for c in CANDIDATE_PATHS:
            p = Path(c)
            if p.is_file():
                path = p
                break

    if path is None or not path.is_file():
        print("❌ 未找到 config.plist。")
        print("   请先挂载 EFI 分区：diskutil list | grep 'EFI EFI' → diskutil mount diskXsY")
        print("   或直接指定路径：python3 efi_config_audit.py /Volumes/EFI/EFI/OC/config.plist")
        return 1

    try:
        cfg = load_plist(path)
    except Exception as e:
        print(f"❌ 读取失败: {e}")
        return 1

    JSON_MODE = bool(a.json)
    if JSON_MODE:
        # 检查过程中的直接 print（表头/提示）吞进缓冲区，只保留 findings
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_checks(cfg, path)
    else:
        print("=" * 64)
        print(f"审计目标: {path}")
        print("=" * 64)
        run_checks(cfg, path)

    if JSON_MODE:
        summary = {k: sum(1 for f in FINDINGS if f["level"] == k)
                   for k in ("ok", "warn", "fail", "info")}
        print(json.dumps({
            "tool": "efi_config_audit",
            "version": "1.0.1",
            "target": str(path),
            "summary": summary,
            "findings": FINDINGS,
        }, ensure_ascii=False, indent=2))
        return 0 if summary.get("fail", 0) == 0 else 2

    print("\n" + "=" * 64)
    print("提示: 本工具只读取并核对，不会修改任何文件。")
    print("      修改后请 plutil -lint 校验 + Reset NVRAM + 回归测试。")
    print("=" * 64)

    print("\n★ 交付前校验：用 OpenCorePkg 自带的 ocvalidate 对 config.plist 做完整 schema/语义校验")
    print("  （ocvalidate 比 plutil -lint 更严格，能抓 quirks / kext 引用 / 字段类型类错误）。")
    print("  ocvalidate 不通过不要交付；用法：ocvalidate <path>/config.plist")
    return 0


# ---------------------------------------------------------------- ocvalidate

def find_ocvalidate(path=None):
    """定位 ocvalidate：PATH → config.plist 附近（OpenCorePkg/Utilities/ocvalidate/）。"""
    exe = shutil.which("ocvalidate")
    if exe:
        return exe
    if path:
        try:
            for up in Path(path).resolve().parents[:4]:
                for hit in sorted(glob.glob(str(up / "**/Utilities/ocvalidate/ocvalidate"),
                                            recursive=True)):
                    if Path(hit).is_file():
                        return hit
        except Exception:
            pass
    return None


def check_xhci_unsupported(cfg):
    """P2-7：XHCI-unsupported.kext 冗余提示（Dortania：仅非原生 USB 控制器需要）。
    本脚本无硬件报告，按仿冒机型代次判：Gen11+ 机型（Ice Lake）与 400 系桌面
    （iMac20,x = Comet Lake-S）均为原生 USB 控制器，一般不需要该 kext。"""
    audit_section("XHCI-unsupported 冗余检查")
    adds = dget(cfg, "Kernel", "Add", default=[]) or []
    present = set()
    for k in adds:
        if isinstance(k, dict):
            for f in ("BundlePath", "Path"):
                if f in k:
                    present.add(k[f].split("/")[-1].lower())
    if "xhci-unsupported.kext" not in present:
        emit(INFO, "未启用 XHCI-unsupported.kext",
             "仅非原生 USB 控制器需要它（Dortania 清单：H370/B360/H310/Z390(pre-Mojave)/X79/X99/部分 ASRock）。")
        return
    model = dget(cfg, "PlatformInfo", "Generic", "SystemProductName", default="")
    gen = MODEL_GEN.get(model)
    if gen in ("Gen11", "Gen12") or model in ("iMac20,1", "iMac20,2"):
        emit(WARN, "XHCI-unsupported.kext 冗余",
             f"机型 {model}（{gen or 'Comet Lake-S 400 系'}）为 macOS 原生 XHCI 控制器——\n"
             "       Dortania 原则是「仅非原生控制器需要」（B460/Z490+ 明示不需要）；\n"
             "       确认 USB 各口实测正常后可移除。")
        return
    emit(INFO, "XHCI-unsupported.kext 已启用，未命中已知冗余场景",
         "若主板芯片组不在 Dortania 非原生清单（H370/B360/H310/Z390 pre-Mojave/X79/X99/\n"
         "       部分 ASRock）内，且 USB 各口实测正常，可移除做减法。")


def check_ocvalidate(path):
    """自动调用 ocvalidate 校验 config.plist（P2-6）。"""
    audit_section("ocvalidate 自动校验")
    if not path or not Path(path).is_file():
        emit(INFO, "无 config.plist 可校验", "跳过 ocvalidate")
        return
    exe = find_ocvalidate(path)
    if not exe:
        emit(INFO, "未找到 ocvalidate，未做该项检查",
             "装 OpenCorePkg 后从其 Utilities/ocvalidate/ 取二进制（或加入 PATH）重跑；\n"
             "       ocvalidate 不过不要交付。")
        return
    try:
        r = subprocess.run([exe, str(path)], capture_output=True,
                           text=True, timeout=120)
    except Exception as e:
        emit(WARN, f"ocvalidate 执行失败: {e}", f"命令: {exe} {path}")
        return
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-12:]) if out else "(无输出)"
    if r.returncode == 0:
        emit(OK, f"ocvalidate 通过（{Path(exe).name}）",
             "config.plist 的 schema/语义校验全部通过")
    else:
        emit(FAIL, f"ocvalidate 未通过（exit {r.returncode}）",
             f"命令: {exe} {path}\n       ---- 输出末尾 ----\n       {tail}")


def run_checks(cfg, path):
    check_smbios(cfg)
    check_update_mode(cfg)
    check_rtc(cfg)
    check_igpu(cfg)
    check_audio(cfg)
    check_security(cfg)
    check_boot_args(cfg)
    check_kexts(cfg)
    check_acpi(cfg)
    check_enabled_files(cfg, path.parent)
    check_xhci_unsupported(cfg)
    check_ocvalidate(path)


if __name__ == "__main__":
    sys.exit(main())
