# SMBIOS 机型选型：三轴决策参考（核显代次 + TDP 档位 + macOS 版本资格）

> 本文件是技能的核心数据表。使用前请先读 §0 的勘误说明——
> 社区里流传最广的机型属性错误，正是本技能要纠正的对象。
>
> **三轴总览**：① 轴一 = 核显代次（稳定性底线）；② 轴二 = TDP 档位（风扇/续航）；
> ③ 轴三 = macOS 版本资格（机型决定系统能升到哪）。轴三独立于轴一，详见下方专节。

## §0.0 官方依据（本表的可验证来源）

| 结论 | 官方依据 |
|---|---|
| 机型属性（CPU / 核显 / 年代 / 雷雳口） | Apple 官方「识别 Mac 机型」规格页（`support.apple.com` → **HT201300**，按机型号查技术规格），与 everymac.com 交叉核对 |
| iGPU 代次与 macOS 驱动支持 | WhateverGreen 官方 FAQ 的 Intel 核显章节（`github.com/acidanthera/WhateverGreen/wiki/FAQ`） |
| 机型选择的电源语义 | Dortania OpenCore Install Guide → `PlatformInfo` 章节（`dortania.github.io/OpenCore-Install-Guide/config.html`） |
| opcore-simplify 为何推错 | 其源码映射逻辑与 README 自述（`github.com/lzhoang2801/OpCore-Simplify`） |

---

## §0 必读：三个流传最广的错误认知

### 错误 1：把 16,2 与 16,3 的身份弄反

| 机型 | 真实身份 | iGPU | Tahoe |
|---|---|---|---|
| **MacBookPro16,2** | 2020 13" **四**雷雳3 | **Ice Lake 28W，Gen11**（Iris Plus G7） | ✅ |
| **MacBookPro16,3** | 2020 13" **双**雷雳3 | **Coffee Lake-U 15W，Gen9.5**（Iris Plus 645） | ❌ |

两者极易混淆（都是 13"，都在 16,x 族），但**核显差两代、Tahoe 资格也不同**。
把 Gen11 的机器排除掉 16,2 会少一个可用选项；
反过来把 Gen9.5 的机器仿冒成 16,2（Gen11），则直接踩中「GPU hang → 睡死」的雷。

> ⚠️ **勘误说明**：本节曾把两者写反（误称 16,2=CFL-U/16,3=Ice Lake）。
> 2026-10-23 依 Apple 官方「识别 MacBook Pro 机型」规格页复核修正，特此标注。

### 错误 2：按 CPU 世代挑机型

**iGPU 代次与 CPU 世代从第 10 代起就不再一一对应。**
10 代同时存在两条产品线：

| | CPU 微架构 | 核显 | device-id |
|---|---|---|---|
| **Comet Lake-U** | Comet Lake（10 代） | **Gen9.5（UHD 620）** | 0x9B41（macOS 无驱动） |
| **Ice Lake-U** | Ice Lake（10 代 G） | **Gen11（Iris Plus）** | 0x8A51 等 |

任何「按 CPU 世代映射机型」的算法（**包括 opcore-simplify**）
都会把 10 代 U 系推到 Ice Lake 机型上，而这在轴一上是错的。

### 错误 3：以为 Apple 有对应每一代 Intel 的机型

> **★ 10 代 Comet Lake-U 笔记本在 Apple 产品线中没有真身。**
>
> Apple 的 10 代**笔记本**机型（`MacBookPro16,2` / `MacBookAir9,1`）**全是 Ice Lake + Gen11**；
> `MacBookPro16,3` 是 **8 代 Coffee Lake-U（15W / Gen9.5）**；Comet Lake 只出现在
> 台式机（iMac20,x）与 `MacBookPro16,1` / `16,4`（Coffee Lake-**H**，45W）。
>
> 所以 10 代 **U** 系（i5-10210U / i7-10510U 等）**没有任何一台真 Mac 与之对应**，
> 必须退回 **Coffee Lake-U** 的机型族：**15W → `MacBookPro15,4` 或 `MacBookPro16,3`；
> 28W → `MacBookPro15,2`**。

这是本技能最重要的单条知识：它解释了为什么自动化工具会推错，也解释了该往哪退。

---

## 一、轴一：核显（iGPU）代次 → 候选机型族

