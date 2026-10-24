#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_opcore_result.py — 静态审计 opcore-simplify 的产物（装机前可用，只读）

适用：还没装 macOS、开不了机，只能对着 Windows 下生成的 EFI 做静态审查。

⚠️ 工程约束（写同类脚本时务必复用）：
  1. 只用 Python 标准库 —— Windows / macOS / Linux 通用。
  2. config.plist 必须用 plistlib 解析。plutil -convert json 遇 <data> 段会报
     "invalid object in plist for destination format"；plistlib 把 <data> 原生读作 bytes。
  3. ACPI 反编译需 iasl；缺失时明确提示"未做该项检查"，不可静默跳过。

用法：
  python3 audit_opcore_result.py <OpCore-Simplify目录>       # 自动找 Results/ 与 SysReport/
  python3 audit_opcore_result.py --config EFI/OC/config.plist --report SysReport/Report.json
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
import tempfile
from pathlib import Path

OK, WARN, FAIL, INFO = "✅", "⚠️ ", "❌", "ℹ️ "


def dget(d, *keys, default=None):
    """安全多层字典取值（与 efi_config_audit.py 同签名）。"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

# ---------------------------------------------------------------- 机型数据
# 单一数据源 scripts/models.json（board-id / 代次 / TDP / Tahoe / T2 一处维护，
# 三个脚本共用；igpu_gen=null 的机型如 iMacPro1,1 无核显，不进代次对照表）。
# 数据勘误锚点（详见 models.json 的 _meta）：
#   · 16,2 = Ice Lake 28W/Gen11；16,3 = CFL-U 15W/Gen9.5（曾弄反，已修正）
#   · 10 代起核显代次与 CPU 世代脱钩（CML-U=Gen9.5，Ice Lake=Gen11）
#   · 不存在 iMac21,x Intel 机型（iMac21,x 是 2021 款 M1）

def load_models():
    """读 scripts/models.json；返回 (MODEL_TAXONOMY, TAHOE_OK, T2_MODELS, MODELS)。"""
    p = Path(__file__).resolve().parent / "models.json"
    if not p.is_file():
        print(f"{FAIL} 缺少机型数据源: {p}（应与脚本同目录分发）")
        sys.exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    models = data.get("models", [])
    taxonomy = {m["model"]: (m["igpu_gen"], m["tdp"])
                for m in models if m.get("igpu_gen")}
    tahoe_ok = {m["model"] for m in models if m.get("tahoe")}
    t2 = {m["model"] for m in models if m.get("t2")}
    return taxonomy, tahoe_ok, t2, models


MODEL_TAXONOMY, TAHOE_OK, T2_MODELS, MODELS = load_models()


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

# ★ 核显型号 → 代次（必须按型号数字判定，不能一律归到 Gen9.5）
#   早期「HD Graphics NNN」横跨 Gen7.5~Gen9.5，用正则粗判会全错。
#   补录（P2-3）：Gemini Lake UHD 605/610（Gen9.5）；Rocket Lake-S UHD 710/730/750
#   与 Alder Lake-S UHD 770（Gen12，无原生驱动）。
IGPU_MODEL_GEN = {
    # Haswell Gen7.5
    4200: "Gen7.5", 4400: "Gen7.5", 4600: "Gen7.5",
    5000: "Gen7.5", 5100: "Gen7.5", 5200: "Gen7.5",
    # Broadwell Gen8
    5300: "Gen8", 5500: "Gen8", 5600: "Gen8",
    6000: "Gen8", 6100: "Gen8", 6200: "Gen8",
    # Skylake Gen9
    510: "Gen9", 515: "Gen9", 520: "Gen9", 530: "Gen9",
    540: "Gen9", 550: "Gen9", 580: "Gen9",
    # Kaby Lake / Coffee Lake / Whiskey / Comet / Amber / Gemini Lake → Gen9.5
    615: "Gen9.5", 617: "Gen9.5", 620: "Gen9.5", 630: "Gen9.5",
    605: "Gen9.5", 610: "Gen9.5",
    # Rocket Lake-S / Alder Lake-S → Gen12（Xe-LP，macOS 无原生驱动）
    710: "Gen12", 730: "Gen12", 750: "Gen12", 770: "Gen12",
}

# Alder Lake-N / Twin Lake（N100 / N200 / i3-N305）的 PCI device ID 段（WhateverXe 支持列表）
# —— 其核显在 Windows 里只报「Intel UHD Graphics」无型号数字，只能靠 device ID 认代次。
ADL_N_GPU_IDS = set(range(0x46D0, 0x46D5))

# Alder Lake-N / Jasper Lake 的 CPU 命名（不走 i3/i5/i7 体系，正则匹配 i[3579]- 会漏）
ADL_N_CPU = re.compile(r"\b(?:I3-)?N(?:100|200|305)\b", re.I)     # Alder Lake-N → Gen12
JASPER_LAKE_CPU = re.compile(r"\bN(?:4120|5095|5100|5105|6000|6005)\b", re.I)  # Jasper Lake → Gen11

# CPU 世代 → 核显代次（仅在核显型号识别不出时的兜底）
CPU_GEN_TO_IGPU = {
    3: "Gen7", 4: "Gen7.5", 5: "Gen8", 6: "Gen9",
    7: "Gen9.5", 8: "Gen9.5", 9: "Gen9.5", 10: "Gen9.5", 11: "Gen12",
}

# CPU 后缀 → TDP 档位（初判，以实测为准）
#   NG（如 i7-1068NG7 / i5-1038NG7）= Ice Lake MacBook 专用 SKU，28W
SUFFIX_TDP = {"Y": 5, "U": 15, "H": 45, "HK": 45, "G": 28, "P": 28, "NG": 28}


# ---------------------------------------------------------------- JSON 模式
# --json：检查逻辑不变，emit/hr 改记录结构化结果（供 CI / 脚本消费）。
JSON_MODE = False
FINDINGS = []
CUR_SECTION = "(全局)"
_LEVEL_OF = {OK: "ok", WARN: "warn", FAIL: "fail", INFO: "info"}


def emit(icon, label, detail=""):
    if JSON_MODE:
        FINDINGS.append({"section": CUR_SECTION,
                         "level": _LEVEL_OF.get(icon, "info"),
                         "label": label, "detail": detail})
        return
    print(f"  {icon} {label}" + (f"\n       {detail}" if detail else ""))


def hr(title):
    if JSON_MODE:
        global CUR_SECTION
        CUR_SECTION = title
        return
    print(f"\n【{title}】")
    print("-" * 68)


# ---------------------------------------------------------------- 读取

def load_config(path: Path):
    with open(path, "rb") as f:
        return plistlib.load(f)


def find_first(root: Path, *candidates):
    """按候选名找文件；先精确、后大小写不敏感兜底。
    ★ Linux（ext4 等大小写敏感文件系统）上真实文件名常是 dsdt.aml 小写，
      写死 'DSDT.aml' 会找不到——必须兜底。"""
    for c in candidates:
        if "*" in c:
            hits = sorted(root.rglob(c))
            for h in hits:
                if h.is_file():
                    return h
        else:
            p = root / c
            if p.is_file():
                return p
            parent = p.parent
            if parent.is_dir():
                for h in sorted(parent.iterdir()):
                    if h.is_file() and h.name.lower() == p.name.lower():
                        return h
    return None


def walk_strings(obj):
    """
    递归收集 JSON 里所有字符串，用于未知 schema 的硬件报告。

    ★ 必须连 dict 的 **key** 一起收集：Hardware Sniffer 的 Report.json 结构是
      「设备名 → 详情」（Network / Sound / GPU / Storage Controllers 等段都如此），
      设备名（如 "Realtek 8821CE Wireless..."）在 key 里。只遍历 value 会漏掉
      网卡与声卡，导致这两项直接不检查。
    """
    out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(walk_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(walk_strings(v))
    return out


def grep(strings, *patterns):
    for s in strings:
        for p in patterns:
            if re.search(p, s, re.I):
                return s.strip()
    return None


# ---------------------------------------------------------------- 硬件事实抽取

def sniff_hardware(report: dict):
    """
    从 Report.json 里抽硬件事实（schema 未知，用模式匹配兜底）。

    ★ 匹配顺序有讲究：先找「带芯片型号」的精确写法，再退到泛化写法。
      例如网卡若只写 r"Wireless"，会先命中 "Wireless LAN 802.11ac" 这种
      不含芯片型号的描述，导致无法判断是 Realtek 还是 Intel。
    """
    strs = walk_strings(report)
    cpu = grep(strs, r"i[3579]-\d{4,5}[A-Z]{0,2}", r"Core\(TM\)\s+i[3579]")
    igpu = grep(strs, r"Iris\s*(Plus|Xe)", r"(UHD|HD)\s*Graphics\s*\d{0,3}",
                r"Intel.*Graphics")
    dgpus = [s.strip() for s in strs
             if re.search(r"(NVIDIA|GeForce|Radeon|RX\s*\d{4})", s, re.I)
             and not re.search(r"基本显示|Basic Display", s)]

    # 网卡：优先命中带厂商+芯片的写法
    nic = (grep(strs, r"Realtek.*(88\d{2}|87\d{2}|RTL\s?\d{4})", r"Realtek.*Wireless")
           or grep(strs, r"(MediaTek|MT79\d{2}|RZ6\d{2})")
           or grep(strs, r"(Intel.*Wireless|AX\d{3}|AC\s?\d{4}|7265|3160)")
           or grep(strs, r"(Broadcom|BCM\d{4,5})")
           or grep(strs, r"Wireless"))

    codec = (grep(strs, r"ALC\s?\d{3,4}")
             or grep(strs, r"10EC[-\s_](\w{4})")
             or grep(strs, r"DEV[_&]0?(\w{4})")
             or grep(strs, r"Realtek.*Audio", r"High Definition Audio"))

    hw = {
        "cpu": cpu, "igpu": igpu, "dgpu": dgpus[0] if dgpus else None,
        "codec": codec, "nic": nic, "raw": strs,
        "codename": None, "gpu_id": None, "codec_id": None, "brand": None,
        "platform": (report.get("Motherboard") or {}).get("Platform"),
        "chipset": (report.get("Motherboard") or {}).get("Chipset"),
        "board_name": (report.get("Motherboard") or {}).get("Name"),
    }
    hw.update(sniff_structured(report))
    # 品牌（用于挑选同品牌的声卡 layout）
    blob = "\n".join(strs)
    hw["brand"] = next((b for b in ("LENOVO", "ThinkPad", "Dell", "HP", "ASUS",
                                    "Acer", "HUAWEI", "MSI")
                        if re.search(b, blob, re.I)), None)
    return hw


# Hardware Sniffer 的 Report.json 有结构化的 Device ID / Codename 段，
# 比正则猜可靠得多；但不同版本 schema 可能变，故只做尽力提取。
CODE_NAME_GEN = {
    "haswell": "Gen7.5", "broadwell": "Gen8",
    "skylake": "Gen9", "kaby lake": "Gen9.5", "kabylake": "Gen9.5",
    "coffee lake": "Gen9.5", "whiskey lake": "Gen9.5",
    "comet lake": "Gen9.5", "amber lake": "Gen9.5",
    "ice lake": "Gen11", "tiger lake": "Gen12",
}


def sniff_structured(report: dict):
    """从 Report.json 的结构化段里抽 Codename / GPU Device ID / codec ID。"""
    out = {"codename": None, "gpu_id": None, "codec_id": None}
    for sec, val in (report.get("GPU") or {}).items():
        if isinstance(val, dict):
            out["codename"] = out["codename"] or val.get("Codename")
            did = val.get("Device ID") or ""
            m = re.search(r"8086[-\s_]([0-9A-Fa-f]{4})", str(did))
            if m:
                out["gpu_id"] = int(m.group(1), 16)
    for sec, val in (report.get("Sound") or {}).items():
        if isinstance(val, dict):
            did = str(val.get("Device ID") or "")
            if re.match(r"10EC", did, re.I):
                m = re.search(r"10EC[-\s_]([0-9A-Fa-f]{4})", did, re.I)
                if m:
                    out["codec_id"] = 0x10EC0000 + int(m.group(1), 16)
    return out


def cpu_generation(cpu):
    """从 CPU 型号取世代：i5-5200U → 5；i7-10510U → 10；i7-1068NG7 → 10；i7-9750H → 9；
    i7-1185G7 → 11；i5-1240P → 12。"""
    if not cpu:
        return None
    m = re.search(r"i[3579]-(\d{4,5})", cpu)
    if not m:
        return None
    n = m.group(1)
    # 5 位型号（如 10510 / 12600K）取前两位。
    # 4 位型号：首位为 1 的只可能是 10~14 代（1-9 代的 4 位型号是 2xxx~9xxx，
    #   首位即代次；1 代用的是 3 位型号）——否则取首位（8257 → 8，9750 → 9）。
    #   旧版只特判了 "10xx"，把 11/12 代 4 位型号（1185G7 / 1240P）误判成第 1 代。
    if len(n) == 5 or (len(n) == 4 and n[0] == "1"):
        return int(n[:2])
    return int(n[0])


def infer_igpu_gen(cpu, igpu, codename=None, gpu_id=None):
    """
    推断核显代次。

    ★ 不能用「(UHD|HD) Graphics → Gen9.5」这种粗判：
      HD 5500=Gen8(Broadwell)、HD 520=Gen9(Skylake)、HD 620=Gen9.5(Kaby)，
      一律判 Gen9.5 会在 5/6 代机器上给出错误的错配告警。

    ★ 10 代起 CPU 世代与核显代次脱钩，必须单独处理：
      Comet Lake-U(i7-10510U)=Gen9.5；Ice Lake(i5-1035G7)=Gen11。

    ★ Alder Lake-N / Twin Lake（N100/N200/i3-N305）的核显在 Windows 里只报
      「Intel UHD Graphics」无型号数字，与 Gemini Lake 同名——只能靠 PCI device ID
      （46D0–46D4）或 CPU 命名区分（P2-3 补录）。
    """
    low = (igpu or "").lower()

    # 0) 结构化字段里的 Codename 最可靠（Report.json 直接给了，如 "Broadwell"）
    cn = (codename or "").strip().lower()
    if cn in CODE_NAME_GEN:
        return CODE_NAME_GEN[cn]

    # 0.5) Alder Lake-N / Twin Lake：device ID 46D0–46D4 → Gen12
    #      （WhateverXe 支持列表；CPU 名 N100/N200/N305 同为 Gen12）
    if gpu_id in ADL_N_GPU_IDS:
        return "Gen12"
    if cpu and ADL_N_CPU.search(cpu):
        return "Gen12"
    if cpu and JASPER_LAKE_CPU.search(cpu):
        return "Gen11"

    # 1) Iris Xe → Gen12（11 代及更新，macOS 无驱动）
    if "xe" in low:
        return "Gen12"

    # 2) Iris Plus 横跨两代，靠 CPU 后缀区分
    if "iris" in low and "plus" in low:
        if cpu and re.search(r"i[3579]-10\d{2,3}[A-Z]*G\d?", cpu):
            return "Gen11"    # Ice Lake
        if cpu and re.search(r"i[3579]-10\d{2,3}[A-Z]*U", cpu):
            return "Gen9.5"   # Comet Lake-U
        if cpu and cpu_generation(cpu) == 8:
            return "Gen9.5"   # Coffee Lake-U 的 Iris Plus 655
        return "Gen11"

    # 3) HD/UHD Graphics NNN → 按型号数字查表（最关键的一步）
    m = re.search(r"(?:uhd|hd)\s*graphics\s*(\d{3,4})", low)
    if m:
        num = int(m.group(1))
        if num in IGPU_MODEL_GEN:
            return IGPU_MODEL_GEN[num]
        # 查不到但形如 6xx → 大概率 Gen9.5
        if 600 <= num < 700:
            return "Gen9.5"

    # 4) 兜底：用 CPU 世代推（10 代 G 系除外，已在第 2 步处理）
    gen = cpu_generation(cpu)
    if gen in CPU_GEN_TO_IGPU:
        return CPU_GEN_TO_IGPU[gen]
    return None


def infer_tdp(cpu):
    if not cpu:
        return None
    m = re.search(r"i[3579]-\d{4,5}([A-Z]{0,2})", cpu)
    if not m:
        return None
    suf = m.group(1).upper()
    if suf in SUFFIX_TDP:
        return SUFFIX_TDP[suf]
    if suf.startswith("G"):
        return 28
    return None


# ---------------------------------------------------------------- 检查项

def kext_names(cfg):
    """从 Kernel>Add 收集已启用 kext 的小写名集合。"""
    adds = (cfg.get("Kernel", {}) or {}).get("Add", []) or []
    names = set()
    for k in adds:
        if isinstance(k, dict):
            for f in ("BundlePath", "Path"):
                if f in k:
                    names.add(k[f].split("/")[-1].lower())
    return names


def kext_order(cfg):
    """Kernel>Add 的顺序列表（小写名），用于检查 Lilu 必须先加载。"""
    adds = (cfg.get("Kernel", {}) or {}).get("Add", []) or []
    out = []
    for k in adds:
        if isinstance(k, dict):
            for f in ("BundlePath", "Path"):
                if f in k:
                    out.append(k[f].split("/")[-1].lower())
    return out


def boot_args(cfg):
    return (cfg.get("NVRAM", {}).get("Add", {})
            .get("7C436110-AB2A-4BBB-A880-FE41995C9F82", {}) or {}).get("boot-args", "")


# NVIDIA 架构分流（用于独显路线判断）
RE_TURING = re.compile(r"(?i)(gtx\s*16\d{2}|rtx\s*[2-5]\d{3}|titan\s*rtx|quadro\s*rtx)")
RE_PASCAL = re.compile(r"(?i)(mx\s*[12]\d{2}|geforce\s*mx|gtx\s*10\d{2}|geforce\s*gtx\s*(9|10)\d)")
RE_XE = re.compile(r"(?i)(iris\s*xe|\bxe\b\s*graphics)")


def check_two_axis(cfg, hw):
    hr("SMBIOS 三轴复核（★ 工具最容易推错的地方）")
    model = (cfg.get("PlatformInfo", {}).get("Generic", {}) or {}).get(
        "SystemProductName", "(未设置)")
    plat = platform_of(model)
    plat_cn = {"laptop": "笔记本", "desktop": "台式机", "unknown": "未知平台"}.get(plat, "未知")
    print(f"  硬件报告: CPU={hw['cpu']}  核显={hw['igpu']}  独显={hw['dgpu']}  平台={hw.get('platform') or '未识别'}")
    print(f"  config   : SystemProductName = {model}（{plat_cn}）")

    # P1-9：Report 平台 × 机型平台 交叉核对——不一致会让 RTC/fbmem/device-id/
    # 盒盖睡眠等分支整体走错，是最贵的静默错误（台式机跑笔记本检查或反之）。
    rep_plat_raw = hw.get("platform")
    rep_plat = (rep_plat_raw or "").strip().lower()
    if rep_plat in ("desktop", "laptop") and plat in ("desktop", "laptop") and rep_plat != plat:
        emit(FAIL, f"平台交叉错配：硬件报告={rep_plat_raw}，仿冒机型={model}（{plat_cn}）",
             "★ 平台不一致会让 RTC/fbmem/device-id/盒盖睡眠等检查整体走错分支：\n"
             "       台式机仿笔记本机型（或反之）先纠正 SystemProductName，再重跑审计。\n"
             "       另请核对 Report.json 的 Motherboard.Platform 是否采集正确。")

    if model not in MODEL_TAXONOMY:
        if plat == "desktop":
            emit(WARN, f"台式机机型 {model} 不在内置对照表",
                 "台式机用 iMac/Macmini/MacPro 仿冒是正常做法，请人工按三轴核对：\n"
                 "       轴一核显代次、轴二（桌面=iGPU-only/+dGPU 取舍）、轴三 macOS 版本资格（见 references/desktop-validation.md）。")
        else:
            emit(WARN, f"机型 {model} 不在已知对照表中",
                 "请人工按三轴核对：轴一核显代次、轴二 TDP 档位、轴三 macOS 版本资格")
        return

    m_gen, m_tdp = MODEL_TAXONOMY[model]
    h_gen = infer_igpu_gen(hw["cpu"], hw["igpu"], hw.get("codename"), hw.get("gpu_id"))
    h_tdp = infer_tdp(hw["cpu"])
    wx = "whateverxe.kext" in kext_names(cfg)

    if h_gen and h_gen != m_gen:
        if wx:
            emit(INFO, f"轴一（核显代次）硬件 {h_gen} ≠ 机型 {m_gen}，但已启用 WhateverXe",
                 "WhateverXe 按 PCI ID 接管 GPU，不依赖 SMBIOS 声明代次 → 该错配由实验驱动兜底。\n"
                 "       仍需确认：与 WhateverGreen 互斥、boot-args 与 OCLP root patch 齐全（见下方新 GPU 路线检查）。")
        else:
            emit(FAIL, f"轴一（核显代次）错配：硬件 {h_gen} ≠ 机型 {m_gen}",
                 "★ 声明代次新于物理核显 → CoreML/Vision/MPS 挑错 kernel → gpuRestart → 睡死。\n"
                 "       这是稳定性问题，必须修正。")
    elif h_gen:
        emit(OK, f"轴一（核显代次）一致：{h_gen}")

    if plat == "desktop":
        emit(INFO, "轴二（桌面）：iMac19/20 均 65W 级散热，桌面档位= iGPU-only / +dGPU 取舍",
             "桌面无笔记本式功耗曲线约束；iGPU-only 选 iMac19,1/20,1/Macmini8,1，\n"
             "       iGPU+dGPU 选 iMac20,2，纯独显（Xeon；11 代 Rocket Lake-S 亦无原生桌面机型）选 MacPro7,1。")
    elif h_tdp and abs(h_tdp - m_tdp) > 7:
        emit(FAIL, f"轴二（TDP 档位）错配：CPU 约 {h_tdp}W ≠ 机型 {m_tdp}W 档",
             "档位过高 → 持续负载风扇狂转；过低 → 过度降频。")
    elif h_tdp:
        emit(OK, f"轴二（TDP 档位）一致：约 {h_tdp}W")

    if not hw["cpu"]:
        emit(WARN, "未能从报告识别 CPU", "请人工确认 CPU 型号与后缀后重跑三轴判定")

    # 10 代专项提示（笔记本 U 系 + 桌面 CML-S）
    if hw["cpu"]:
        if re.search(r"i[3579]-10\d{2,3}[A-Z]*U", hw["cpu"]) and h_gen == "Gen9.5":
            print()
            emit(WARN, "★ 10 代 Comet Lake-U 专项提醒",
                 "该组合（10 代 CPU + Gen9.5 核显）在 Apple 笔记本产品线中**没有真身**：\n"
                 "       Apple 的 10 代笔记本机型（16,2 / Air9,1）全是 Ice Lake(Gen11)；\n"
                 "       16,3 是 8 代 CFL-U（Gen9.5/15W，双轴其实一致但最高只到 Sequoia）；\n"
                 "       Comet Lake 台式机对应 iMac20,x。\n"
                 "       同代同档可用机型：MacBookPro15,4 / 16,3（15W 档）、MacBookPro15,2（28W 档）；\n"
                 "       若目标 macOS 26 Tahoe，Gen9.5 可升族只有 16,1/16,4（45W），选型时权衡。")
        elif plat == "desktop" and re.search(r"i[3579]-10\d{2,3}", hw["cpu"]):
            print()
            emit(INFO, "★ 桌面 Comet Lake-S 提示",
                 "10 代桌面（如 i3-10105 / i7-10700）核显 UHD630 = Gen9.5，原生驱动；\n"
                 "       目标 Tahoe 应选 iMac20,1（iGPU-only）/ iMac20,2（带独显），二者均在 Tahoe 列表。\n"
                 "       注意 iMac19,1 虽同代但不在 Tahoe 列表（最高 Sequoia）。")

    # 轴三：macOS 版本资格（独立于轴一）
    if model in MODEL_TAXONOMY:
        ok = model in TAHOE_OK
        emit(INFO, f"轴三（macOS 版本资格）：{model} 的 Tahoe(macOS 26) 资格 = {'✅ 支持' if ok else '❌ 最高 Sequoia'}",
             "macOS 26 Tahoe 是最后一个支持 Intel Mac 的版本，受支持机型：\n"
             "       笔记本 16,1/16,2/16,4；台式机 iMac20,1/20,2、MacPro7,1。\n"
             "       请按你的目标系统版本确认机型资格：目标 Tahoe 却选了非支持机型需走 OCLP 或降系统目标。\n"
             "       （注意：轴三与轴一不挂钩——同为 Gen11 的 Air9,1 不在 Tahoe 列表，16,2 在。）")

    # 台式机专属：CFG Lock / 独显 / 跳过笔记本项
    if plat == "desktop":
        print()
        xcpm = dget(cfg, "Kernel", "Quirks", "AppleXcpmCfgLock", default=None)
        if xcpm is True:
            emit(WARN, "★ 台式机 CFG Lock 未解（AppleXcpmCfgLock=true）",
                 "桌面（H470/B460 等）普遍锁 CFG Lock。应进 BIOS 解锁，并把它改回 false；\n"
                 "       仅靠 quirk 是「代价不是解法」，原生 XCPM 电源管理被降级。")
        elif xcpm is False:
            emit(OK, "AppleXcpmCfgLock = false（已 BIOS 解锁或原生）")
        emit(INFO, "台式机跳过笔记本专属检查",
             "盒盖睡眠 / Optimus 独显 / PNLF 内屏亮度 / I2C 触控板 / SMCBatteryManager 不适用于台式机。\n"
             "       台式机重点：独显路线（AMD 免驱 / NVIDIA 无驱动）、USB 本机映射、板载声卡 codec、软睡眠唤醒。")


# T2_MODELS / TAHOE_OK / MODEL_TAXONOMY 均来自单一数据源 models.json（文件顶部 load_models）。

# 已知「SIP 被放宽」的 csr-active-config 值（OCLP 根补丁/未签名 kext/调试场景）
CSR_RELAXED = {0x67, 0x6F, 0x77, 0x7F, 0xFF, 0x803, 0xA03}


def check_security(cfg, model):
    """P1-4：SecureBootModel 与 SIP（csr-active-config）检查。"""
    hr("Misc → Security / SIP")
    sec = (cfg.get("Misc", {}) or {}).get("Security", {}) or {}
    sbm = sec.get("SecureBootModel")
    if model in T2_MODELS:
        if sbm == "Default":
            emit(OK, "SecureBootModel = Default（T2 机型推荐值）",
                 "启用 Apple 安全启动策略；仿冒 T2 机型（iMac20,x / MacPro7,1 / 16,x 等）在新系统下应给 Default")
        elif sbm in (None, "Disabled"):
            emit(WARN, f"SecureBootModel = {sbm}（机型 {model} 带 T2）",
                 "★ 仿冒 T2 机型建议 SecureBootModel = Default；Disabled 会关掉 Apple 安全启动策略，\n"
                 "       部分系统功能与激活/服务检查可能受影响（Dortania Apple Secure Boot 页）。")
        else:
            emit(INFO, f"SecureBootModel = {sbm}",
                 "T2 机型一般 Default 即可；其它取值须与 Dortania 机型表精确对应。")
    else:
        if sbm in (None, "Disabled"):
            emit(OK, f"SecureBootModel = {sbm}（非 T2 机型，Disabled 正确）")
        else:
            emit(WARN, f"SecureBootModel = {sbm}（非 T2 机型）",
                 "非 T2 机型设非 Disabled 值可能启动失败；除非按 Dortania 表格精确选值，否则用 Disabled。")

    nv = (cfg.get("NVRAM", {}) or {}).get("Add", {}) or {}
    csr_raw = (nv.get("7C436610-AB2A-4BBB-A880-FE41995C9F82", {}) or {}).get("csr-active-config")
    if csr_raw is None:
        emit(OK, "csr-active-config 未设置（SIP 默认全开）", "无 OCLP/根补丁需求时的正确状态")
    else:
        val = int.from_bytes(bytes(csr_raw), "little") if isinstance(csr_raw, (bytes, bytearray)) else csr_raw
        if val == 0:
            emit(OK, "csr-active-config = 0x00（SIP 完全启用）")
        elif isinstance(val, int) and val in CSR_RELAXED:
            emit(WARN, f"csr-active-config = 0x{val:X}（SIP 被放宽）",
                 "★ 该值对应 OCLP 根补丁 / 未签名 kext / 调试场景。确认你确有此类计划\n"
                 "       （WhateverXe / NootedGreen / OCLP 补丁系统）；否则建议删掉该键恢复默认 SIP。")
        else:
            emit(INFO, f"csr-active-config = 0x{val:X}", "非标准放宽值，请按所用工具（OCLP 等）文档核对")


def check_igpu_props(cfg, plat="laptop"):
    hr("核显 DeviceProperties")
    add = (cfg.get("DeviceProperties", {}) or {}).get("Add", {}) or {}
    # ★ 路径大小写以 OpenCore 实际写入为准（Pci(0x1F,0x3) 大写 F 常见），必须 lower 匹配
    path = next((p for p in add if "pci(0x2,0x0)" in p.lower()), None)
    if not path:
        emit(FAIL, "未找到 Pci(0x2,0x0) 注入", "核显靠 DeviceProperties 注入，缺失会无加速或黑屏")
        return
    d = add[path]
    for k in ("device-id", "AAPL,ig-platform-id"):
        v = d.get(k)
        if isinstance(v, (bytes, bytearray)):
            emit(OK, f"{k} = 0x{int.from_bytes(bytes(v), 'little'):08X}")
        elif v is None:
            if k == "device-id" and plat == "desktop":
                emit(INFO, "未设置 device-id",
                     "台式机 UHD630 原生 0x9BC8 可被驱动直接识别，通常**不需要**伪装\n"
                     "       （device-id 伪装是 Comet Lake-U 0x9B41 等笔记本场景的做法）。")
            else:
                emit(WARN, f"未设置 {k}", "Comet Lake-U 等需伪装 device-id 的平台必须设置")
        else:
            emit(OK, f"{k} = {v}")
    for k, note in [("framebuffer-fbmem", "★ 高分辨率流畅度的主要杠杆"),
                    ("framebuffer-stolenmem", "够用即可，不是越大越好")]:
        v = d.get(k)
        if isinstance(v, (bytes, bytearray)):
            n = int.from_bytes(bytes(v), "little")
            emit(OK, f"{k} ≈ {n / (1024 * 1024):.0f} MB", note)
        else:
            if plat == "desktop":
                emit(INFO, f"未设置 {k}",
                     f"{note}；台式机 iGPU 直出物理端口（DP/HDMI）通常不需要，\n"
                     "       高分辨率外接仍有卡顿时再考虑。")
            else:
                emit(WARN, f"未设置 {k}", note)

    # 桌面 Gen9.5 ig-platform-id 三值语义（Dortania Comet Lake 桌面表）
    if plat == "desktop":
        emit(INFO, "桌面 UHD630 的 ig-platform-id 三值语义（按 iGPU-only / +dGPU 选）",
             "· 0x3E9B0007（07009B3E）★ iGPU 驱动显示器的首选值\n"
             "· 0x3E9B0000（00009B3E）  首选值黑屏/花屏时的备选\n"
             "· 0x3E920003 / 0x9BC80003  仅独显输出时的 headless 值\n"
             "300/400 系主板 iGPU 直出黑屏时，优先排查 BusID 与端口补丁\n"
             "（framebuffer-conX-alldata / busid=00），不要反复换 ig-platform-id。")


def check_quirks(cfg, plat="laptop"):
    hr("Quirks 风险点")
    q = (cfg.get("Kernel", {}) or {}).get("Quirks", {}) or {}

    if q.get("AppleXcpmCfgLock") or q.get("AppleCpuPmCfgLock"):
        emit(WARN, "已开启 AppleXcpmCfgLock / AppleCpuPmCfgLock",
             "★ 这说明 **CFG Lock 未解锁**。开 quirk 是代价不是解法：\n"
             "       原生 CPU 电源管理被降级，睿频与续航受损。\n"
             "       优先路径：进 BIOS 关 CFG Lock（或用 RU.efi / setup_var 解锁）。")
    else:
        emit(OK, "未依赖 CFG Lock 类 quirk", "若 BIOS 中 CFG Lock 已关闭，则状态正确")

    if q.get("DisableRtcChecksum") is True:
        emit(OK, "DisableRtcChecksum = true")
    else:
        if plat == "desktop":
            emit(INFO, f"DisableRtcChecksum = {q.get('DisableRtcChecksum')}",
                 "台式机无盒盖睡眠，但部分主板 BIOS 仍校验 RTC/CMOS 校验和，macOS 写入会致其失效 →\n"
                 "       唤醒强重启。若出现睡眠唤醒失败，设 Kernel → Quirks → DisableRtcChecksum = true 后 Reset NVRAM。")
        else:
            emit(FAIL, f"DisableRtcChecksum = {q.get('DisableRtcChecksum')}",
                 "★ 笔记本盒盖睡死高发项。Dell/HP 等 BIOS 校验 RTC/CMOS 校验和，\n"
                 "       macOS 写入会致其失效 → Sleep Wake failure in EFI → 强制重启。\n"
                 "       修法：Kernel → Quirks → DisableRtcChecksum = true，然后 Reset NVRAM。")

    # PowerTimeoutKernelPanic：修复电源状态切换/睡眠唤醒时的 kernel panic
    # （Dortania 全平台推荐 true；文档曾误写 false，已按官方口径修正）
    if q.get("PowerTimeoutKernelPanic") is True:
        emit(OK, "PowerTimeoutKernelPanic = true",
             "修复电源状态切换/睡眠唤醒时的 panic（Dortania 全平台推荐值）")
    else:
        emit(WARN, f"PowerTimeoutKernelPanic = {q.get('PowerTimeoutKernelPanic')}",
             "★ Dortania 对全部平台推荐 true：该 quirk 修复（而非触发）电源管理超时 panic，\n"
             "       最常见于数字音频与睡眠唤醒路径。建议 Kernel → Quirks → PowerTimeoutKernelPanic = true。")

    if q.get("XhciPortLimit"):
        emit(WARN, "XhciPortLimit 已开启",
             "仅作临时用途：USB 端口映射完成后应移除，否则在较新 macOS 上会导致端口异常。")


def detect_codec(hw):
    """
    从硬件报告里识别声卡 codec，返回 (显示名, CodecID 整数)。

    ★ 必须在**全部字符串**里搜，不能只用那条被挑出来的描述：
      "Realtek High Definition Audio" 里没有芯片号，芯片号在它旁边的
      硬件 ID（HDAUDIO\\...&VEN_10EC&DEV_0292）里。
    """
    # 结构化字段优先（Report.json 的 Sound → Device ID，形如 "10EC-0292"）
    if hw.get("codec_id"):
        cid = hw["codec_id"]
        # codec 型号是 ID 的十六进制后缀：0x10EC0292 → ALC292（不是十进制 658）
        return f"ALC{cid & 0xFFFF:03X} (0x{cid:08X})", cid

    pool = list(hw.get("raw") or []) + [x for x in [hw.get("codec")] if x]
    for s in pool:
        m = re.search(r"ALC\s?(\d{3,4})", s, re.I)
        if m:
            # ★ ALC 编号是**十六进制**：ALC897 → 0x897，不是十进制 897。
            #   十进制会把 CodecID 拼成 0x10EC0381（假 ID），AppleALC 永远匹配不上。
            cid = clamp_cid(0x10EC0000 + int(m.group(1), 16))
            if cid:
                return f"ALC{int(m.group(1), 16):03X}", cid
    for s in pool:
        m = re.search(r"VEN_10EC.*?DEV[_&](\w{4})", s, re.I) or \
            re.search(r"DEV[_&](\w{4})", s, re.I)
        if m:
            try:
                cid = clamp_cid(0x10EC0000 + int(m.group(1), 16))
                if cid:
                    return s.strip()[:60], cid
            except ValueError:
                pass
    return (hw.get("codec") or "").strip(), None


def clamp_cid(cid):
    """CodecID 收敛到 Realtek 0x10EC 段；越界返回 None（防止拼出假 ID 后误匹配）。"""
    if cid is None:
        return None
    return cid if 0x10EC0000 <= cid <= 0x10ECFFFF else None


def applealc_layouts(oc_dir: Path, codec_id):
    """从 AppleALC.kext 的 Info.plist 读出该 codec 支持的 layout 列表。"""
    hits = sorted(oc_dir.rglob("AppleALC.kext/Contents/Info.plist"))
    if not hits:
        return None
    try:
        with open(hits[0], "rb") as f:
            info = plistlib.load(f)
    except Exception:
        return None
    pers = info.get("IOKitPersonalities", {}) or {}
    out = []
    for val in pers.values():
        for item in (val.get("HDAConfigDefault") or []):
            if isinstance(item, dict) and item.get("CodecID") == codec_id:
                out.append((item.get("LayoutID"), item.get("Comment", "")))
    return out


def check_audio_layout(cfg, hw, cfg_path: Path):
    hr("声卡 layout 与 codec 匹配（★ 工具最常挑错的一项）")
    add = (cfg.get("DeviceProperties", {}) or {}).get("Add", {}) or {}
    # 音频控制器路径随平台变化：Skylake+ 是 Pci(0x1f,0x3)，Haswell/Broadwell 是 Pci(0x1b,0x0)
    # ★ 大小写以 OpenCore 实际写入为准（Pci(0x1F,0x3) 大写 F 常见），必须 lower 匹配，
    #   否则已正确注入声卡的机器会被误报「未找到音频控制器注入」
    path = next((p for p in add
                 if "pci(0x1f,0x3)" in p.lower() or "pci(0x1b,0x0)" in p.lower()), None)
    if not path:
        emit(WARN, "未找到音频控制器注入",
             "Skylake 及更新走 Pci(0x1f,0x3)；Haswell/Broadwell 走 Pci(0x1b,0x0)")
        return
    d = add[path]
    layout = d.get("alc-layout-id", d.get("layout-id"))
    if layout is None:
        emit(FAIL, "未注入 layout 属性", "声卡会走默认/回退布局，可能无声")
        return
    emit(INFO, f"{path} → layout = {layout}")

    name, codec_id = detect_codec(hw)
    if not codec_id:
        emit(WARN, "未能识别声卡 codec", f"报告线索: {hw.get('codec')} —— 人工核对 layout")
        return

    oc_dir = cfg_path.parent  # .../EFI/OC
    layouts = applealc_layouts(oc_dir, codec_id)
    if not layouts:
        emit(WARN, f"未在 EFI 内找到 AppleALC.kext 或其 {name} 条目",
             "人工核对：AppleALC.kext/Contents/Info.plist → IOKitPersonalities → HDAConfigDefault")
        return

    ids = [x[0] for x in layouts]
    if layout in ids:
        emit(OK, f"layout {layout} 属于 {name}（0x{codec_id:08X}）的定义")
    else:
        emit(FAIL, f"layout {layout} **不在** {name}（0x{codec_id:08X}）的可用列表中",
             "★ 这是 opcore-simplify 常见失误：挑到了别的机型的 layout（如带扩展坞的机型）。\n"
             "       优先改用在注释里写明「本品牌/同平台机型」的 layout。")
    print(f"\n       {name} 可用 layout（来自 AppleALC.kext）：")
    for lid, cmt in sorted(layouts, key=lambda x: (x[0] is None, x[0])):
        mark = " ← 当前" if lid == layout else ""
        print(f"         {lid:<5} {cmt}{mark}")

    # ★ 两条自动推理（复现人工复核里最常用的判断）：
    #   1) 注释含 dock/with dock → 多半是「带扩展坞的机型」专用，非本机的可能性高
    #   2) 注释含本机品牌 → 优先候选
    cur_cmt = next((c for l, c in layouts if l == layout), "")
    if re.search(r"\bdock\b", cur_cmt, re.I):
        emit(WARN, f"当前 layout {layout} 的注释含「dock」：{cur_cmt}",
             "★ 这通常是**带扩展坞的特定机型**专用配置，被工具挑中的概率很高。\n"
             "       除非你的机型正是注释里那台，否则建议换掉。")

    brand = hw.get("brand")
    if brand:
        cand = [(l, c) for l, c in layouts
                if re.search(brand, c, re.I) and l != layout]
        if cand:
            s = "、".join(f"{l}（{c}）" for l, c in cand)
            emit(WARN, f"存在注释标注「{brand}」的 layout：{s}",
                 "★ 同品牌/同平台的 layout 通常比通用项更贴合本体；建议优先实测这些。")


# 有线网卡芯片 → 所需 kext / 特殊处理（P1-6）
# 依据：RealtekRTL8111 / LucyRTL8125 / IntelMausi README + Dortania Ethernet 页 +
#       I225-V 自 macOS Ventura 13 起原生支持（需 VT-d；更早系统 FakePCIID 伪装 0x15F2）
WIRED_NIC_MAP = [
    (r"rtl\s?8111|rtl\s?8168|rtl\s?8411", "realtekrtl8111.kext", "Realtek 千兆（RTL8111/8168/8411）"),
    (r"rtl\s?8125", "lucyrtl8125ethernet.kext", "Realtek 2.5GbE（RTL8125）"),
    (r"i219|i211", "intelmausi.kext", "Intel 千兆（I219/I211）"),
    (r"i226", "intelmausi.kext", "Intel 2.5GbE（I226）"),
    (r"i225", None,
     "Intel 2.5GbE（I225-V）：Ventura 13+ 已原生支持（需 VT-d 开启）；\n"
     "       更早系统需 FakePCIID + FakePCIID_Intel_I225-V，device-id 伪装 0x15F2（data 写作 F2150000）"),
    (r"bcm\s?54\d{2}|aquantia|aqc|atl", None,
     "Aquantia/Broadcom 万兆/2.5G：Aquantia 需 Kernel→Quirks→ForceAquantiaEthernet（且需 VT-d）"),
]

# Intel 无线网卡 → 蓝牙三件套（Tahoe/新系统下缺一不可）
INTEL_BT_TRIO = ("intelbluetoothfirmware.kext", "intelbtpatcher.kext", "bluetoolfixup.kext")


def check_nic(hw, cfg):
    hr("网卡兼容性（无线 + 有线）")
    names = kext_names(cfg)
    nic = hw.get("nic")
    is_intel_wifi = False
    if not nic:
        emit(INFO, "报告中未识别无线网卡", "人工确认芯片型号")
    else:
        low = nic.lower()
        if re.search(r"realtek|rtl\s?\d{4}|88(21|22|52|92)\w{0,2}|8723", low):
            emit(FAIL, f"Realtek 无线网卡（{nic}）：macOS 无驱动",
                 "★ 这是硬件硬伤，改 EFI 解决不了 —— Wi-Fi 与蓝牙必然不工作。\n"
                 "       建议换卡：BCM94352Z(DW1560) / DW1820A 走 AirportBrcmFixup（支持 AirDrop/Handoff）；\n"
                 "       Intel 卡走 itlwm 或 AirportItlwm + IntelBluetoothFirmware（无原生 AirDrop）。\n"
                 "       换卡后需在 Kernel→Add 补对应 kext（Lilu → 插件 → AirportBrcmFixup → Brcm* → BlueToolFixup）。")
        elif re.search(r"mediatek|mt79\d{2}|rz6\d{2}", low):
            emit(WARN, f"MediaTek 无线网卡（{nic}）",
                 "蓝牙通常可用，Wi-Fi 属实验性支持，建议换卡求稳。")
        elif re.search(r"intel|ax\d{3}|ac\s?9\d{3}|7265|3160", low):
            is_intel_wifi = True
            emit(OK, f"Intel 无线网卡（{nic}）有社区驱动",
                 "itlwm（配 Wi-Fi profile 可开机自连）或 AirportItlwm；蓝牙需 IntelBluetoothFirmware。\n"
                 "       注意：kext 需按 macOS 版本选，大版本升级要换。")
        elif re.search(r"broadcom|bcm", low):
            emit(OK, f"Broadcom 无线网卡（{nic}）", "免驱型号可直接用；被新系统剔除时需 OCLP root patch。")
        else:
            emit(INFO, f"无线网卡：{nic}", "请人工确认芯片型号与驱动路线")

    # ---- 有线网卡（P1-6）----
    wired = None
    for s in hw.get("raw") or []:
        if re.search(r"(ethernet|rtl\s?81\d{2}|i219|i211|i225|i226|gigabit|2\.5g|pcie gbe)",
                     s, re.I) and not re.search(r"wireless|wi-?fi|802\.11|bluetooth", s, re.I):
            wired = s.strip()
            break
    if not wired:
        emit(INFO, "未识别到板载有线网卡", "USB 网卡 / 纯无线用户可忽略")
    else:
        for pat, kext, desc in WIRED_NIC_MAP:
            if re.search(pat, wired, re.I):
                if kext:
                    if kext in names:
                        emit(OK, f"{desc} → {kext} 在位")
                    else:
                        emit(WARN, f"{desc}（{wired}）：Kernel→Add 未见 {kext}",
                             "★ 不装对应 kext = 有线网卡不工作（macOS 无通用驱动）。\n"
                             "       补 kext 后注意加载顺序：Lilu 先于插件类 kext。")
                else:
                    emit(INFO, f"{desc}", f"识别：{wired}")
                break
        else:
            emit(INFO, f"有线网卡：{wired}", "请人工对照 Dortania Ethernet 页确认驱动路线")

    # ---- Intel 蓝牙三件套完整性（P1-6）----
    if is_intel_wifi:
        missing = [k for k in INTEL_BT_TRIO if k not in names]
        if missing:
            emit(WARN, "Intel 蓝牙三件套不完整，缺：" + "、".join(missing),
                 "★ Tahoe/新系统下 Intel 蓝牙需 IntelBluetoothFirmware + IntelBTPatcher + BlueToolFixup\n"
                 "       三件套齐备才工作；只装 BlueToolFixup 或只装 Firmware 均无效。\n"
                 "       （旧系统可省 IntelBTPatcher/BlueToolFixup——按目标系统版本核对。）")
        else:
            emit(OK, "Intel 蓝牙三件套齐全（Firmware + Patcher + BlueToolFixup）")


def check_platform_info(cfg):
    """P1-7：三码占位/有效性检查。"""
    hr("PlatformInfo 三码（SN / MLB / UUID / ROM）")
    gen = (cfg.get("PlatformInfo", {}) or {}).get("Generic", {}) or {}
    for label in ("SystemSerialNumber", "MLB", "SystemUUID"):
        val = gen.get(label)
        if not val:
            emit(WARN, f"{label} 为空",
                 "★ 占位/未填写：iMessage/FaceTime/激活类服务可能异常。\n"
                 "       请用 ProperTree / OpenCore 按机型生成，绝不可沿用网上 EFI 的三码。")
        elif isinstance(val, str) and (set(val) == {"0"} or val.upper() in
                                       ("CO2CO2CO2CO2", "C02C02C02C02") or len(val) < 8):
            emit(WARN, f"{label} = {val}（疑似占位符）",
                 "★ opcore 类工具的默认占位值，必须换成自己生成的三码。")
        else:
            emit(OK, f"{label} 已填写")
    rom = gen.get("ROM")
    if not rom:
        emit(INFO, "ROM 未填写",
             "建议填本机真实网卡 MAC；与他人重复会引发 Continuity/iMessage 疑难。\n"
             "       留空时确认 UpdateSMBIOSMode 语义（部分配置由 OC 自动生成）。")
    else:
        emit(OK, "ROM 已填写", "确认是本机网卡真实 MAC（非他人 EFI 抄来）")


# 危险启动参数分级表（P1-1）：比「调试参数」更醒目——这些参数会直接改变图形/显卡行为
DANGEROUS_BOOT_ARGS = [
    (r"-igfxvesa", "FAIL",
     "核显强制 VESA：**Metal 完全无加速**（等于白丢核显），最常见于误信「修复花屏」偏方。\n"
     "       正解是修 framebuffer / ig-platform-id / BusID，不是关加速。"),
    (r"-radvesa", "FAIL",
     "AMD 独显强制 VESA：**Metal 完全无加速**，删除。"),
    (r"nv_disable=1", "WARN",
     "禁用 NVIDIA 独显属性注入（10.13 前的老写法）。确认独显确实要禁用才有意义；\n"
     "       现代做法是 WhateverGreen + SSDT 屏蔽，两者留一即可。"),
    (r"-wegnoegpu", "INFO",
     "禁用独显：Optimus 笔记本属合理操作；台式机带独显直出时不应加。"),
]


def check_boot_args(cfg, ba=None):
    """boot-args 分级检查：危险参数（FAIL/WARN/INFO）→ 调试参数（WARN）。"""
    if ba is None:
        ba = boot_args(cfg)
    if not ba:
        emit(INFO, "boot-args 为空", "未配置任何启动参数")
        return
    hit_danger = False
    icon_of = {"FAIL": FAIL, "WARN": WARN, "INFO": INFO}
    for pat, level, note in DANGEROUS_BOOT_ARGS:
        if re.search(pat, ba, re.I):
            hit_danger = True
            emit(icon_of.get(level, FAIL), f"危险启动参数命中：{pat}",
                 f"{note}\n       当前 boot-args: {ba}")
    if re.search(r"(^|\s)-v(\s|$)", ba) or "debug=0x" in ba:
        emit(WARN, f"boot-args 含调试参数：{ba}",
             "-v / debug=0x100 会刷屏并拖慢启动，日常使用建议去掉；\n"
             "       keepsyms=1 可保留（便于读 panic 日志）。")
    elif not hit_danger:
        emit(OK, f"boot-args 未见危险/调试参数：{ba}")


def check_misc(cfg):
    hr("config 杂项")
    sec = (cfg.get("Misc", {}) or {}).get("Security", {}) or {}
    if sec.get("AllowNvramReset") is True:
        emit(OK, "AllowNvramReset = true", "picker 里有 Reset NVRAM 入口")
    else:
        emit(WARN, f"AllowNvramReset = {sec.get('AllowNvramReset')}",
             "★ 改机型 / 改音频 / 改 RTC 后必须 Reset NVRAM。\n"
             "       此项为 false 时 picker 不显示重置入口（即便装了 ResetNvramEntry.efi 也没用）。")

    check_boot_args(cfg)


def check_dgpu(cfg, hw):
    hr("独显处理")
    ba = boot_args(cfg)
    adds = (cfg.get("ACPI", {}) or {}).get("Add", []) or []
    names = " ".join(a.get("Path", "") for a in adds if isinstance(a, dict)).upper()
    dgpu = hw["dgpu"]
    if not dgpu:
        emit(INFO, "报告中未识别到独显", f"boot-args: {ba or '(空)'}")
        return

    disabled = ("wegnoegpu" in ba.lower()) or any(
        k in names for k in ("DGPU", "OFF", "DISABLE"))

    if RE_TURING.search(dgpu):
        emit(INFO, f"检测到 NVIDIA Turing+ 独显（{dgpu}）",
             "可走 NullMoth 实验路线（仅 macOS 15 Sequoia / x86_64）：\n"
             "       · 物理前置：有 mux / 独显直显输出，或台式机；纯 Optimus 本仍无法给内屏加速\n"
             "       · 安装：1401 工具自动改 OC，或手动 install.sh；需调 SIP / SecureBootModel\n"
             "       · ★ AM5 平台必须确认 OpenRuntime DisableVariableWrite 在位（防写主板固件变砖）\n"
             "       · 已知限制：无硬件视频编解码、HDMI/DP 音频未实现；详见 references/nvidia-gpu.md")
        if disabled:
            emit(OK, "当前已禁用该独显",
                 "保守选择合理：无 mux 的 Optimus 本即便架构受支持也无法为内屏加速。\n"
                 "       保留禁用可避免睡眠/唤醒失败与空耗电。")
        return

    if disabled:
        emit(OK, f"已用 -wegnoegpu 禁用独显（{dgpu}）")
    elif any(k in names for k in ("DGPU", "OFF", "DISABLE")):
        emit(OK, f"存在独显禁用 SSDT（{dgpu}）",
             "★ 需确认 _OFF 路径来自本机 DSDT，照抄他人路径会导致独显没真关 → 耗电 + 睡醒失败")
    else:
        emit(FAIL, f"检测到独显（{dgpu}）但未见禁用措施",
             "笔记本 Optimus 独显绝大多数无 mux，无法为内屏加速；\n"
             "       保留它只会导致睡眠/唤醒失败与空耗电。建议禁用。\n"
             "       （若为 Turing+ 且机器有 mux/直显，可评估 NullMoth 实验路线——见 references/nvidia-gpu.md）")


def check_gpu_routes(cfg, hw):
    hr("新 GPU 路线检查（2026-10 起，实验性）")
    names = kext_names(cfg)
    ba = boot_args(cfg).lower()
    wx = "whateverxe.kext" in names
    weg = "whatevergreen.kext" in names

    # 1) WG / WX 互斥（项目明文要求）
    if wx and weg:
        emit(FAIL, "WhateverGreen 与 WhateverXe 同时启用",
             "★ 项目明文互斥（多显卡机器也不例外）——这是**配置错误**，不是兼容性问题。\n"
             "       走 Xe 路线 → 关 WG；走原生路线 → 删 WX。详见 references/gen12-xe.md")
    elif wx:
        emit(OK, "已启用 WhateverXe（与 WhateverGreen 互斥检查通过）")

    # 2) WhateverXe 前置条件
    if wx:
        order = kext_order(cfg)
        if "lilu.kext" in order and order.index("lilu.kext") < order.index("whateverxe.kext"):
            emit(OK, "Lilu 先于 WhateverXe 加载（顺序正确）")
        else:
            emit(WARN, "WhateverXe 加载顺序存疑",
                 "必须 Lilu → WhateverXe；顺序错则插件不工作")
        for flag, note in (("-allow3d", "WhateverXe 必需"),
                           ("-disablegfxfirmware", "WhateverXe 必需"),
                           ("-wex-nopanel", "Tiger Lake 无内屏机器需要；ADL-N 后端不需要")):
            if flag in ba:
                emit(OK, f"boot-args 含 {flag}", note)
            else:
                emit(INFO, f"boot-args 未含 {flag}", note)
        emit(INFO, "OCLP root patch / AMFIPass 需人工确认",
             "WhateverXe 依赖修改版 OCLP 的 root patch + AMFIPass Mod，静态审计看不到，\n"
             "       请对照 references/gen12-xe.md 的组件清单逐项核对")

    # 3) Xe-LP 硬件但无路线
    if hw["igpu"] and RE_XE.search(hw["igpu"]) and not (wx or "nootedgreen" in str(names)):
        emit(INFO, f"检测到 Xe 核显（{hw['igpu']}）但未启用实验路线",
             "macOS 原生不支持该核显：要么接受 VESA 无加速，要么按\n"
             "       references/gen12-xe.md 上 WhateverXe / NootedGreen（实验性，WG 须关）。")

    # 4) Turing+ 独显 + 未装驱动组件
    if hw["dgpu"] and RE_TURING.search(hw["dgpu"]):
        if any("nvidia" in n or "nvmtl" in n for n in names):
            emit(OK, "已含 NVIDIA 驱动相关组件")
        else:
            emit(INFO, "检测到 Turing+ 独显但 config 无 NVIDIA 驱动组件",
                 "若打算启用独显：装 NullMoth 驱动（1401 或手动）并确认目标系统为 macOS 15 Sequoia；\n"
                 "       否则维持禁用（见独显处理一节）。详见 references/nvidia-gpu.md")


def check_kexts(cfg):
    hr("Kext 清单")
    adds = (cfg.get("Kernel", {}) or {}).get("Add", []) or []
    names = set()
    for k in adds:
        if isinstance(k, dict):
            for f in ("BundlePath", "Path"):
                if f in k:
                    names.add(k[f].split("/")[-1].lower())
    for req in ("lilu.kext", "whatevergreen.kext", "virtualsmc.kext"):
        emit(OK, req) if req in names else emit(FAIL, f"缺少 {req}")
    for rec in ("applealc.kext", "nvmefix.kext"):
        emit(OK, rec) if rec in names else emit(INFO, f"未启用 {rec}")
    both = {"voodooi2c.kext", "voodoops2controller.kext"}
    if both & names == both:
        emit(INFO, "VoodooI2C 与 VoodooPS2Controller 同时存在",
             "★ 这**可能是合法的**：笔记本常见「PS2 键盘 + I2C 触控板」组合，此时两套都需要。\n"
             "       判据是本机实际总线（见 intake-questions.md 批 4）：\n"
             "         · 键盘 PS2 + 触控板 I2C → 两套都保留\n"
             "         · 两者同为 PS2 或同为 I2C → 另一套是冗余，删除并同步改 Enabled / 删条目")
    # USB 映射
    if any("utbmap" in n for n in names):
        emit(OK, "已含 UTBMap.kext（端口映射已完成）")
    elif any("utbdefault" in n for n in names):
        emit(WARN, "仍在用 UTBDefault.kext",
             "★ 装后第一步：用 USBToolBox 做端口映射 → 加入 UTBMap.kext → 删除 UTBDefault.kext")
    else:
        emit(INFO, "未发现 USB 映射 kext", "建议装后优先完成端口映射")


def _entry_names(cfg, section):
    """取 Kernel/Add、ACPI/Add 或 UEFI/Drivers 的条目名列表。"""
    adds = (cfg.get(section, {}) or {}).get("Add", []) or []
    out = []
    for k in adds:
        if isinstance(k, dict):
            name = (k.get("BundlePath") or k.get("Path") or "").split("/")[-1]
            if name:
                out.append((name, k))
    return out


def check_enabled_files(cfg, oc_dir):
    """P1-2：Enabled 与文件一致性——「有条目但文件缺失」「Enabled=false 残留」
    「有文件但 config 没引用」三类，这是 SKILL.md 纪律第 8 条的脚本落地。"""
    hr("Enabled × 文件一致性（Kernel/Add · ACPI/Add · UEFI/Drivers）")
    if not oc_dir or not oc_dir.is_dir():
        emit(INFO, "未定位到 OC 目录", "无法核对条目与文件一致性（需要 EFI/OC 目录树）")
        return

    # ---- Kernel > Add ----
    adds = (cfg.get("Kernel", {}) or {}).get("Add", []) or []
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
        if not (oc_dir / "Kexts" / name).exists() and not list(oc_dir.rglob(name))[:1]:
            missing.append(name)
    kdir = oc_dir / "Kexts"
    orphan = []
    if kdir.is_dir():
        on_disk = {p.name for p in kdir.iterdir() if p.suffix in (".kext", ".efi", ".aml")}
        referenced = {((k.get("BundlePath") or k.get("Path") or "").split("/")[-1])
                      for k in adds if isinstance(k, dict)}
        orphan = sorted(on_disk - referenced)

    if disabled:
        emit(WARN, "Kernel/Add 存在 Enabled=false 残留：" + "、".join(disabled),
             "★ 要么删条目，要么确认有意关闭——留着会在升级/迁移时造成「以为装了其实没装」。")
    else:
        emit(OK, "Kernel/Add 无 Enabled=false 残留")
    if missing:
        emit(FAIL, "Kernel/Add 有条目但文件缺失：" + "、".join(missing),
             "★ 文件不在 EFI/OC/Kexts 下 → 该 kext 实际不会加载（启动静默失败）。\n"
             "       要么补文件，要么删条目。")
    else:
        emit(OK, "Kernel/Add 条目均有对应文件")
    if orphan:
        emit(INFO, "Kexts 目录有文件但 config 未引用：" + "、".join(orphan),
             "不加载、无危害；确认无用可删，避免分享 EFI 时泄露/混淆。")

    # ---- ACPI > Add ----
    aadds = (cfg.get("ACPI", {}) or {}).get("Add", []) or []
    a_dis, a_missing = [], []
    for a in aadds:
        if not isinstance(a, dict):
            continue
        name = (a.get("Path") or "").split("/")[-1]
        if not name:
            continue
        if a.get("Enabled") is False:
            a_dis.append(name)
            continue
        if not (oc_dir / "ACPI" / name).exists() and not list(oc_dir.rglob(name))[:1]:
            a_missing.append(name)
    if a_dis:
        emit(WARN, "ACPI/Add 存在 Enabled=false 残留：" + "、".join(a_dis))
    if a_missing:
        emit(FAIL, "ACPI/Add 有条目但文件缺失：" + "、".join(a_missing),
             "★ SSDT 文件缺失 = 该补丁实际没打（电源/EC/USB 行为静默退回原生）。")
    elif aadds:
        emit(OK, "ACPI/Add 条目均有对应文件")

    # ---- UEFI > Drivers ----
    dadds = (cfg.get("UEFI", {}) or {}).get("Drivers", []) or []
    d_dis, d_missing = [], []
    for d in dadds:
        if not isinstance(d, dict):
            continue
        name = (d.get("Path") or "").split("/")[-1]
        if not name:
            continue
        if d.get("Enabled") is False:
            d_dis.append(name)
            continue
        if not (oc_dir / "Drivers" / name).exists() and not list(oc_dir.rglob(name))[:1]:
            d_missing.append(name)
    if d_dis:
        emit(WARN, "UEFI/Drivers 存在 Enabled=false 残留：" + "、".join(d_dis))
    if d_missing:
        emit(FAIL, "UEFI/Drivers 有条目但文件缺失：" + "、".join(d_missing),
             "★ 驱动文件缺失 = 对应能力静默失效（如 OpenRuntime 缺失会破坏 NVRAM 写入）。")
    elif dadds:
        emit(OK, "UEFI/Drivers 条目均有对应文件")


def check_uefi_drivers(cfg):
    """P1-3：UEFI → Drivers 语义审计（OpenRuntime 必备 / OpenCanopy↔PickerMode /
    ResetNvramEntry↔AllowNvramReset 联动 / apfs_aligned 版本提示）。"""
    hr("UEFI → Drivers")
    drv = [(d.get("Path") or "").split("/")[-1].lower()
           for d in ((cfg.get("UEFI", {}) or {}).get("Drivers", []) or [])
           if isinstance(d, dict)]
    names = set(drv)

    if "openruntime.efi" in names:
        emit(OK, "OpenRuntime.efi 在位", "★ NVRAM 写入/虚拟化内存映射依赖它，缺失会破坏大量功能")
    else:
        emit(FAIL, "缺少 OpenRuntime.efi",
             "★ OpenRuntime 是 OC 运行时的核心驱动，缺失会导致 NVRAM 写入失败等一串问题。\n"
             "       从 OpenCorePkg 复制 EFI/OC/Drivers/OpenRuntime.efi 并在 UEFI→Drivers 添加。")

    if "resetnvramentry.efi" in names:
        sec = (cfg.get("Misc", {}) or {}).get("Security", {}) or {}
        if sec.get("AllowNvramReset") is True:
            emit(OK, "ResetNvramEntry.efi + AllowNvramReset=true（联动完整）",
                 "picker 里可用 Reset NVRAM 入口")
        else:
            emit(WARN, "装了 ResetNvramEntry.efi 但 AllowNvramReset 未开",
                 "★ 只装驱动不开开关 = picker 不显示重置入口（本机实机踩过）：\n"
                 "       Misc → Security → AllowNvramReset = true。")
    else:
        sec = (cfg.get("Misc", {}) or {}).get("Security", {}) or {}
        if sec.get("AllowNvramReset") is True:
            emit(WARN, "AllowNvramReset=true 但未装 ResetNvramEntry.efi",
                 "开关开着而驱动缺失，picker 里仍无入口；补 EFI/OC/Drivers/ResetNvramEntry.efi。")

    picker = (cfg.get("Misc", {}) or {}).get("Boot", {}).get("PickerMode")
    if "opencanopy.efi" in names:
        if picker == "External":
            emit(OK, "OpenCanopy.efi + PickerMode=External（配套正确）")
        else:
            emit(WARN, f"OpenCanopy.efi 在位但 PickerMode={picker}",
                 "★ 图形 picker 需 Misc → Boot → PickerMode = External，否则回退文本界面。")
    else:
        emit(INFO, "未启用 OpenCanopy（文本 picker）",
             f"PickerMode={picker}；追求图形界面才需要 OpenCanopy + External。" if picker else
             "可接受；追求图形界面才需要 OpenCanopy + PickerMode=External。")

    if "apfs_aligned.efi" in names:
        emit(INFO, "apfs_aligned.efi 在位",
             "注意与 OpenCore 版本匹配：旧版 apfs_aligned 在部分固件上读不了较新 APFS 容器，\n"
             "       升级 OC 时同步更新该驱动。")
    if "hfsplus.efi" in names:
        emit(INFO, "HfsPlus.efi 在位", "仅 HFS+ 安装镜像/旧卷需要；纯 APFS 场景属冗余。")
    for extra in names - {"openruntime.efi", "resetnvramentry.efi", "opencanopy.efi",
                          "apfs_aligned.efi", "hfsplus.efi", "audiooc.efi",
                          "crbluetoothoc.efi", "opensbios.efi"}:
        emit(INFO, f"额外驱动：{extra}", "确认用途后保留；不明驱动建议查 OpenCorePkg 文档。")


# ---------------------------------------------------------------- ACPI 解析（Phase 3 升级）
# External 声明类型 → DSDT 里应有的对象类型（P1-10：类型不符会导致 SSDT 加载失败）
EXT_TYPE_MAP = {
    "DeviceObj": {"Device"},
    "ProcessorObj": {"Processor"},
    "ScopeObj": {"Scope"},
    "MethodObj": {"Method"},
    "NameObj": {"Name"},
    "IntObj": {"Name"}, "StrObj": {"Name"}, "BuffObj": {"Name"}, "PkgObj": {"Name"},
    "FieldUnitObj": {"FieldUnit", "Field"},
    "EventObj": {"Event"},
    "MutexObj": {"Mutex"},
    "OpRegionObj": {"OperationRegion"},
    "PowerResObj": {"PowerResource"},
    "ThermalZoneObj": {"ThermalZone"},
}

_ACPI_TOKEN = re.compile(
    r"(?P<open>\{)|(?P<close>\})|"
    r"\b(?P<kind>Scope|Device|Processor|Method|Name|PowerResource|ThermalZone|"
    r"Event|Mutex|OperationRegion|Field)\s*\(\s*"
    r"(?P<name>\\?_?(?:SB_)?[.A-Za-z0-9_\\]+)\s*[,)]")


def _strip_sb(name: str) -> str:
    r"""去掉路径名里的 _SB_/_SB 前缀（\_SB.PCI0、\_SB_、嵌套写法归一化；纯 _SB 返回空）。"""
    n = name.strip().strip("\\")
    if n in ("_SB", "SB"):
        return ""
    for pre in ("_SB_.", "_SB.", "SB_."):
        if n.startswith(pre):
            return n[len(pre):]
    if n.startswith("_SB_"):
        return n[4:]
    return n


def parse_acpi_declarations(text: str):
    """极简 ACPI 声明解析（花括号配平 + Scope/Device/Processor 嵌套跟踪）。
    返回 {规范化全路径: 对象类型}，如 {'PCI0.LPCB.EC0': 'Device', 'PR00': 'Processor'}。
    覆盖 iasl 产物的两种写法：平铺 Device(\\_SB.PCI0) 与嵌套 Scope(\\_SB){Device(PCI0)}。"""
    decls = {}
    frames = []  # [kind, name, state]；state: 'pending'（等 body 的 {）/'open'/'anon'

    def path_of(upto):
        parts = []
        for f in upto:
            # ★ 只有 open 帧（有 body 的容器）算父级；pending 的叶子声明
            #   （Name (X, 0) 等无花括号）不当父级，否则路径被错误串成长链
            if f[0] == "__anon__" or f[2] != "open":
                continue
            nm = _strip_sb(f[1] or "")
            if nm:
                parts.append(nm)
        return ".".join(parts)

    for m in _ACPI_TOKEN.finditer(text):
        if m.group("open"):
            if frames and frames[-1][2] == "pending":
                frames[-1][2] = "open"
            else:
                frames.append(["__anon__", None, "open"])
        elif m.group("close"):
            for i in range(len(frames) - 1, -1, -1):
                if frames[i][2] == "open":
                    if frames[i][0] != "__anon__":
                        decls.setdefault(path_of(frames[:i + 1]), frames[i][0])
                    del frames[i:]
                    break
        else:
            kind, name = m.group("kind"), m.group("name")
            parent = path_of(frames)
            nm = _strip_sb(name)
            own = f"{parent}.{nm}" if parent else nm
            decls.setdefault(own, kind)
            frames.append([kind, nm, "pending"])
    return decls


def iasl_decompile(aml_path: Path):
    """有 iasl 就自动反编译 .aml → .dsl 文本；否则返回 (None, 原因)。
    ★ 必须把 .aml 拷进临时目录后用**相对名**调用：iasl 对「绝对路径 + 外部 cwd」
      不会在 cwd 落盘（实测 20240827 版行为），直接传绝对路径会误判「未产出」。"""
    iasl = shutil.which("iasl")
    if not iasl:
        return None, ("iasl 不在 PATH（macOS: brew install acpica；"
                      "Linux: apt install acpica-tools；Windows: 用 iasl.exe 后重跑）")
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / aml_path.name
        try:
            shutil.copy2(aml_path, local)
            subprocess.run([iasl, "-d", local.name], cwd=td,
                           capture_output=True, timeout=180)
        except Exception as e:
            return None, f"iasl 反编译失败: {e}"
        dsl = Path(td) / (aml_path.stem + ".dsl")
        if not dsl.is_file():
            return None, "iasl 未产出 .dsl（表可能损坏）"
        return dsl.read_text(errors="ignore"), None


def check_acpi_patches(root: Path, cfg):
    """P1-8：ACPI>Patch 的 Find 是否真的在表里命中（补丁不命中 = 没打）。"""
    hr("ACPI → Patch 命中检查（重命名补丁是否真的打到表上）")
    patches = [p for p in ((cfg.get("ACPI", {}) or {}).get("Patch", []) or [])
               if isinstance(p, dict)]
    if not patches:
        emit(INFO, "config 中无 ACPI>Patch", "纯 SSDT 路线正常；用过 GFX0→IGPU / SAT0→SAT1 等改名补丁才需逐条核对")
        return
    acpi_dir = None
    for cand in ("SysReport/ACPI", "SysReport/acpi", "ACPI", "Results/ACPI"):
        p = root / cand
        if p.is_dir():
            acpi_dir = p
            break
    if acpi_dir is None:
        emit(WARN, "未找到 ACPI dump 目录", "无法核对 Find 命中；导出 ACPI 表后重跑")
        return
    tables = {}
    for f in sorted(acpi_dir.iterdir()):
        if f.is_file() and f.suffix.lower() == ".aml":
            tables[f.name.lower()] = f.read_bytes()
    if not tables:
        emit(WARN, "ACPI 目录无 .aml 表", "只有 .dsl 时本项跳过（Find 是二进制，无法在文本里核）")
        return
    hit_n = 0
    for p in patches:
        cmt = p.get("Comment") or "(无注释)"
        if p.get("Enabled") is False:
            emit(INFO, f"补丁已禁用，跳过：{cmt}")
            continue
        find = p.get("Find")
        if not isinstance(find, (bytes, bytearray)) or not find:
            emit(INFO, f"补丁无有效 Find：{cmt}", "Base64 模式下确认内容正确")
            continue
        hits = [n for n, b in tables.items() if bytes(find) in b]
        if hits:
            hit_n += 1
            emit(OK, f"补丁命中：{cmt}",
                 f"Find {len(find)}B 命中 " + "、".join(hits[:4]))
        else:
            emit(WARN, f"补丁未命中：{cmt}",
                 f"Find {bytes(find).hex()[:32]}… 未在任何 .aml 表中出现 → **补丁等于没打**。\n"
                 "       确认目标表（DSDT vs SSDT）与 OEM BIOS 版本是否一致。")
    emit(INFO, "说明", f"共核对 {len(patches)} 条补丁，命中 {hit_n} 条。")


def check_ssdt_paths(root: Path, cfg, oc_dir=None):
    """Phase 3（P1-10 升级）：SSDT 外部引用核对升级为
    完整路径 + 对象类型 + 重名检测；.aml 有 iasl 就自动反编译。
    SSDT 源码查找范围：ACPI dump 目录 → EFI/OC/ACPI（config 实际加载的那些）。"""
    hr("SSDT 引用路径核对（完整路径 + 对象类型 + 重名）")
    adds = (cfg.get("ACPI", {}) or {}).get("Add", []) or []
    cfg_ssdts = [a.get("Path", "") for a in adds if isinstance(a, dict)]
    if not cfg_ssdts:
        emit(INFO, "config 中未启用任何 SSDT")
        return

    acpi_dir = None
    for cand in ("SysReport/ACPI", "SysReport/acpi", "ACPI", "Results/ACPI"):
        p = root / cand
        if p.is_dir():
            acpi_dir = p
            break
    if acpi_dir is None:
        emit(WARN, "未找到 ACPI dump 目录（SysReport/ACPI）",
             "无法核对 SSDT 引用的路径是否存在于本机 DSDT。\n"
             "       请用 Hardware Sniffer / SSDTTime 导出 ACPI 表后重跑。")
        return
    ssdt_dirs = [acpi_dir]
    if oc_dir and (oc_dir / "ACPI").is_dir():
        ssdt_dirs.append(oc_dir / "ACPI")

    dsdt = find_first(acpi_dir, "DSDT.dsl", "DSDT.aml", "*DSDT*.dsl", "*dsdt*.dsl")
    if dsdt is None:
        emit(WARN, "ACPI 目录中未找到 DSDT", f"目录: {acpi_dir}")
        return

    if dsdt.suffix.lower() == ".aml":
        text, why = iasl_decompile(dsdt)
        if text is None:
            emit(WARN, f"ACPI dump 未反编译且无法自动反编译（{why}）",
                 "→ 未做该项检查（不要误以为已通过）。手动处理：iasl -d DSDT.aml SSDT-*.aml 后重跑。")
            return
        emit(INFO, f"已用 iasl 自动反编译 {dsdt.name}")
    else:
        text = dsdt.read_text(errors="ignore")
    print(f"  DSDT: {dsdt.name}")

    decls = parse_acpi_declarations(text)
    checked = 0
    for name in cfg_ssdts:
        stem = Path(name).stem
        f = None
        for d in ssdt_dirs:
            f = find_first(d, f"{stem}.dsl", f"{stem}.aml")
            if f:
                break
        if f is None:
            emit(INFO, f"{name}：dump 与 EFI/OC/ACPI 均无同名文件，跳过路径核对")
            continue
        if f.suffix.lower() == ".aml":
            stext, why = iasl_decompile(f)
            if stext is None:
                emit(INFO, f"{name}：仅 .aml 且无法自动反编译（{why}），跳过",
                     "安装 iasl（macOS: brew install acpica）后重跑可自动核对")
                continue
        else:
            stext = f.read_text(errors="ignore")
        checked += 1

        # 1) External 引用：完整路径 + 对象类型
        refs = re.findall(
            r"External\s*\(\s*((?:\\?_SB_)?[.\\A-Za-z0-9_]+)\s*,\s*([A-Za-z]+)\s*\)",
            stext)
        problems = []
        skipped_bare = 0
        for ref_path, ref_type in refs:
            # 裸名引用（既无路径分隔符也无 _SB）：其解析规则无法可靠静态建模
            # （典型：SSDT-RTCAWAC 的 External (STAS, IntObj)，STAS 实为 DSDT
            #  深处 OperationRegion 里的 Field 单元）——跳过严格核对，不误报
            if "." not in ref_path and "_SB" not in ref_path:
                skipped_bare += 1
                continue
            norm = ".".join(_strip_sb(s) for s in ref_path.split(".") if _strip_sb(s))
            if not norm:
                continue
            got = decls.get(norm)
            if got is None:
                problems.append(f"{ref_path}：DSDT 中不存在（完整路径未命中）")
            elif ref_type in EXT_TYPE_MAP and got not in EXT_TYPE_MAP[ref_type]:
                problems.append(
                    f"{ref_path}：声明为 {ref_type} 但 DSDT 里是 {got}（类型不符→SSDT 加载失败）")
        if problems:
            emit(FAIL, f"{name}：外部引用有问题",
                 "· " + "\n· ".join(problems[:5]) + "\n"
                 "       ★ 引用不存在路径/类型不符的 SSDT 要么无效，要么触发 AE_NOT_FOUND/AE_ALREADY_EXISTS。\n"
                 "       建议先禁用，实测后再决定（详见 references/ssdt-audit.md）。")
            continue

        # 2) 重名检测：SSDT 新建 Device/Processor 与 DSDT 同路径 → AE_ALREADY_EXISTS
        #    不限于根级——经典翻车是 DSDT 已有 EC，又叠加 SSDT-EC 的
        #    Scope(\_SB.PCI0.LPCB){Device(EC)} → 整表加载失败。
        #    只查 Device/Processor：同名 Method 重定义是合法覆盖手法（如 SSDT-USB-Reset
        #    覆盖 RHUB._STA），不能算冲突。
        sdecls = parse_acpi_declarations(stext)
        clashes = [p for p, k in sdecls.items()
                   if k in ("Device", "Processor") and p in decls]
        if clashes:
            emit(WARN, f"{name}：新建对象与 DSDT 同路径重名：" + "、".join(clashes[:5]),
                 "★ 同路径重复 Device/Processor 会触发 AE_ALREADY_EXISTS → 该 SSDT 整表加载失败。\n"
                 "       若本意是改已有的对象，应改用 _STA/方法补丁而非重新定义。")

        emit(OK, f"{name}：外部引用在 DSDT 中均能找到（完整路径 + 类型）"
             + (f"（另有 {skipped_bare} 条裸名引用跳过严格核对）" if skipped_bare else ""))

    emit(INFO, "说明", f"已核对 {checked} 份 SSDT。基于 iasl 反编译产物的静态解析，\n"
                       "       仍建议对 FAIL/WARN 条目人工阅读 DSDT 确认。")


# ---------------------------------------------------------------- ocvalidate

def find_ocvalidate(cfg_path=None, root=None):
    """定位 ocvalidate：PATH → 审计目录附近（OpenCorePkg/Utilities/ocvalidate/）。

    Dortania 要求交付前必须过 ocvalidate（比 plutil -lint 严格得多），
    但多数人不会手动跑——找到就自动跑，找不到明确提示（不静默跳过）。"""
    exe = shutil.which("ocvalidate")
    if exe:
        return exe
    pats = []
    if root:
        pats.append(str(root / "**/Utilities/ocvalidate/ocvalidate"))
    if cfg_path:
        try:
            for up in cfg_path.resolve().parents[:4]:
                pats.append(str(up / "**/Utilities/ocvalidate/ocvalidate"))
        except Exception:
            pass
    for pat in pats:
        for hit in sorted(glob.glob(pat, recursive=True)):
            if Path(hit).is_file():
                return hit
    return None


def check_ocvalidate(cfg_path):
    """自动调用 ocvalidate 校验 config.plist（P2-6）。"""
    hr("ocvalidate 自动校验")
    if not cfg_path or not Path(cfg_path).is_file():
        emit(INFO, "无 config.plist 可校验", "跳过 ocvalidate")
        return
    exe = find_ocvalidate(cfg_path)
    if not exe:
        emit(INFO, "未找到 ocvalidate，未做该项检查",
             "装 OpenCorePkg 后从其 Utilities/ocvalidate/ 取二进制（或加入 PATH）重跑；\n"
             "       ocvalidate 不过不要交付。")
        return
    try:
        r = subprocess.run([exe, str(cfg_path)], capture_output=True,
                           text=True, timeout=120)
    except Exception as e:
        emit(WARN, f"ocvalidate 执行失败: {e}", f"命令: {exe} {cfg_path}")
        return
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-12:]) if out else "(无输出)"
    if r.returncode == 0:
        emit(OK, f"ocvalidate 通过（{Path(exe).name}）",
             "config.plist 的 schema/语义校验全部通过")
    else:
        emit(FAIL, f"ocvalidate 未通过（exit {r.returncode}）",
             f"命令: {exe} {cfg_path}\n       ---- 输出末尾 ----\n       {tail}")


# ---------------------------------------------------------------- XHCI

# Dortania（ktext.md → USB）原文口径：
#   XHCI-unsupported.kext "Needed for non-native USB controllers"；
#   "AMD CPU based systems don't need this"；
#   常见需要的芯片组：H370 / B360 / H310 / Z390（Mojave 及更新不需要）/ X79 / X99 /
#   ASRock Intel 主板（B460/Z490+ 不需要）。
NONNATIVE_CHIPSET_RE = re.compile(r"\b(H370|B360|H310|Z390|X79|X99)\b", re.I)
NATIVE_400_SERIES_RE = re.compile(r"\b([BHQZWM]4\d{2}|W480)\b", re.I)
AMD_CPU_RE = re.compile(r"\b(AMD|Ryzen|Threadripper|EPYC|Athlon)\b", re.I)


def check_xhci_unsupported(cfg, hw):
    """P2-7：XHCI-unsupported.kext 冗余提示（Dortania：仅非原生 USB 控制器需要）。"""
    hr("XHCI-unsupported 冗余检查")
    names = kext_names(cfg)
    if "xhci-unsupported.kext" not in names:
        emit(INFO, "未启用 XHCI-unsupported.kext",
             "仅非原生 USB 控制器需要它（Dortania 清单：H370/B360/H310/Z390(pre-Mojave)/X79/X99/部分 ASRock）。")
        return

    cpu = hw.get("cpu") or ""
    raw = " ".join(hw.get("raw") or [])
    chipset = (hw.get("chipset") or "")
    board = (hw.get("board_name") or "")
    is_amd = bool(AMD_CPU_RE.search(cpu)) or bool(AMD_CPU_RE.search(raw[:4000]))
    gen = cpu_generation(cpu)
    is_icelake_plus = (gen is not None and gen >= 11) or bool(
        re.search(r"i[3579]-10\d{2,3}[A-Z]*G\d?", cpu or ""))

    if is_amd:
        emit(WARN, "XHCI-unsupported.kext 冗余：AMD 平台",
             "Dortania 明示「AMD CPU based systems don't need this」——AMD 系统移除该 kext。")
        return
    if is_icelake_plus:
        emit(WARN, "XHCI-unsupported.kext 冗余：Ice Lake 及更新平台",
             "Ice Lake+（10 代 G 系 / 11 / 12 代）为 macOS 原生 XHCI 控制器，\n"
             "       Dortania 的原则是「仅非原生控制器需要」；确认 USB 全部口可用后可移除。")
        return
    if NONNATIVE_CHIPSET_RE.search(chipset) or NONNATIVE_CHIPSET_RE.search(board):
        extra = ("（注意：Z390 在 Mojave 及更新版本上已不需要）"
                 if re.search(r"Z390", chipset + board, re.I) else "")
        emit(OK, f"XHCI-unsupported.kext 有存在理由：芯片组 {chipset or board} 在 Dortania 非原生清单内{extra}",
             "H370/B360/H310/Z390(pre-Mojave)/X79/X99 属非原生 USB 控制器，保留合理。")
        return
    if NATIVE_400_SERIES_RE.search(chipset):
        emit(WARN, f"XHCI-unsupported.kext 冗余：芯片组 {chipset}（400 系）",
             "Dortania 明示 B460/Z490+ 主板不需要该 kext（原生 USB 控制器）；\n"
             "       确认 USB 各口正常后可移除。")
        return
    emit(INFO, "XHCI-unsupported.kext 已启用，但未命中 Dortania 非原生清单",
         f"芯片组识别: {chipset or '(报告未给出)'}。 Dortania 清单：H370/B360/H310/Z390(pre-Mojave)/X79/X99/\n"
         "       部分 ASRock Intel 主板；其余（含 100/200/300 系主流桌面与笔记本 PCH）多为原生控制器。\n"
         "       若 USB 各口实测正常，可移除该 kext 做减法；确有控制器不被识别再保留。")


# ---------------------------------------------------------------- main

def main():
    global JSON_MODE
    ap = argparse.ArgumentParser(
        description="静态审计 opcore-simplify 产物（只读，装机前可用）")
    ap.add_argument("root", nargs="?", help="OpCore-Simplify 目录（含 Results/ 与 SysReport/）")
    ap.add_argument("--config", help="直接指定 config.plist")
    ap.add_argument("--report", help="直接指定 Report.json")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON（findings 列表 + 汇总），抑制文本报告")
    a = ap.parse_args()

    root = Path(a.root) if a.root else Path(".")
    if not root.is_dir():
        print(f"{FAIL} 目录不存在: {root}")
        return 1

    cfg_path = Path(a.config) if a.config else find_first(
        root, "Results/EFI/OC/config.plist", "Results/*/EFI/OC/config.plist",
        "*/EFI/OC/config.plist", "EFI/OC/config.plist")
    rep_path = Path(a.report) if a.report else find_first(
        root, "SysReport/Report.json", "*/Report.json", "Report.json")

    cfg = None
    if cfg_path and cfg_path.is_file():
        try:
            cfg = load_config(cfg_path)
        except Exception as e:
            print(f"{FAIL} 读取 config.plist 失败: {e}")
    else:
        print(f"\n{FAIL} 未找到 config.plist，无法审计 EFI。")

    hw = {"cpu": None, "igpu": None, "dgpu": None, "codec": None, "nic": None}
    if rep_path and rep_path.is_file():
        try:
            hw = sniff_hardware(json.loads(rep_path.read_text(errors="ignore")))
        except Exception as e:
            print(f"{WARN} 解析 Report.json 失败: {e}")
    else:
        print(f"{WARN} 未找到 Report.json，跳过硬件事实与三轴复核。")

    model = ""
    if cfg:
        model = (cfg.get("PlatformInfo", {}).get("Generic", {}) or {}).get(
            "SystemProductName", "")

    JSON_MODE = bool(a.json)
    if JSON_MODE:
        # 检查过程中的直接 print（表头/提示）吞进缓冲区，只保留 findings
        with contextlib.redirect_stdout(io.StringIO()):
            if cfg:
                run_checks(cfg, hw, root, cfg_path, model)
    else:
        print("=" * 68)
        print("opcore-simplify 产物静态审计（只读，不修改任何文件）")
        print("=" * 68)
        print(f"  根目录 : {root}")
        print(f"  config : {cfg_path or '❌ 未找到'}")
        print(f"  report : {rep_path or '❌ 未找到'}")
        if cfg:
            run_checks(cfg, hw, root, cfg_path, model)
        print("\n" + "=" * 68)
        print("注意：本工具只做静态审查，不能替代真机实测。")
        print("      装机后的回归重点：盒盖睡眠与唤醒、离电续航、外接显示器、USB 端口。")
        print("=" * 68)
        print("\n★ 交付前校验：用 OpenCorePkg 自带的 ocvalidate 对 config.plist 做完整 schema/语义校验")
        print("  （ocvalidate 比 plutil -lint 更严格，能抓 quirks / kext 引用 / 字段类型类错误）。")
        print("  ocvalidate 不通过不要交付；用法：ocvalidate <path>/config.plist")
        return 0

    summary = {k: sum(1 for f in FINDINGS if f["level"] == k)
               for k in ("ok", "warn", "fail", "info")}
    print(json.dumps({
        "tool": "audit_opcore_result",
        "version": "1.0.1",
        "root": str(root),
        "config": str(cfg_path) if cfg_path else None,
        "report": str(rep_path) if rep_path else None,
        "model": model,
        "summary": summary,
        "findings": FINDINGS,
    }, ensure_ascii=False, indent=2))
    return 0 if summary.get("fail", 0) == 0 else 2


def run_checks(cfg, hw, root, cfg_path, model):
    plat = platform_of(model)
    check_two_axis(cfg, hw)
    check_igpu_props(cfg, plat)
    check_quirks(cfg, plat)
    check_misc(cfg)
    check_security(cfg, model)
    check_audio_layout(cfg, hw, cfg_path)
    check_nic(hw, cfg)
    check_dgpu(cfg, hw)
    check_gpu_routes(cfg, hw)
    check_kexts(cfg)
    check_enabled_files(cfg, cfg_path.parent if cfg_path else None)
    check_uefi_drivers(cfg)
    check_platform_info(cfg)
    check_acpi_patches(root, cfg)
    check_ssdt_paths(root, cfg, cfg_path.parent if cfg_path else None)
    check_xhci_unsupported(cfg, hw)
    check_ocvalidate(cfg_path)


if __name__ == "__main__":
    sys.exit(main())
