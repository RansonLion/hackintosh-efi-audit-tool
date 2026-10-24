# 台式机黑苹果验证方法（与笔记本的差异 & 三轴在桌面的落地）

> 本 skill 原以笔记本为中心。本文件补齐**台式机（Desktop）**的验证方法：
> 平台判定、SMBIOS 选型、与笔记本审计的差异清单、以及本案例（ThinkCenter）的交叉印证。
> 三轴框架（核显代次 + TDP 档位 + macOS 版本资格）仍然适用，但**轴二在桌面的含义不同**。
> 两个审计脚本（`efi_config_audit.py` / `audit_opcore_result.py`）现已**平台感知**，
> `recommend_smbios.py` 支持 `--platform desktop`。

---

## 0. 先判定平台（最关键的开关）

工具必须第一件事判定「这是笔记本还是台式机」，否则会把台式机 SMBIOS 误判为 FAIL。

判定依据（按优先级）：
1. `Report.json` / SysReport → `Motherboard.Platform == "Desktop"`（本 ThinkCenter 即是）。
2. 机型前缀：`MacBook*`（含 MacBook / MacBookAir / MacBookPro）→ 笔记本；
   `iMac / Macmini / MacPro / iMacPro` → 台式机。
3. 硬件特征：有电池 / 触控板 / I2C → 笔记本；台式 CPU（后缀 K/F/S/T，无 U/Y/H）→ 台式机。

> ⚠️ 平台感知后的行为变化：
> - 台式机用 `iMac / Macmini / MacPro` 仿冒属**正常**，不再报「非笔记本机型」FAIL。
> - 笔记本专属检查（盒盖睡眠 / Optimus 独显 / PNLF 内屏亮度 / I2C 触控板 / SMCBatteryManager）
>   对台式机**自动降级或跳过**。
> - 台式机额外关注：CFG Lock、`AppleXcpmCfgLock`、独显路线、USB 映射、板载声卡 codec。

---

## 1. 台式机 SMBIOS 选型（三轴）

### 轴一：核显代次（与笔记本同逻辑）
- **UHD 630**（Coffee Lake-S / Comet Lake-S）= **Gen9.5**，macOS 原生驱动，**无需伪装 device-id**。
- **UHD 750**（Rocket Lake，11 代桌面）= **Gen12**——⚠️ macOS 上**没有**核显驱动，
  iGPU-only 的 Rocket Lake 不建议装黑苹果（须配 AMD 独显走 MacPro7,1）。
- 配独显（AMD Navi/Polaris）时，核显代次由独显驱动决定，SMBIOS 核显声明仅影响 iGPU 加速；
  AMD 用 WhateverGreen **原生免驱**。

### 轴二：TDP 档位（桌面含义不同）
- 笔记本的「TDP 档位」= 机型写死的功耗/温度曲线（15W/28W/45W 直接决定风扇与续航）。
- **桌面没有这个硬约束**：iMac19/20/21 都是 65W 级散热，MacPro7,1 是工作站级。
- 桌面的「轴二」实为 **iGPU-only vs iGPU+dGPU** 的取舍：
  - iGPU-only → 选 **iMac19,1 / iMac20,1 / Macmini8,1**（出厂即核显平台）。
  - iGPU + 独显 → 选 **iMac20,2**（出厂带 dGPU）。
  - 纯独显（无核显或核显不可用，如 Rocket Lake / Xeon）→ 选 **MacPro7,1**（须配 AMD 独显）。

### 轴三：macOS 版本资格（独立于轴一，桌面清单）
macOS 26 **Tahoe** 是最后一个支持 Intel Mac 的版本，受支持的**桌面**机型：
- **iMac20,1 / iMac20,2**（Comet Lake-S，2020 27"）✅ Tahoe
- **MacPro7,1**（2019 工作站）✅ Tahoe
- ❌ 不支持 Tahoe（最高 Sequoia）：iMac19,1 / iMac19,2（Coffee Lake 2019）、
  Macmini8,1（Coffee Lake 2018）、iMacPro1,1（2017）

> ★ 实战技巧：同一颗 Coffee Lake-S（如 i9-9900K），在 Sequoia 及更早用 **iMac19,1**；
> 要上 **Tahoe** 必须换成 **iMac20,1/20,2**（二者同代同核显 Gen9.5，仅年份/资格不同）。