**为什么是硬要求**：macOS 上层框架（CoreML / Vision / MPS / mediaanalysisd）
按 SMBIOS 声明的 GPU 代次挑选 compute kernel。声明代次新于物理核显时，
kernel 下放到旧核显会在 batch 边界静默 stall → `gpuRestart` → 睡死 / 卡死。
**这不是性能问题，是稳定性底线。**

| 物理核显 | iGPU Gen | CPU 平台 | 候选 MacBook 机型族 |
|---|---|---|---|
| HD 510/520/530 | Gen9 | Skylake（6 代） | MacBookPro13,x / MacBook9,1 |
| HD 620/630 | Gen9.5 | Kaby Lake（7 代） | MacBookPro14,x / MacBook10,1 |
| UHD 620 | Gen9.5 | Coffee Lake-U（8 代） | MacBookPro15,x / **16,3** |
| UHD 620 | Gen9.5 | Whiskey Lake-U（8.5 代） | MacBookPro15,x / **16,3** |
| UHD 617 | Gen9.5 | Amber Lake-Y（8 代 Y） | MacBookAir8,1 / MacBookAir8,2 |
| UHD 630 | Gen9.5 | Coffee Lake-H（8/9 代 H） | MacBookPro15,1 / 15,3 / 16,1 / 16,4 |
| **UHD 620** | **Gen9.5** | **Comet Lake-U（10 代 U）** | **MacBookPro15,4 / 16,3**（★ 见 §0 错误 3） |
| Iris Plus | Gen11 | Ice Lake（10 代 G） | **MacBookPro16,2 / MacBookAir9,1**（❌ 不是 16,3） |
| Iris Xe | Gen12 | Tiger Lake（11 代）/ Alder Lake-N（12 代 N） | ⚠️ 原生无驱动；实验路线见 `references/gen12-xe.md`（WhateverXe / NootedGreen），机型仍落 Gen11 族 |

> ⚠️ **Comet Lake（10 代 U）特别提示**：原生 device-id 为 `0x9B41`，
> macOS **没有**对应驱动，必须用 DeviceProperties 伪装为 `0x3E9B`（Coffee Lake 系），
> 并配 `AAPL,ig-platform-id = 0x3E9B0000`。

---

## 二、轴二：TDP 功耗档位 → 唯一机型

**为什么重要**：机型的功耗/温度曲线**写死在平台里**，无法干净覆盖（见 §0 错误 3 的推论）。

| 机型档位 | 代表机型 | 平台 | 错配后果 |
|---|---|---|---|
| 5–7W Y 系 | MacBookAir8,1 | Amber Lake-Y（Gen9.5） | 压 15W U 系 → **过度降频** |
| ~10W | MacBookAir9,1 | Ice Lake（Gen11） | 仅 Gen11 可用 |
| **15W U 系** | **MacBookPro15,4** | Coffee Lake-U（Gen9.5） | ★ **低压 U 系首选** |
| 28W | MacBookPro15,2 | Coffee Lake-U（**Gen9.5**） | Gen9.5 28W 机用 |
| 28W | MacBookPro16,2 | Ice Lake（**Gen11**） | Gen11 28W 机用 |
| 45W H 系 | MacBookPro16,1 / 16,4 | Coffee Lake-H（Gen9.5） | 压 15W U 系 → **持续负载风扇狂转** |

**CPU 后缀 → TDP 档位速查**：

| 后缀 | 含义 | 典型 TDP | 应选档位 |
|---|---|---|---|
| `Y` | 超低压 | 5–7W | MacBookAir8,1 |
| `U` | 低压 | 15W（部分机型 cTDP 可到 25W，以实测为准） | **MacBookPro15,4** |
| `H` / `HK` | 标压 | 45W | MacBookPro16,1 / 16,4 |
| `G1`/`G4`/`G7`/`NG7` | Ice Lake 核显加强（**Gen11**） | 12–28W | MacBookAir9,1 / MacBookPro16,2 |

> ⚠️ `U` 后缀里存在 25W cTDP-up 的机型（如部分 Comet Lake-U 被厂商放开）。
> **以后缀为初判、以实测为准**：若持续负载下频繁撞功耗墙且风扇不吵，可考虑 28W 档；
> 反之若风扇狂转，说明档位给高了。

---

## 三、机型 ↔ board-id 速查

> **⚠️ 强烈建议：让 OpenCore 自动填 board-id。**
> 把 `PlatformInfo → UpdateSMBIOSMode` 设为 OC 会自动按 `SystemProductName`
> 生成整套 SMBIOS（含 board-id）的模式，可**彻底避免手抄 board-id 出错**。
> 下表仅供核对，不作为抄写来源——board-id 与机型不匹配会引发莫名其妙的问题。

