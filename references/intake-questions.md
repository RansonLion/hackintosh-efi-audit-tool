# 硬件采集：分批提问模板与采集命令

> 用法：**一次只问一批**（≤4 问），用户答完再进下一批。
> 每个问题都要附上「怎么拿到答案」——不要让用户自己去猜该用什么命令。
> 用户答不上来的项，标 **`待实测`** 并给出最低成本实测法，**不要替他填**。

---

## 批 0：环境判定（决定后续用哪套采集命令）

| 问 | 为什么重要 |
|---|---|
| 你现在是 **Windows / macOS / Linux**，还是尚未安装？ | 决定采集命令与审计方式 |
| 目标 macOS 版本？ | 决定 kext 兼容性与机型白名单 |
| 是否 **Windows 双系统**（需要 Windows 读到原厂 SMBIOS 表）？ | 决定 SMBIOS 更新模式的选择 |
| 是否已挂载 / 能否读到 EFI 分区？ | 决定能否直接审计 config.plist |

---

## 批 1：平台身份

1. 笔记本**完整型号**（如 Dell Inspiron 5498）
2. **主板 / 芯片组**（如 Comet Lake-U PCH-LP）
3. **BIOS 版本**
4. 当前系统版本

**采集方法**

```powershell
# Windows（PowerShell）
Get-CimInstance Win32_ComputerSystemProduct   # 型号
Get-CimInstance Win32_BaseBoard               # 主板
Get-CimInstance Win32_BIOS                    # BIOS 版本
```

```bash
# macOS
system_profiler SPHardwareDataType | grep -E 'Model|Chip|Memory|Serial'
sysctl -n hw.model                            # 当前仿冒机型
```

---

## 批 2：CPU 与 GPU（★ 双轴选型的全部输入）

1. **CPU 完整型号**（必须看到后缀）
2. **真实 TDP**（不看标称看实测档位）
3. **核显型号** → iGPU 代次
4. **独显型号 + 是否 Optimus**（是否直连内屏）
5. **独显架构与意图**：NVIDIA **Turing+**（GTX 16 / RTX 20–50）还是 **Pascal 及更早**（MX150/MX250/GTX 10 系）？
   机器是**有 mux / 独显直显**还是纯 Optimus？是否**愿意接受实验性驱动**（目标 macOS 是否为 15 Sequoia）？
   → 决定走 `references/nvidia-gpu.md` 路线还是继续禁用
6. **11/12 代 Xe 核显持有者**：是否知道 macOS 原生不支持、打算走 WhateverXe / NootedGreen 实验路线？
   → 见 `references/gen12-xe.md`（先告知实验属性与 WG 互斥）

**采集方法**

```powershell
# Windows
Get-CimInstance Win32_Processor | Select Name, MaxClockSpeed
# 设备管理器 → 显示适配器 → 详细信息 → 硬件 ID
#   PCI\VEN_8086&DEV_9B41   → 8086=Intel，9B41=Comet Lake-U 核显（Gen9.5）
#   PCI\VEN_10DE&DEV_1D52   → 10DE=NVIDIA
Get-CimInstance Win32_VideoController | Select Name, PNPDeviceID
```

```bash
# macOS
sysctl -n machdep.cpu.brand_string
system_profiler SPDisplaysDataType | grep -E 'Chipset Model|Device ID|Vendor|Metal'
system_profiler SPDisplaysDataType | grep -iE 'Metal Support|VRAM'   # ★ 确认加速是否真的开了
```

**判读规则**