### 台式机机型速查表
| 机型 | board-id | 平台 | 核显 | Gen | 散热 | Tahoe | 备注 |
|---|---|---|---|---|---|---|---|
| iMac19,1 | Mac-AA95B1DDAB278B95 | Coffee Lake-S | UHD 630 | Gen9.5 | 65W | ❌(Sequoia) | iGPU-only / +dGPU；Tahoe 需换 iMac20,x |
| iMac19,2 | OC 自动生成 | Coffee Lake-S | UHD 630 | Gen9.5 | 65W | ❌ | 21.5" 机型 |
| iMac20,1 | Mac-CAD8A5C1E5E6AADC | Comet Lake-S | UHD 630 | Gen9.5 | 65W | ✅ | **iGPU-only 桌面首选（Tahoe）** |
| iMac20,2 | OC 自动生成 | Comet Lake-S | UHD 630 | Gen9.5 | 65W | ✅ | iGPU + dGPU |
| Macmini8,1 | OC 自动生成 | Coffee Lake | UHD 630 | Gen9.5 | 65W | ❌ | iGPU-only 小主机 |
| MacPro7,1 | Mac-27AD2F918AEAC861 | Xeon W | 无 iGPU | — | 工作站 | ✅ | 须配 AMD 独显（Navi/Polaris） |

> board-id 仍可交给 OpenCore 按 `SystemProductName` 自动生成，不必手抄（手写错配更难查）。
> 上表仅列高频机型；Apple 2019–2020 的 Intel 台式机机型均同此逻辑。
>
> ★ **不存在 iMac21,x 这个 Intel 机型**：iMac21,1 / iMac21,2 是 2021 款 24" **M1**
> （A2438 / A2439，Apple Silicon），Apple 从未发布 Rocket Lake 版 iMac，最后一个
> Intel iMac 是 iMac20,x（2020 Comet Lake-S）。
> **Rocket Lake-S（11 代桌面 i5-11400 / i7-11700 等，UHD 730/750 = Gen12 无驱动）**的
> 仿冒惯例：**iMac20,1**（核显无加速 + AMD 独显输出）或 **MacPro7,1**（纯独显），
> 没有"原生 Rocket Lake 桌面机型"可仿。

---

## 2. 台式机专属审计清单（相对笔记本的差异）

### 对台式机应**跳过 / 降级**的笔记本检查
| 笔记本检查 | 台式机处理 |
|---|---|
| 盒盖睡眠（DisableRtcChecksum 文案） | 改判为「软睡眠/唤醒的 RTC 校验和」；仍可开 `DisableRtcChecksum` 防 Sleep Wake failure，但不再用「盒盖」措辞 |
| Optimus 独显处理 | 台式机独显是 PCIe 独立卡，不涉及 Optimus _muxless；只判「AMD 免驱 / NVIDIA 无驱动」 |
| PNLF 内屏亮度 | **不需要**（外接显示器走 DP/HDMI，亮度由显示器物理按键控制）；降级为 INFO，不报 FAIL |
| I2C 触控板 | 不存在；跳过 |
| SMCBatteryManager.kext | 不需要；台式机用 SMCProcessor / SMCSuperIO 即可，缺 SMCBatteryManager **不**报 FAIL |

### 台式机应**额外**关注的要点
1. **CFG Lock**（AppleXcpmCfgLock）：桌面（尤其 H470/B460 等）普遍锁着。应进 BIOS 解锁，
   并把 `AppleXcpmCfgLock` 改 `false`；仅靠 quirk 是「代价不是解法」。`AppleCpuPmCfgLock` 同理（老平台）。
2. **独显**：AMD 独显（RX 4xx/5xx/Vega/VII/5xxx/6xxx）→ WhateverGreen 原生免驱；
   NVIDIA（除 Kepler 老卡）在 Ventura+ 无驱动。**MacPro7,1 路线必须有 AMD 独显**。
3. **USB 映射**：台式机主板端口多，必须用 USBToolBox 做一次本机映射（`UTBMap.kext`），
   否则端口数超限 → 睡眠唤醒异常、部分口失效。`XhciPortLimit` 在 Catalina 后已失效，别依赖。
4. **声卡 codec**：台式机常见 ALC887/892/897/1200，用 AppleALC + `alcid=`，
   试 1/2/3/5/7/11/12/21/66 等，主板不同差异大。