| 机型 | 平台 / 档位 | iGPU Gen | macOS 26 Tahoe |
|---|---|---|---|
| MacBookPro15,4 | Coffee Lake-U 15W | Gen9.5 | ❌ |
| MacBookPro16,3 | Coffee Lake-U 15W | **Gen9.5** | ❌ |
| MacBookPro15,2 | Coffee Lake-U 28W | **Gen9.5** | ❌ |
| MacBookPro16,2 | Ice Lake 28W | **Gen11** | ✅ |
| MacBookAir8,1 | Amber Lake-Y 5W | Gen9.5 | ❌ |
| MacBookAir9,1 | Ice Lake ~10W | Gen11 | ❌ |
| MacBookPro16,1 / 16,4 | Coffee Lake-H 45W | Gen9.5 | ✅ |

> ⚠️ Apple Silicon 机型（MacBookPro17,1 及 Mac14,x 等）**绝不可**用于 Intel 笔记本仿冒。
> ⚠️ 台式机机型（iMac / MacPro / Macmini）**对笔记本仿冒不可**——机型语义决定背光、电池、电源管理、雷电行为。
> 台式机本身有合法的 iMac / Mac mini / Mac Pro 机型可选（见下方「八、台式机平台」一节），二者并不矛盾。

---

## 三之二、轴三：macOS 版本资格（Tahoe 收紧后的新变量）

机型同时决定 **系统能升到哪个版本**——这是选型第三轴，**独立于轴一（核显代次）**。
**macOS 26 Tahoe 是最后一个支持 Intel Mac 的版本**（Apple 于 WWDC25 宣布，27 起仅支持 Apple Silicon），
且 Tahoe 仅支持极少数晚期 Intel 机型。本技能覆盖的笔记本 SMBIOS 资格如下
（board-id 已按 Apple 官方规格页核对，区别于社区流传的错值）：

| 机型 | iGPU | board-id | Tahoe（macOS 26） | 最高可用 |
|---|---|---|---|---|
| MacBookPro16,1 / 16,4 | Gen9.5（CFL-H 45W） | `Mac-E1008331FDC96864` / `Mac-A61BADE1FDAD7B05` | ✅ | Tahoe |
| MacBookPro16,2 | Gen11（Ice Lake 28W） | `Mac-E7203C0F68AA0004` | ✅ | Tahoe |
| MacBookPro15,4 | Gen9.5（CFL-U 15W） | `Mac-53FDB3D8DB8CA971` | ❌ | 最高 Sequoia |
| MacBookPro15,2 | Gen9.5（CFL-U 28W） | `Mac-827FB448E656EC26` | ❌ | 最高 Sequoia |
| MacBookPro16,3 | Gen9.5（CFL-U 15W） | `Mac-5F9802EFE386AA28` | ❌ | 最高 Sequoia |
| MacBookAir8,1 | Gen9.5（Amber Lake-Y 5W） | `Mac-7BA5B2DFE22DDD8C` | ❌ | 最高 Sequoia |
| MacBookAir9,1 | Gen11（Ice Lake ~10W） | `Mac-0CFF9C7C2B63DF8D` | ❌ | 最高 Sequoia |

> ★ **关键认知：轴三 与 轴一 不挂钩。** `MacBookAir9,1` 是 Gen11 却**不在** Tahoe 列表；
> 而 `MacBookPro16,2` 同为 Gen11 却**在**。能不能上 Tahoe 看的是 **Apple 官方支持列表（机型本身）**，
> 不是核显代次。所以选型时**必须同时问目标 macOS 版本**。

### 选型流程第 3 步：按目标 macOS 版本过滤（轴三）

- **目标 = Tahoe（26）**：候选只能落在 `16,1` / `16,2` / `16,4`。
  - Gen11 28W 机器（Ice Lake，如 i7-1068NG7）→ 直接选 **16,2**，原生 Tahoe；
  - Gen9.5 笔记本（如 Comet Lake-U i7-10510U）若坚持 Tahoe，只剩 **16,1 / 16,4（45W）**
    → 与轴二冲突（风扇/续航），需权衡；或走 **OCLP 类方案**（关 SIP/AMFI + 每次系统更新后重打
    root patch，复杂度另计，且非本技能主线推荐）。
- **目标 ≤ Sequoia（15）**：轴三不构成约束，`15,4` / `16,3` / `15,2` 等均可用，按轴一/轴二定。