| 看到 | 结论 |
|---|---|
| 后缀 `U` | 低压 15W 档（注意部分机型 cTDP-up 到 25W，以实测为准） |
| 后缀 `H` / `HK` | 标压 45W 档 |
| 后缀 `Y` | 超低压 5–7W 档 |
| 后缀 `G1/G4/G7` | **Ice Lake，Gen11**（与 U 系 Gen9.5 完全不同！） |
| 核显 UHD 620 | Gen9.5 |
| 核显 Iris Plus（Ice Lake） | Gen11 |
| 核显 Iris Xe | Gen12 → 原生无驱动；实验路线见 `references/gen12-xe.md`（WhateverXe / NootedGreen），**实验 ≠ 可用** |
| 独显为 MX / GTX 移动版 | 绝大多数**未直连内屏** → 禁用（见下）；例外：有 mux/直显 + Turing+ + 愿上实验驱动（`references/nvidia-gpu.md`） |

> **Optimus 判定**：若笔记本只有一个显示输出路径、独显只在 Windows 下被驱动调用，
> 即无 mux 的 Optimus。此类独显**在 macOS 下无法为内屏加速**，默认结论是**禁用**，
> 否则会引发睡眠/唤醒失败与空耗电。

---

## 批 3：存储与内存

1. **NVMe 型号**（排查 macOS 不兼容型号）
2. 是否有 SATA 盘
3. 内存容量与通道数

```powershell
# Windows：设备管理器 → 存储控制器 → 硬件 ID；或用
Get-PhysicalDisk | Select FriendlyName, MediaType, BusType
Get-CimInstance Win32_PhysicalMemory | Select Capacity, Speed
```

```bash
# macOS
system_profiler SPSerialATADataType SPNVMeDataType | grep -E 'Model|Capacity'
system_profiler SPMemoryDataType | grep -E 'Size|Speed|Status'
```

**红旗**：三星 PM981 / PM981a / PM991 在 macOS 下有已知兼容问题（部分会掉盘或休眠失败）；
若命中，建议换盘或加 `NVMeFix.kext` 并实测。**判定以实测为准，不要凭型号一刀切。**

---

## 批 4：输入与输出（★ 工具看不到，必须人工确认）

1. **触控板总线：I2C 还是 PS2？**
2. **键盘**：PS2 还是 USB 内置
3. **摄像头**：USB 还是内建（CSI）
4. **屏幕**：接口 **eDP / LVDS**、分辨率、是否有 mux（独显直连）

```powershell
# Windows：设备管理器 → 人体学输入设备 / 系统设备 → 硬件 ID
#   ACPI\VEN_...&DEV_... 或 I2C\VID_xxx  → I2C 触控板
#   ACPI\PNP0303 / *PNP0303              → PS2 键盘
Get-PnpDevice -Class 'Mouse','Keyboard','Camera','System' | Select FriendlyName, InstanceId
```

```bash
# macOS
ioreg -l | grep -i -E 'VoodooI2C|VoodooPS2|ApplePS2'   # 看当前用的是哪套驱动
system_profiler SPCameraDataType
```

**判读**

| 事实 | 后果 |
|---|---|
| 触控板 I2C | 需 `VoodooI2C` + GPIO 引脚配置 + `SSDT-GPI0`（部分需 `XOSI`） |
| 触控板 PS2 | `VoodooPS2Controller` 即可 |
| 面板 eDP | 需 `SSDT-PNLF`（亮度）；LVDS 则另论 |
| 有 mux | 极少数游戏本可切独显输出，一般仍需禁用独显求稳 |

---

## 批 5：网络与音频

1. **无线网卡芯片**（决定走哪条驱动路线）
2. **声卡 codec 型号**
3. **3.5mm 孔类型**（combo 还是分离）

```powershell
# Windows：设备管理器 → 网络适配器 / 声音视频和游戏控制器 → 硬件 ID
#   PCI\VEN_8086  → Intel（itlwm / AirportItlwm，需确认具体型号）
#   PCI\VEN_14E4  → Broadcom（多数免驱或需 OCLP）
#   PCI\VEN_10EC&DEV_0236 → Realtek ALC236 声卡
Get-PnpDevice -Class 'Net' | Select FriendlyName, InstanceId
```