5. **网卡**：板载 Realtek 千兆 → RealtekRTL8111；Intel 板载 → IntelMausi；
   要 WiFi，要么换 Broadcom PCIe 卡（FV-T919 / BCM94360CD，Tahoe 原生免驱），
   要么 Intel 卡走 AirportItlwm + OCLP root patch（每次更新重打补丁）。
6. **软睡眠/唤醒**：台式机没有盒盖，但要测「菜单睡眠→键鼠唤醒」「定时唤醒」，
   以及 NVMe/SATA 的 `Power Nap` / `hibernatemode`。`PowerTimeoutKernelPanic=true` 仍建议开。

---

## 3. 本案例交叉印证（ThinkCenter H470 / i3-10105 / UHD 630 / iMac20,1 / Tahoe）

对 `EFI_审计报告.html` 的逐条复核（已比对真实 `config.plist`）：

- ✅ **SMBIOS = iMac20,1**：桌面 Comet Lake-S + UHD630 + Tahoe，选型正确。
  旧版脚本会误报「非笔记本机型」FAIL——本报告靠人工纠正；升级后的脚本已平台感知，不再误报。
- ✅ **`-igfxvesa` 在 boot-args（P0）**：属实，`config.plist` NVRAM boot-args 确为
  `-v debug=0x100 keepsyms=1 igfxonln=1 -igfxvesa`，强制 VESA 无加速，必须删。
- ✅ **缺 AppleALC（P0）**：属实，`Kernel→Add` 无 AppleALC.kext，板载 ALC897 将无声。
- ✅ **缺 Intel WiFi/BT 栈（P0）**：属实，AC 8260 无 AirportItlwm / IntelBluetoothFirmware /
  IntelBTPatcher（仅 BlueToolFixup 不足以驱动 Intel BT）。
- ✅ **AppleXcpmCfgLock=true（P1）**：属实（Kernel→Quirks），H470 典型 CFG Lock 未解。
- ✅ **AllowNvramReset 缺失（P1）**：属实，`Misc→Security` 无该键（默认 false），picker 无重置入口。
- ✅ **ig-platform-id = 0x3E9B0000 + 不伪装 device-id**：桌面 UHD630 原生，正确
  （笔记本的「缺 device-id」提示在桌面不适用）。
- ✅ **SSDT 路径命中 DSDT**：AWAC / SBUS / XHC_ 已在 SysReport 确认存在；
  EC 在 H470 常规位于 `LPCB.EC`（DSDT 反编译确认更稳妥，本机未独立反编译）。
- ⚠️ **工具盲区（本报告靠人工补齐）**：若直接用升级前脚本跑此 EFI，会误报
  「非笔记本机型 FAIL」与「缺 SMCBatteryManager FAIL」；升级后的脚本已平台感知，两项不再误报。

---

## 4. 工具用法

- 机型推荐（台式机，目标 Tahoe）：
  ```bash
  python3 recommend_smbios.py --cpu "i3-10105" --platform desktop --os tahoe
  ```
  → 推 **iMac20,1**（iGPU-only 桌面首选）；若带独显加 `--dgpu` 走 iMac20,2 / MacPro7,1。
- 审计 EFI（台式机，自动判定平台）：
  ```bash
  python3 efi_config_audit.py /path/to/config.plist
  # 或
  python3 audit_opcore_result.py /path/to/opcore-output
  ```
  → 自动判定平台，台式机不再误报「非笔记本机型」「缺 SMCBatteryManager」。
  （`efi_laptop_audit.py` 为 v1.0.1 前的旧名，保留兼容入口，行为与 `efi_config_audit.py` 一致。）

---

## 5. 与笔记本审计的总差异一览（脚本层面）

| 检查项 | 笔记本 | 台式机（升级后） |
|---|---|---|
| SMBIOS 族 | MacBook* OK | iMac/Macmini/MacPro OK |
| 盒盖睡眠 / RTC | 重点（DisableRtcChecksum） | 改判「软睡眠 RTC」，措辞去「盒盖」 |
| Optimus / 独显 mux | 检查 | 改判「AMD 免驱 / NVIDIA 无驱动」 |
| PNLF 内屏亮度 | 必需 | 跳过（外接显示器） |
| I2C 触控板 | 检查 | 跳过 |
| SMCBatteryManager | 必需 | 不需要（不报 FAIL） |
| CFG Lock / AppleXcpmCfgLock | 偶尔 | 重点（桌面普遍锁） |
| USB 映射 | 建议 | 重点（端口多，必做本机映射） |