> 提醒：`recommend_smbios.py` 支持 `--os tahoe` 自动按轴三过滤；审计脚本会在双轴复核后
> 单独给出当前机型的 Tahoe 资格提示，便于你对照目标系统版本。

---

## 四、完整推演案例：Dell Inspiron 5498 / i7-10510U

> 教学样本。完整时间线（16,3 → 16,2 → 16,4 → CPUFriend 翻车 → 15,4）见
> `references/case-dell-5498.md`。

### 硬件事实
- CPU：`i7-10510U` → **Comet Lake-U（10 代）**，后缀 **U** → 真实 TDP **15W**
- 核显：**UHD 620** → **Gen9.5**
- 独显：**MX250（Pascal）** → Optimus，无 mux → 禁用

### 轴一（核显代次）
物理 Gen9.5 → 候选族：`15,x`、`16,3`、`16,1/16,4`、`Air8,x`
❌ **排除** `MacBookPro16,2`（Ice Lake / Gen11）、`MacBookAir9,1`（Gen11）

### 轴二（TDP 档位）
真实 15W → 在候选族中挑 15W 档：
- ❌ `MacBookPro16,4`（45W H 系）：代次对但档位过高 → **实测 WPS 图片裁切等持续负载风扇狂转**
- ❌ `MacBookAir8,1`（5W Y 系）：代次对但档位过低 → 过度降频
- ❌ `MacBookPro15,2`（28W）：档位偏高
- ✅ **`MacBookPro15,4`（15W U 系）**：代次对 + 档位对

### 结论
```
推荐机型：MacBookPro15,4
理由     ：① UHD 620 = Gen9.5，与 15,4 的 Iris Plus 同代 → 消除 GPU hang
           ② i7-10510U 真实 15W，与 15,4 原生 15W 档一致 → 风扇安静、续航正常
           ③ 零自定义 kext，最稳
```

### 落地要点
- `device-id = 0x3E9B`（伪装加载 Coffee Lake 系驱动）— **必须保持**
- `AAPL,ig-platform-id = 0x3E9B0000` — **必须保持**
- `framebuffer-fbmem` + `framebuffer-stolenmem` — 见 `references/power-management.md` §DVMT
- 换机型后**必须 Reset NVRAM**

---

## 四之二、早期平台（4–5 代）与 macOS 版本上限

技能主线覆盖 6–10 代，但**三轴原则（含轴三 macOS 版本资格）对更早的平台同样适用**，只需补两张表：

| CPU 世代 | 核显代次 | 可用机型族 |
|---|---|---|
| 4 代 Haswell | Gen7.5 | MacBookAir6,x / MacBookPro11,x |
| 5 代 Broadwell | Gen8 | MacBook8,1 / MacBookAir7,x / MacBookPro12,1 |
| 6 代 Skylake | Gen9 | MacBook9,1 / MacBookPro13,x |
| 7 代 Kaby Lake | Gen9.5 | MacBook10,1 / MacBookPro14,x |

> ⚠️ **4–5 代与 6 代+的重要差异**：早期核显一律叫 "HD Graphics NNN"，
> 而 **HD 5500=Gen8、HD 520=Gen9、HD 620=Gen9.5**，
> 不能因为都叫 "HD Graphics" 就当成同一代。判定必须落到**型号数字**上。

**macOS 版本上限**（选系统版本时必查，别装了不支持的版本再靠 OCLP 补）：

| CPU 世代 | 最后原生支持的 macOS | 备注 |
|---|---|---|
| 5 代 Broadwell | **Monterey** | 真机为 2015 款；上 Ventura+ 需 OCLP 补核显，且安装时要临时换 2019 机型 SMBIOS |
| 6 代 Skylake | Monterey（部分到 Ventura） | 需按具体机型核对 |
| 7–9 代 | Ventura / Sonoma | 按机型核对 |
| 10 代 | Sonoma / Sequoia | 按机型核对 |

> 判定原则：**先按机型查 Apple 官方支持列表**，超出部分才考虑 OCLP，
> 并明确告知「需要 OCLP = 关 SIP/AMFI + 每次系统更新后要重打 root patch」的代价。

### ★ 一条新判据：用注入的 device-id 反查「它到底是哪台真机的卡」

如果 EFI 里注入了伪装 device-id，可以反查它属于哪台真 Mac；
**若反查结果与声明的机型不一致，说明机型选错了**（或至少不自洽）。

