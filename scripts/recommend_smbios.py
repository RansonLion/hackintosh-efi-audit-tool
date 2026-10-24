#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recommend_smbios.py — 黑苹果 SMBIOS（仿冒 Mac 机型）推荐工具（笔记本 / 台式机通用）

依据「三轴决策」：
  轴一：核显（iGPU）代次   → 决定系统稳定性
        （SMBIOS 声明代次新于物理核显 → CoreML/Vision 选错 kernel → stall → gpuRestart → 睡死）
  轴二：CPU 真实 TDP 档位 → 笔记本决定风扇曲线 / 续航；桌面决定 iGPU-only / +dGPU 取舍
  轴三：macOS 版本资格    → 机型决定系统升级资格（Tahoe 只支持 16,1/16,2/16,4 + iMac20,x/MacPro7,1）

用法：
  python3 recommend_smbios.py --cpu "i7-10510U"
  python3 recommend_smbios.py --cpu "i3-10105" --platform desktop --os tahoe   # 台式机目标 Tahoe
  python3 recommend_smbios.py --cpu "i5-10400" --report SysReport/Report.json   # 无后缀 CPU：报告平台优先
  python3 recommend_smbios.py --igpu "UHD 620" --tdp 15
  python3 recommend_smbios.py --interactive
  python3 recommend_smbios.py --list