```bash
# macOS
system_profiler SPNetworkDataType | grep -E 'Vendor|Type'
system_profiler SPAudioDataType | grep -E 'Device|Manufacturer|Output|Input'
ioreg -rxn HDEF | grep -i layout          # ★ 看系统实际用了哪个 layout
```

**判读**

| 网卡 | 路线 |
|---|---|
| Intel | `itlwm`（需配合 Wi-Fi profile 实现开机自连）或 `AirportItlwm`（按 macOS 版本选） |
| Broadcom 免驱型号 | 通常直接可用 |
| Broadcom 非免驱 / 被新系统剔除 | 需 OCLP root patch |
| Realtek / MediaTek | 多数无解或仅蓝牙可用，**建议换卡** |

---

## ★ 批 BIOS：必须人工进 BIOS 目视的 7 项

> **任何工具都读不到 BIOS 设置。** 这是整条流程里唯一必须打断自动化的一环，
> 必须由用户进 BIOS 逐项看，并**照原样回报**（不要猜）。

| BIOS 项 | 应设置 | 拿不到时的后果 |
|---|---|---|
| **CFG Lock / MSR 0xE2** | **Disabled（解锁）** | 未解锁 → 只能用 `AppleCpuPmCfgLock`/`AppleXcpmCfgLock` 兜底，原生电源管理降级 |
| **DVMT Pre-Allocated** | 尽量 64MB 或以上 | 锁死且偏小 → 用 `framebuffer-fbmem`/`stolenmem` 绕过 |
| **Secure Boot** | Disabled | 无法引导 OpenCore |
| **Legacy / CSM** | Disabled（纯 UEFI） | 引导异常 |
| **Above 4G Decoding** | 按平台（有独显时常需 Enable） | 设备枚举失败 |
| **VT-d** | 通常 Disable（或配合 `DisableIoMapper`） | 启动卡死 |
| **Fast Boot** | Disabled | BIOS 设置不生效、USB 识别异常 |

**回报模板**（让用户照抄即可）：

```
CFG Lock      : ?
DVMT Pre-Alloc: ?
Secure Boot   : ?
CSM / Legacy  : ?
Above 4G      : ?
VT-d          : ?
Fast Boot     : ?
```

### OEM 特有项（通用清单之外，按品牌追加询问）

> 通用 7 项是底线，**品牌专属项常常才是真凶**。已知的两家：

**Lenovo / ThinkPad**

| 位置 | 建议值 | 不关会怎样 |
|---|---|---|
| Internal Device Access → **Bottom Cover Tamper Detection** | **必须 Disabled** | ★ 底盖防拆检测触发会干扰睡眠与电池状态 |
| Security → Security Chip | Disabled | TPM 相关干扰 |
| Anti-Theft → Computrace | Disabled | 后台驻留 |
| Memory Protection → Execution Prevention | Enabled | — |
| Virtualization → Intel Virtualization Technology | Enabled | — |

**Dell**

| 位置 | 建议值 |
|---|---|
| `DisableRtcChecksum`（OC 侧） | **true**（Dell 的 RTC 校验和问题是盒盖睡死高发根因） |
| `CustomSMBIOSGuid`（OC 侧） | Dell 固件常需要 true |

> 遇到清单外的品牌：**先搜「品牌 + 型号 + hackintosh + BIOS settings」**，
> 把搜到的特有项并入本批再让用户确认，不要只问通用 7 项。

---

## 采集结果汇总模板

把答案整理成这张表，后续所有决策都以它为依据：

```
机型        : 
CPU         : （后缀 / 真实 TDP）
核显        : （型号 / Gen / device-id）
独显        : （型号 / 是否 Optimus / 是否禁用）
内存 / 存储 : 
触控板      : I2C / PS2
键盘        : PS2 / USB
摄像头      : USB / 内建
屏幕        : eDP / LVDS，分辨率
网卡        : 
声卡        : （codec 型号）
BIOS        : CFG Lock / DVMT / Secure Boot / CSM / Above4G / VT-d / Fast Boot
当前仿冒机型: 
目标 macOS  : 
```