> 实例（X250）：注入 `device-id = 0x1626` → 0x1626 是 **HD 6000 = MacBookAir7,2** 的卡；
> 但 SMBIOS 声明的是 **MacBookPro12,1**（其原生卡是 Iris 6100 = `0x162B`）。
> ⇒ 核显伪装成了 Air7,2，机型却声明 Pro12,1 → 轴二（15W vs 28W）因此不匹配。
> 这成为「改 MacBookAir7,2」的**独立证据**。

伪装本身可能是对的（Broadwell 上把 HD 5500 伪装成 0x1626 是标准做法），
**不自洽的是机型声明**——不要因此去改 device-id。

### ★ 纪律：所有「已验证值」必须标注适用平台

本技能里所有带 ★ 的数值（如 `0x3E9B`、`0x3E9B0000`、`fbmem 48MB`）**都只对特定平台成立**。
用到别的机器上时，若直接拿它当普适标准，会输出**假错配告警**。
凡引用验证值，必须同时说明「这是 X 平台的值，Y 平台不适用」。

---

## 五、常见选型陷阱

| 陷阱 | 说明 |
|---|---|
| **只看 CPU 代次，不看核显代次** | 决定稳定性的是**核显代次**。10 代 Comet Lake-U（Gen9.5）与 10 代 Ice Lake（Gen11）机型不可混用。 |
| **照抄工具/他人给的机型** | 凡只按 CPU 世代映射的算法，在 10 代（U 系/G 系核显分叉）上都要人工复核，并核对目标系统版本资格。见 `references/opcore-simplify-review.md`。 |
| **为「性能」选高档机型** | 45W 档压 15W CPU，只会让风扇曲线激进，**不会提升性能**。 |
| **为「省电」选低档机型** | 5W 档压 15W CPU，过度降频，反而更慢。 |
| **忽略目标 macOS 版本** | Tahoe 只支持 16,1/16,2/16,4；其余机型止步 Sequoia，硬上 Tahoe 需 OCLP 或降系统目标。 |
| **以为机型只是「名字」** | 机型同时决定：核显驱动选择、电源/温度曲线、背光与电池支持、macOS 升级资格。**改名 = 换平台。** |

---

## 六、核显仿冒参数（按平台，★ 为已验证）

| 平台 | device-id（注入） | ig-platform-id | 备注 |
|---|---|---|---|
| **Comet Lake-U（10 代 U）** | ★ `0x3E9B` | ★ `0x3E9B0000` | 原生 0x9B41 无驱动，必须伪装 |
| Coffee Lake-U / Whiskey Lake-U（8/8.5 代） | 通常无需伪装 | 按 WhateverGreen 文档 | 原生 0x3EA0 有驱动 |
| Coffee Lake-H（8/9 代 H） | 通常无需伪装 | 按 WhateverGreen 文档 | 原生 0x3E9B |
| Ice Lake（10 代 G） | 无需伪装 | 按 WhateverGreen 文档 | Gen11，机型必须选 16,2 / Air9,1 |
| Tiger Lake / Alder Lake-N（11/12 代，Xe-LP） | 见 `gen12-xe.md` | 走 WhateverXe 路线，**WG 须关闭** | GPU 由插件按 PCI ID 接管，不依赖 SMBIOS 声明；机型仍按双轴落 Gen11 族 |

> 非已验证平台的 ig-platform-id 请查 WhateverGreen 官方笔记本帧缓冲文档确认，
> **不要凭印象填写**——填错会导致黑屏或加速失效。

---

## 七、边界正在移动：11/12 代 Xe 与 NVIDIA 实验路线（2026-10 起）

**两条轴没有失效，但「无驱动」的边界外扩了。** 选型时的处理方式：

### 7.1 11/12 代 Xe-LP 核显（Tiger Lake / Alder Lake-N）

- Apple 的 Intel Mac 止于 10 代，**Gen12 没有真身机型**——不存在「正确的 Gen12 机型」；
- WhateverXe（Lilu 插件 + 修改版 OCLP + AMFIPass）按 **PCI device ID** 自动识别 GPU，
  **不依赖 SMBIOS 声明代次**；机型仍按双轴落在 Gen11 族（16,2 / Air9,1），TDP 照轴二；
- 因此审计时看到「机型 Gen11 ≠ 硬件 Gen12」：**若启用了 WhateverXe，降级为提示**（实验驱动兜底），
  否则维持轴一 FAIL 判定；