"""

import argparse
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 机型表：单一数据源 scripts/models.json（board-id 依 Apple 官方规格页 +
#   Mr. Macintosh BoardID 列表核对，2026-10-23）。
#   16,2 = 2020 13" 四雷雳3 = Ice Lake 28W/Gen11；16,3 = 2020 13" 双雷雳3 = CFL-U 15W/Gen9.5
#   （此前版本曾把 16,2/16,3 身份与 board-id 弄反，已修正）
#   recommended=false 的机型仅作审计对照，不进推荐候选池。
# --------------------------------------------------------------------------
def load_models():
    p = Path(__file__).resolve().parent / "models.json"
    if not p.is_file():
        print(f"❌ 缺少机型数据源: {p}（应与脚本同目录分发）")
        sys.exit(1)
    all_models = json.loads(p.read_text(encoding="utf-8")).get("models", [])
    # 兼容旧字段：board_id=null 时展示为「OC 自动生成」
    for m in all_models:
        m.setdefault("form_factor", "laptop")
        m.setdefault("board_id", None)
    return [m for m in all_models if m.get("recommended")], all_models


MODELS, ALL_MODELS = load_models()

# Alder Lake-N / Twin Lake（N100 / N200 / i3-N305）→ Gen12 Xe-LP（WhateverXe 支持列表）
# Jasper Lake（N4120/N5095/N5100/N6000 等）→ Gen11。两者都不走 i3/i5/i7 命名体系。
ADL_N_CPU = re.compile(r"\b(?:I3-)?N(?:100|200|305)\b", re.I)
JASPER_LAKE_CPU = re.compile(r"\bN(?:4120|5095|5100|5105|6000|6005)\b", re.I)

# 后缀 → TDP 档位（NG = Ice Lake MacBook 专用 SKU，如 i7-1068NG7，28W）
# 桌面后缀：K=未锁频(95W+)、F=无核显(65W)、S=节能(65W)、T=超低功耗(35W)、X=发烧(112W)
SUFFIX_TDP = {"Y": 5, "U": 15, "H": 45, "HK": 45, "HQ": 45,
              "G1": 15, "G4": 28, "G7": 28, "NG": 28,
              "K": 95, "F": 65, "S": 65, "T": 35, "X": 112}

# CPU 代次 → (平台, iGPU 代次)
GEN_PLATFORM = {
    6:  ("Skylake 6代", "Gen9"),
    7:  ("Kaby Lake 7代", "Gen9.5"),
    8:  ("Coffee Lake / Whiskey Lake 8代", "Gen9.5"),
    9:  ("Coffee Lake Refresh 9代", "Gen9.5"),
    10: ("Comet Lake-U 10代（若后缀为 G 系则为 Ice Lake）", "Gen9.5"),
    11: ("Tiger Lake 11代", "Gen12"),
}


def parse_cpu(cpu: str):
    """从 CPU 型号串解析 (代次, 后缀, 数字部分)。
    4 位型号里 "10xx/11xx/12xx"（如 1068NG7 / 1185G7 / 1240P）是 10 代及更新，
    不能按首位取世代（旧版把 1185G7 误判成第 1 代）；2xxx~9xxx 首位即代次。
    Alder Lake-N / Jasper Lake（N100 / i3-N305 / N5100 等）不走 i3/i5/i7 体系，
    单独识别：后缀返回 ADLN / JSP，代次按 Gen12 / Gen11 记。"""
    s = cpu.strip().upper()
    if ADL_N_CPU.search(s):
        return 12, "ADLN", "ADL-N"
    if JASPER_LAKE_CPU.search(s):
        return 11, "JSP", "Jasper Lake"
    m = re.search(r"I[3579]-([0-9]{4,5})([A-Z0-9]{0,2})", s)
    if not m:
        return None, None, None
    num, suffix = m.group(1), m.group(2)
    if len(num) == 5 or (len(num) == 4 and num[0] == "1"):
        gen = int(num[:2])
    else:
        gen = int(num[0])
    return gen, suffix, num


# 核显型号数字 → 代次（与 audit_opcore_result.py 的 IGPU_MODEL_GEN 保持一致；
# 补录 P2-3：Gemini Lake UHD 605/610 = Gen9.5；Rocket Lake-S UHD 710/730/750
# 与 Alder Lake-S UHD 770 = Gen12）
IGPU_NUM_GEN = {
    # Haswell Gen7.5 / Broadwell Gen8
    4200: "Gen7.5", 4400: "Gen7.5", 4600: "Gen7.5",
    5000: "Gen7.5", 5100: "Gen7.5", 5200: "Gen7.5",
    5300: "Gen8", 5500: "Gen8", 5600: "Gen8",
    6000: "Gen8", 6100: "Gen8", 6200: "Gen8",
    # Skylake Gen9
    510: "Gen9", 515: "Gen9", 520: "Gen9", 530: "Gen9",
    540: "Gen9", 550: "Gen9", 580: "Gen9",
    # Kaby/CFL/WKL/CML/Amber/Gemini Lake → Gen9.5
    605: "Gen9.5", 610: "Gen9.5", 615: "Gen9.5", 617: "Gen9.5",
    620: "Gen9.5", 630: "Gen9.5",
    # Rocket Lake-S / Alder Lake-S → Gen12（Xe-LP，无原生驱动）
    710: "Gen12", 730: "Gen12", 750: "Gen12", 770: "Gen12",
}


def igpu_gen_from_name(igpu: str):
    """核显名称 → iGPU 代次。
    ★ 兼容 'UHD 630' 与 'UHD Graphics 630' 两种写法——旧版用 "uhd 630" 子串匹配，
      全名（中间有 graphics）会漏判成「无法识别」；现按型号数字查表。
    Iris Plus 6xx = Coffee Lake(Gen9.5)；无数字的 Iris Plus Graphics = Ice Lake(Gen11)。"""
    g = igpu.strip().lower()
    if "iris xe" in g:
        return "Gen12"
    if "iris plus" in g:
        # 带 6xx 数字的是 Coffee Lake 的 Iris Plus 655/640（Gen9.5）
        if re.search(r"iris plus\s*(?:graphics\s*)?6\d\d", g):
            return "Gen9.5"
        return "Gen11"
    m = re.search(r"(?:uhd|hd)\s*(?:graphics\s*)?(\d{3,4})", g)
    if m:
        return IGPU_NUM_GEN.get(int(m.group(1)))
    return None


def recommend(igpu=None, tdp=None, cpu=None, os_tahoe=False, platform_type=None,
              has_dgpu=False, report_platform=None):
    """返回 (推荐机型 dict, 候选列表, 被排除列表, 警告列表)。
    os_tahoe=True 时按轴三过滤：仅保留 Tahoe(macOS 26) 支持的机型。
    platform_type: None / "laptop" / "desktop" —— 限定候选池（台式机只从 desktop 机型里选）。
    has_dgpu: 台式机带独显时，优先 iMac20,2 / MacPro7,1 这类出厂带 dGPU 的机型。
    report_platform: Report.json 的 Motherboard.Platform 值（如 "Desktop"），
                     平台信号优先级最高（高于 CPU 后缀推断）。

    ★ 平台信号优先级（P0-2 修复）：Report.json Platform > --platform > CPU 后缀。
      无后缀 CPU（i5-10400 / i7-10700 等桌面主流型号）无法从型号判断平台——
      移动版必带 U/Y/H/G 后缀——此时**不静默按 15W 笔记本**，而是：
      TDP 暂按桌面 65W 档计、给出双平台候选、并要求用户确认（ambiguous=True，
      表现为 chosen=None 且报告同时展示笔记本/台式机两套候选）。"""
    warns = []
    ambiguous = False
    gen12_route = False   # Gen12 硬件（ADL-N）落 Gen11 机型的实验路线

    # 平台信号 1：硬件报告 Motherboard.Platform（最可靠）
    if platform_type is None and report_platform:
        rp = (report_platform or "").strip().lower()
        if rp.startswith("desk") or rp in ("tower", "workstation"):
            platform_type = "desktop"
            warns.append("★ 由硬件报告 Motherboard.Platform 判定为台式机平台。")
        elif rp.startswith("lap") or rp in ("notebook", "portable"):
            platform_type = "laptop"
            warns.append("★ 由硬件报告 Motherboard.Platform 判定为笔记本平台。")

    # 平台信号 2：CPU 后缀
    if platform_type is None and cpu:
        _, suffix, _ = parse_cpu(cpu)
        if suffix:
            if suffix in ("ADLN", "JSP"):
                # Alder Lake-N / Jasper Lake 只做移动/小主机，无桌面 SKU
                platform_type = "laptop"
                warns.append(f"★ 由 CPU「{cpu}」识别为 {suffix}（移动/小主机平台），按笔记本处理。")
            elif suffix[0] in ("K", "F", "S", "T", "X"):
                platform_type = "desktop"
                warns.append("★ 由 CPU 后缀推断为台式机平台（如非，请显式 --platform）。")
            elif suffix[0] in ("U", "Y", "H", "G", "P") or suffix == "NG":
                platform_type = "laptop"
                warns.append("★ 由 CPU 后缀推断为笔记本平台（如非，请显式 --platform）。")
        else:
            ambiguous = True
            warns.append(
                "⚠️ 无法从 CPU 型号判断平台：无后缀型号（如 i5-10400 / i7-10700）\n"
                "     桌面与笔记本皆有可能——移动版必带 U/Y/H/G 后缀。\n"
                "     TDP 暂按桌面 65W 档计，下面同时给出笔记本与台式机两套候选；\n"
                "     请用 --platform laptop/desktop 或 --report Report.json 明确后重跑。")

    if cpu:
        gen, suffix, _ = parse_cpu(cpu)
        if gen is None:
            raise ValueError(f"无法从 CPU 型号解析代次: {cpu}")
        if not tdp:
            if suffix in SUFFIX_TDP:
                tdp = SUFFIX_TDP[suffix]
            elif suffix == "ADLN":
                tdp = 15   # Alder Lake-N 6–15W（N305 最高 15W）
            elif suffix == "JSP":
                tdp = 10   # Jasper Lake 6–10W
            elif suffix.startswith("G"):
                tdp = 28
            elif platform_type == "desktop" or ambiguous:
                tdp = 65
            else:
                tdp = 15
        if suffix == "ADLN":
            platform, igpu_gen = "Alder Lake-N（Xe-LP Gen12 无原生驱动）", "Gen12"
        elif suffix == "JSP":
            platform, igpu_gen = "Jasper Lake（Tremont，Gen11 无原生驱动）", "Gen11"
        elif gen == 10 and suffix and (suffix.startswith("G") or suffix.startswith("N")):
            # G1/G4/G7 与 NG（如 i7-1068NG7）都是 Ice Lake 专用后缀
            platform, igpu_gen = "Ice Lake", "Gen11"
        elif gen in GEN_PLATFORM:
            platform, igpu_gen = GEN_PLATFORM[gen]
            # 平台标签按实际平台分流：10 代分 Comet Lake-S/-U；11 代分 Rocket Lake-S / Tiger Lake
            if gen == 10:
                platform = ("Comet Lake-S 10代" if platform_type == "desktop"
                            else "Comet Lake-U 10代" if platform_type == "laptop"
                            else "Comet Lake 10代（S 桌面 / U 低压，平台待确认）")
            elif gen == 11:
                platform = ("Rocket Lake-S 11代（UHD730/750 Gen12 无驱动）"
                            if platform_type == "desktop"
                            else "Tiger Lake 11代（Xe Gen12 无驱动）"
                            if platform_type == "laptop" else platform)
        else:
            raise ValueError(f"暂未覆盖的 CPU 代次: {gen}")
        warns.append(f"由 CPU「{cpu}」推断：平台={platform}，iGPU 代次={igpu_gen}，TDP 档={tdp}W")
    else:
        igpu_gen = igpu_gen_from_name(igpu or "")
        if igpu_gen is None:
            raise ValueError(f"无法识别核显代次: {igpu}")
        platform = "（按 iGPU 代次推断）"

    if igpu_gen == "Gen12" and not (cpu and parse_cpu(cpu)[1] == "ADLN"):
        warns.append("★ Iris Xe / UHD 7xx（Gen12）在 macOS 上无显卡驱动，不建议用该机装黑苹果。")
        if platform_type == "desktop" or (cpu and parse_cpu(cpu)[1] in ("", "K", "F", "S", "T", "X")):
            warns.append(
                "★ 11/12 代桌面（Rocket Lake-S / Alder Lake-S）没有原生 Mac 桌面机型：\n"
                "     核显必然无加速，须配 AMD 独显。选型惯例：\n"
                "     · iMac20,2 —— UHD630 仅作 headless + AMD 独显输出（Tahoe ✅）\n"
                "     · MacPro7,1 —— 纯独显方案（Tahoe ✅）\n"
                "     （不存在「原生 Rocket Lake 桌面机型」，iMac21,x 是 M1，与 Intel 黑苹果无关。）")
        return None, [], [], warns

    if igpu_gen == "Gen12":
        # Alder Lake-N / Twin Lake：WhateverXe 有实验路线（device ID 46D0–46D4，
        # 需 PCI rev 0），机型仍按双轴落 Gen11 机型族——GPU 由 WX 按 PCI ID 接管。
        warns.append(
            "★ Alder Lake-N / Twin Lake（Gen12 Xe-LP）在 macOS 无原生驱动，\n"
            "     但 WhateverXe 有实验路线（支持 device ID 46D0–46D4，需 PCI rev 0；\n"
            "     配 Lilu + 修改版 OCLP + AMFIPass Mod + boot-args -allow3d -disablegfxfirmware，\n"
            "     与 WhateverGreen 互斥）。完整前置条件与排错见 references/gen12-xe.md。\n"
            "     机型按双轴落 Gen11 机型族（Apple 产品线无 Gen12 真身），GPU 由 WX 按 PCI ID 接管；\n"
            "     优先保留一份无实验驱动可启动的 EFI。")
        gen12_route = True
        igpu_gen = "Gen11"

    cands = [m for m in MODELS if m["igpu_gen"] == igpu_gen]
    rejects = [m for m in MODELS if m["igpu_gen"] != igpu_gen]

    # 轴三：目标 macOS 26 Tahoe 过滤（Tahoe 是最后一个支持 Intel Mac 的版本，
    # 仅 16,1/16,2/16,4 受支持；其余机型最高到 Sequoia）
    if os_tahoe:
        kept = [m for m in cands if m["tahoe"]]
        dropped = [m for m in cands if not m["tahoe"]]
        if kept:
            if dropped:
                names = ", ".join(m["model"] for m in dropped)
                warns.append(
                    f"按目标 macOS 26 Tahoe 过滤（轴三）：{names} 不受 Tahoe 支持，已排除。")
            cands = kept
        else:
            warns.append(
                "⚠️ 该核显代次没有任何 Tahoe 支持机型（轴三）。\n"
                "     若目标系统是 Tahoe，需评估 OCLP 类方案（关 SIP/AMFI + 每次更新重打 root patch），\n"
                "     或改选 Gen9.5 的 45W 档 16,1/16,4（会与轴二 TDP 冲突，权衡风扇/续航）。\n"
                "     否则请降级目标系统到 Sequoia 或更低。")

    # 平台过滤：限定候选池（台式机只从 desktop 机型里选，反之亦然）
    if platform_type in ("laptop", "desktop"):
        pooled = [m for m in cands if m.get("form_factor", "laptop") == platform_type]
        if pooled:
            if len(pooled) < len(cands):
                dropped_names = ", ".join(
                    m["model"] for m in cands if m not in pooled)
                warns.append(
                    f"按平台={platform_type} 过滤（轴二在桌面含义不同）：{dropped_names} 为另一平台，已排除。")
            cands = pooled
        else:
            warns.append(
                f"⚠️ 该核显代次在 {platform_type} 平台下无内置机型（可参考 references/desktop-validation.md 手动选 MacPro7,1 等）。")

    # 台式机带独显：优先出厂带 dGPU 的机型（iMac20,2 / MacPro7,1）
    if platform_type == "desktop" and has_dgpu:
        dgpu_first = [m for m in cands if m["model"] in ("iMac20,2", "MacPro7,1")]
        if dgpu_first:
            cands = dgpu_first + [m for m in cands if m not in dgpu_first]
            warns.append("★ 台式机带独显：优先 iMac20,2（iGPU+dGPU）/ MacPro7,1（纯独显）。")

    exact = [m for m in cands if m["tdp"] == tdp]
    chosen = exact[0] if exact else None
    if chosen is None and cands and not ambiguous:
        chosen = min(cands, key=lambda m: abs(m["tdp"] - tdp))
        warns.append(
            f"⚠️ 无 {tdp}W 档的 {igpu_gen} 机型，取最接近的 "
            f"{chosen['model']}（{chosen['tdp']}W）。风扇/续航可能不最优。"
        )
    if ambiguous:
        # 平台未确认：即使 TDP 精确命中也不静默选一套（笔记本 65W 档本就不存在，
        # 命中的必是桌面机型，等于替用户做了平台判断）——保持 chosen=None 走双候选展示
        chosen = None

    # Gen12 硬件走 Gen11 机型（实验路线）：打标记，print_report 改印实验路线配套
    if chosen is not None and gen12_route:
        chosen["_gen12_route"] = True

    return chosen, cands, rejects, warns


def print_report(chosen, cands, rejects, warns):
    print("=" * 64)
    print("黑苹果 SMBIOS 机型推荐（笔记本 / 台式机通用）")
    print("=" * 64)

    for w in warns:
        print(f"\n{w}")

    if chosen:
        ff = chosen.get("form_factor", "laptop")
        board_id = chosen.get("board_id") or "OC 自动生成（按 SystemProductName）"
        print("\n★ 推荐机型")
        print("-" * 64)
        print(f"  机型     : {chosen['model']}  （{('台式机' if ff == 'desktop' else '笔记本')}）")
        print(f"  board-id : {board_id}")
        print(f"  平台     : {chosen['platform']}  ·  TDP 档 {chosen['tdp']}W  ·  iGPU {chosen['igpu_gen']}")
        print(f"  说明     : {chosen['note']}")
        print(f"  macOS 26 Tahoe 支持: {'✅ 是' if chosen['tahoe'] else '❌ 否'}")
        if chosen.get("_gen12_route"):
            print("\n  ★ Gen12 硬件走 Gen11 机型（实验路线，详见 references/gen12-xe.md）:")
            print("    机型只提供 SMBIOS 语义；核显由 WhateverXe 按 PCI ID 接管，")
            print("    **不要**注入 Gen9.5 的 device-id / ig-platform-id（那是 Gen9.5 framebuffer 的事）。")
            print("    boot-args 必加：-allow3d -disablegfxfirmware")
            print("    与 WhateverGreen 互斥；上 WX 就要关 WG。")
            print("    底线：保留一份无实验驱动可启动的 EFI。")
        elif ff == "desktop":
            print("\n  配套核显参数（台式机 UHD630 原生 0x9BC8，通常**不需要**伪装 device-id）:")
            print("    AAPL,ig-platform-id = 0x3E9B0007（07009B3E）  ★ iGPU 直出显示器首选")
            print("                           0x3E9B0000（00009B3E）    黑屏/花屏时改试的备选")
            print("                           0x3E920003 / 0x9BC80003   仅独显输出时的 headless 值")
            print("    framebuffer-fbmem      按分辨率配置（★ 高分辨率流畅度的主要杠杆）")
            print("    framebuffer-stolenmem  够用即可，不是越大越好")
            print("    300/400 系主板 iGPU 直出黑屏时优先排查 BusID/端口补丁（framebuffer-conX-alldata）。")
        else:
            print("\n  配套核显参数:")
            print("    device-id           = 0x3E9B      (Gen9.5 驱动；Comet Lake-U 0x9B41 原生无驱动必须伪装)  ★ 必须")
            print("    AAPL,ig-platform-id = 0x3E9B0000                      ★ 必须")
            print("    framebuffer-fbmem      按分辨率配置（★ 高分辨率流畅度的主要杠杆）")
            print("    framebuffer-stolenmem  够用即可，不是越大越好")

    if chosen is None and cands:
        print("\n★ 平台待确认 —— 笔记本 / 台式机两套候选（请按实际平台选用后重跑确认）")
        print("-" * 64)
        for ff, cn in (("laptop", "笔记本候选"), ("desktop", "台式机候选")):
            pool = sorted([m for m in cands if m.get("form_factor", "laptop") == ff],
                          key=lambda x: x["tdp"])
            if not pool:
                continue
            print(f"\n  【{cn}】")
            for m in pool:
                mark = "  ◀ 桌面 65W 档" if m["tdp"] == 65 else ""
                print(f"    {m['model']:<18} {m['tdp']:>3}W  {m['board_id'] or 'OC 自动生成'}  {m['note']}{mark}")
        print("\n  ⚠️ 关于 fbmem 的实测参考（同一台机出现过两个值，勿盲抄）:")
        print("     · 本机（i7-10510U / 2K 外屏）最终生效且有效的是 fbmem ≈ 48 MB")
        print("     · 社区流传的另一组是 fbmem 00009000(≈9MB) + stolenmem 00003001(≈19MB)")
        print("     两者都曾让 2K 变顺，说明关键在「帧缓冲被正确分配」而非某个固定数值。")
        print("     建议：先按当前配置实测，只有加屏/上 4K 才调大 fbmem。")
        print("\n  ⚠️ UpdateSMBIOSMode：语义随 OpenCore 版本变化，以所用版本文档为准。")
        print("     需要 Windows 双系统读到原厂表时，选「保留原厂表」的那一档；")
        print("     board-id 建议交给 OpenCore 按 SystemProductName 自动填充，避免手抄出错。")
        print("  ⚠️ 换机型后必须 Reset NVRAM")

    if cands:
        print("\n同代次候选机型（按 TDP 档排序）")
        print("-" * 64)
        for m in sorted(cands, key=lambda x: x["tdp"]):
            mark = "◀ 选中" if chosen and m["model"] == chosen["model"] else ""
            print(f"  {m['model']:<18} {m['tdp']:>3}W  {m['board_id'] or 'OC 自动生成'}  {m['note']} {mark}")

    if rejects:
        print("\n被排除（iGPU 代次不符 —— 会导致 GPU stall / 睡死）")
        print("-" * 64)
        for m in rejects:
            print(f"  {m['model']:<18} iGPU {m['igpu_gen']}")

    print("\n  ⚠️ 交付 config.plist 前，用 OpenCorePkg 自带的 ocvalidate 做完整校验：")
    print("      ocvalidate <path>/config.plist   （不通过不要交付）")

    print("\n" + "=" * 64)


def list_models():
    print("机型总表（board-id 已验证；★ 桌面机型）")
    print("=" * 78)
    for m in sorted(MODELS, key=lambda x: (x["igpu_gen"], x["tdp"])):
        th = "✅" if m["tahoe"] else "❌"
        tag = "★桌面" if m.get("form_factor") == "desktop" else "     "
        print(f"  {m['model']:<18}{tag} {m['igpu_gen']:<7} {m['tdp']:>3}W  {(m['board_id'] or 'OC 自动生成'):<22} {th} Tahoe")
        print(f"       └ {m['note']}")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(
        description="黑苹果 SMBIOS 机型推荐（笔记本/台式机通用；三轴：核显代次 + TDP 档位 + macOS 版本资格）")
    ap.add_argument("--cpu", help="CPU 型号，如 i7-10510U / i3-10105（推荐，自动推断代次/后缀/TDP）")
    ap.add_argument("--igpu", help="核显型号，如 'UHD 620'")
    ap.add_argument("--tdp", type=int, help="CPU 真实 TDP 档位（5/15/28/45/65/95）")
    ap.add_argument("--os", dest="os_name", default="",
                    help="目标 macOS 版本，如 tahoe（按轴三过滤不受支持的机型）；留空不做过滤")
    ap.add_argument("--platform", dest="platform_type", default=None,
                    choices=["laptop", "desktop"],
                    help="平台：laptop / desktop（台式机只从桌面机型里选；不指定则依次按 Report.json → CPU 后缀推断）")
    ap.add_argument("--report", help="Report.json（Hardware Sniffer 产物）：其 Motherboard.Platform 作为最高优先级平台信号")
    ap.add_argument("--dgpu", action="store_true",
                    help="台式机带独立显卡（AMD）：优先 iMac20,2 / MacPro7,1")
    ap.add_argument("--interactive", action="store_true", help="交互模式")
    ap.add_argument("--list", action="store_true", help="列出全部机型")
    a = ap.parse_args()

    os_tahoe = bool(a.os_name) and ("tahoe" in a.os_name.lower() or "26" in a.os_name)

    if a.list:
        list_models()
        return 0

    if a.interactive:
        print("黑苹果 SMBIOS 机型推荐 —— 交互模式（笔记本/台式机通用）\n")
        cpu = input("请输入 CPU 型号（如 i7-10510U，回车跳过）: ").strip()
        igpu = ""
        tdp = None
        platform_type = None
        has_dgpu = False
        if cpu:
            t = input("请输入真实 TDP 档位（5/15/28/45/65/95，回车自动按后缀推断）: ").strip()
            if t:
                tdp = int(t)
            p = input("平台（laptop 笔记本 / desktop 台式机，回车自动推断）: ").strip().lower()
            if p in ("laptop", "desktop"):
                platform_type = p
            if p == "desktop":
                d = input("是否带独立显卡 AMD（y/n，回车=n）: ").strip().lower()
                has_dgpu = d == "y"
        else:
            igpu = input("请输入核显型号（如 UHD 620）: ").strip()
            t = input("请输入真实 TDP 档位（5/15/28/45/65/95）: ").strip()
            tdp = int(t) if t else 15
            p = input("平台（laptop 笔记本 / desktop 台式机，回车=laptop）: ").strip().lower()
            if p in ("laptop", "desktop"):
                platform_type = p
        t_os = input("目标 macOS 版本（回车跳过，如 tahoe）: ").strip()
        os_t = bool(t_os) and ("tahoe" in t_os.lower() or "26" in t_os)
        try:
            ch, cd, rj, wn = recommend(cpu=cpu or None, igpu=igpu or None,
                                       tdp=tdp, os_tahoe=os_t,
                                       platform_type=platform_type, has_dgpu=has_dgpu)
        except ValueError as e:
            print(f"\n❌ {e}")
            return 1
        print()
        print_report(ch, cd, rj, wn)
        return 0

    if not a.cpu and not a.igpu:
        ap.print_help()
        print("\n❌ 请至少提供 --cpu 或 --igpu（或用 --interactive）")
        return 1

    # Report.json 的 Motherboard.Platform = 最高优先级平台信号（P0-2）
    report_platform = None
    if a.report:
        rp = Path(a.report)
        if rp.is_file():
            try:
                report_platform = (json.loads(rp.read_text(errors="ignore"))
                                   .get("Motherboard") or {}).get("Platform")
            except Exception as e:
                print(f"⚠️ 读取 Report.json 失败: {e}（回退到 CPU 后缀推断）")
        else:
            print(f"⚠️ Report.json 不存在: {a.report}（回退到 CPU 后缀推断）")

    try:
        ch, cd, rj, wn = recommend(cpu=a.cpu, igpu=a.igpu, tdp=a.tdp, os_tahoe=os_tahoe,
                                   platform_type=a.platform_type, has_dgpu=a.dgpu,
                                   report_platform=report_platform)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    print_report(ch, cd, rj, wn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