- 详见 `references/gen12-xe.md`（含 NootedGreen 对比、boot-args、DVMT 96MB 建议、WG 互斥）。

### 7.2 NVIDIA Turing+ 独显

- NullMoth 驱动（仅 macOS 15 Sequoia / x86_64）让 GTX 16 / RTX 20–50 / TITAN RTX /
  Quadro RTX 获得 Metal 加速；**Pascal 及更早（MX150/MX250/GTX 10 系）仍明确不支持**；
- 纯 NVIDIA 台式机：机型按驱动文档选（社区常见 `iMacPro1,1` / `MacPro7,1`），
  **不要套用 Intel 双轴表**；双显卡笔记本：核显双轴照旧，独显能否启用取决于 **mux/直显物理条件**；
- 详见 `references/nvidia-gpu.md`。

### 7.3 优先级不变

> 能选 6–10 代原生支持平台，**仍然优先原生**。实验路线服务「已经买了这些硬件」的人，
> 不是「买新电脑时的推荐」——这是本技能对两条新路线的固定立场。

---

## 八、台式机平台的三轴选型（iMac / Mac mini / Mac Pro）

> 完整的台式机验证方法（平台判定、三轴机型选型、笔记本专属检查跳过/降级、台式机特有关注项）
> 见 `references/desktop-validation.md`。本节只给**选型决策所需的桌面机型速查**，避免冲淡笔记本主线。

### 8.1 桌面与笔记本的轴语义差异

| 轴 | 笔记本语义 | 台式机语义 |
|---|---|---|
| 轴一 核显代次 | 同：SMBIOS 声明代次 = 物理核显代次 | 同；桌面常见 UHD 630（Gen9.5 原生免驱）/ UHD 7x0（Gen12 无驱动）/ 核显直出或独显直显 |
| 轴二 TDP 档位 | 机型写死功耗/温度曲线（15/28/45W）直接决定风扇与续航 | **改写为「iGPU-only vs +dGPU」取舍**：核显机选 iMac；+AMD 独显选 iMac20,2 / MacPro7,1 |
| 轴三 macOS 资格 | 同：机型决定系统上限 | 同；Tahoe 桌面机型见下表 |

### 8.2 台式机机型速查（含 Tahoe 资格）

| 机型 | board-id | 平台 / 核显 | macOS 26 Tahoe |
|---|---|---|---|
| iMac19,1 | `Mac-AA95B1DDAB278B95` | Coffee Lake-S / UHD 630（Gen9.5） | ❌（最高 Sequoia） |
| iMac19,2 | OC 自动生成 | Coffee Lake-S / UHD 630（Gen9.5） | ❌ |
| **iMac20,1** | `Mac-CAD8A5C1E5E6AADC` | Comet Lake-S / UHD 630（Gen9.5） | ✅（iGPU-only 桌面首选） |
| iMac20,2 | OC 自动生成 | Comet Lake-S / +AMD dGPU 原生 | ✅（带独显台式机首选） |
| Macmini8,1 | OC 自动生成 | Coffee Lake-S / UHD 630（Gen9.5） | ❌ |
| **MacPro7,1** | `Mac-27AD2F918AEAC861` | CFL/CML + 多 PCIe / +dGPU | ✅（工作站级，须配 AMD 独显） |

> ★ 同一颗 Coffee Lake-S（如 i9-9900K）：Sequoia 及更早用 **iMac19,1**；要上 **Tahoe** 必须换成
> **iMac20,1 / iMac20,2**（同代同核显 Gen9.5，仅年份/资格不同）。
> ★ **iMac21,1 / 21,2 是 2021 款 24" M1（Apple Silicon），不是 Intel 机型**——Apple 从未发布
> Rocket Lake 版 iMac，最后一个 Intel iMac 是 iMac20,x。11 代桌面（Rocket Lake-S，UHD 730/750
> = Gen12 无驱动）没有原生桌面机型可仿，惯例是 **iMac20,1**（+AMD 独显）或 **MacPro7,1**（纯独显）。
> ★ 台式机**合法使用** iMac / Mac mini / Mac Pro——这与「笔记本禁止套用台式机机型」并不矛盾：
> 后者只提醒笔记本用户不要误用台式机机型（背光/电池/电源语义错配），而非禁止台式机本身选用。
> 台式机的完整审计清单（CFG Lock / dGPU 路由 / USB 映射 / 声卡 codec）见 `references/desktop-validation.md` §2。
