# 完整案例：Dell Inspiron 5498（i7-10510U / UHD 620 / MX250）

> 这份案例是技能的**教学样本**：它完整演示了「机型怎么选错、错在哪一轴、
> 每次修正带来什么新问题、最终如何收敛」。
> 建议阅读顺序：机型三次变更 → 各类副作用 → 收敛后的遗留项。

---

## 硬件事实

| 项 | 值 |
|---|---|
| 机型 | Dell Inspiron 5498 |
| CPU | i7-10510U（**Comet Lake-U**，10 代，后缀 U，真实 **15W**） |
| 核显 | **UHD 620**（**Gen9.5**，原生 device-id `0x9B41`，macOS 无驱动 → 伪装 `0x3E9B`） |
| 独显 | NVIDIA **MX250**（Pascal）→ **Optimus，无 mux** → 禁用 |
| 声卡 | Realtek **ALC236** |
| 系统 | macOS Ventura 13.7.8 |

---

## 一、机型的三次变更（核心时间线）

### 第 1 站：MacBookPro16,3（工具推荐）→ 随后的 Gen11 声明期

- 来源：早期用 **opcore-simplify** 定制的初版 EFI（当时目标 Monterey）
- ⚠️ **勘误（2026-10-23）**：16,3 真实身份是 **Coffee Lake-U 15W / Gen9.5**
  （2020 13" 双雷雳3，依 Apple 官方规格页核实）——就 5498 的双轴而言**并不算错**。
  初稿把它误写成 "Ice Lake / Gen11 / 28W" 并归因为 GPU hang 根因，属**机型身份张冠李戴**。
- GPU hang 的真实归因期：SMBIOS 声明 **Gen11** 的时期（**MacBookPro16,2**，更早还有 9,1）：
  **GPU hang → 睡死**，`/Library/Logs/DiagnosticReports` 累计 `gpuRestart` **11 次**，
  触发进程 **mediaanalysisd**（Spotlight / 照片分析）
- 根因：16,2 = **Ice Lake / Gen11** → 声明 Gen11 → CoreML/Vision/MPS 挑了 Gen11 的
  compute kernel → 下放到 Gen9.5 的 UHD 620 在 batch 边界静默 stall → `gpuRestart`
- 教训：**这是稳定性问题，不是性能问题。** 轴一错了会睡死；
  而 16,2 与 16,3 只差一个数字，核显代次与 Tahoe 资格却完全不同——
  **机型身份必须对照官方规格页核实，不能凭印象。**

### 第 2 站：MacBookPro16,4（轴一修好，轴二仍错）

- 目的：找同为 **Gen9.5** 的机型
- 结果：✅ `gpuRestart` **归零**，GPU 问题根治
- 新症状：❌ **风扇狂转**。WPS 编辑（尤其 Word 图片裁切）持续负载时风扇高速运转
- 根因：16,4 = 2019 16" = **Coffee Lake-H 45W 档**，压在真实 15W 的 i7-10510U 上，
  功耗/风扇曲线过于激进
- 教训：**机型的功耗曲线写死在平台里，无法干净覆盖。**

### 第 3 站（插曲）：自制 CPUFriend —— 翻车

- 尝试：用 CPUFriend 1.3.0 自制 DataProvider + 配套 kext，想压功耗
- 结果：❌ **睡眠唤醒后系统卡顿**（频率向量/PL 与 CPU 不匹配，唤醒后卡低频无法睿频）
- 处理：**删除**，并再次禁用 Spotlight 保稳定；核对 EFI 确认无悬空引用
- 教训：**"同 CPU 的别的机型"的频率向量 ≠ 通用**。平台的 cTDP、散热、功耗墙都不同。
- 更重要的教训：**治本应该是换对机型，而不是给错误平台打补丁。**

### 第 4 站（终局）：MacBookPro15,4 ✅

- 15,4 = 2019 13" 双雷雳3 = **Coffee Lake-U，Gen9.5，15W**
- **轴一** ✅ Gen9.5 = UHD 620 → GPU 修复保留
- **轴二** ✅ 15W = i7-10510U 真实档位 → 风扇安静、续航正常
- 附加收益：**零自定义 kext**

---

## 二、收敛后逐个解决的副作用

### 2.1 盒盖睡眠 → 次日开盖提示「电脑因问题重启」

- 表象像内核崩溃，实际是 **Sleep Wake failure in EFI**（固件层）
- 证据：`last reboot` 显示**连续两晚都在傍晚盒盖时段**重启；
  诊断文件 `Sleep Wake Failure_*_MacBook-Pro.diag`（Failure code `0x1f`）
- 根因：**Dell BIOS 校验 RTC/CMOS 校验和**，macOS 经 OpenCore 的 RTC 系 NVRAM 写入
  改写该区 → 固件睡眠交接校验不过 → 强制重启
- 关键辨析：已加载的 `SSDT-RTCAWAC` **只解决 RTC 设备存在性，不解决校验区**
- 修复：`Kernel → Quirks → DisableRtcChecksum = true` + **Reset NVRAM**
- 回退：仍偶发则加 `RTCMemoryFixup.kext` + `rtcfx_exclude` 保护 Dell 特定 RTC 字节

### 2.2 离电断崖掉电（69% → 6%）

- 排查：`pmset -g log` → **`Total Sleep/Wakes: 0`** —— 系统**从未真正睡眠**
- 元凶：USB 耳机的 `0x4=USB` 断言 + `coreaudiod` 的 `PreventUserIdleSystemSleep`
- 电池本身健康（MaxCapacity = DesignCapacity）
- 修复：关闭 Time Machine（`sudo tmutil disable`）+ 拔掉 USB 耳机改 3.5mm
- 教训：**排查掉电，先看有没有真睡过，别先怀疑电池。**

### 2.3 外接 2K 显示器下 WPS 录入卡顿

- 对照实验（很干净）：
  1. 关内屏仅用外屏 2K → **仍卡**（排除双屏因素）
  2. 外屏降到 1080p → **不卡**（锁定分辨率）
  3. 1080p 下切换输入法 → **都不卡**（排除输入法）
- 结论：瓶颈是 **UHD 620 在 2K 下的填充率**，**不是 EFI 配置错误**（Metal 3 正常、device-id 正确）
- 修复：配置 **`framebuffer-fbmem`** + `framebuffer-stolenmem`
- ★ 反直觉证据：stolenmem 从 **32MB 降到 19MB**、同时**正确设置 fbmem** 后 2K 反而变顺
  → 证明**主杠杆是 fbmem，不是「显存越大越快」**

### 2.4 唤醒后部分键位失灵（6/7/Y/U/H/J/N/M）

- 定性：键盘矩阵 **EC 防鬼影锁列**，属 **A 类（可自愈）**
- 处理：EC 复位（关机拔电长按电源 15s）+ 清理键位；**不改 EFI**
- 教训：A/B 类判断很重要——A 类改 EFI 无效，误判会白白折腾

### 2.5 麦克风（未解决，已决策放弃）

- ALC236 实测 6 个官方 layout：

| layout | 扬声器 | 耳机 | 麦克风 |
|---|---|---|---|
| 54 | ✅ | ❌ | ❌ |
| 13 | ✅ | ❌ | ❌ |
| 15 | ✅ | ✅ | ❌（有设备无电平） |
| 99 | ✅ | ✅ | ❌（有设备无电平） |
| 21 / 28 | ❌ | ❌ | ❌（整卡未识别） |

- 判读：输出节点映射对了，**输入节点没接进输入流 / 引脚配置不对** →
  **PinConfig 层面问题，换 layout 是死胡同**
- 决策：**保留 15 或 99（输出全通），麦克风不再追**
- 副产品教训：曾误判「AppleALC 缺 Resources/ 目录 = 文件损坏」——
  实际上 **1.9.x 起资源内嵌二进制**，不能用目录有无判断

### 2.6 NVIDIA MX250 —— 确认无解

- 核实社区新工具（NullMoth `nvidia-macos-driver`）后确认三条皆不满足：
  1. 支持 **Turing 及更新**，MX250 是 **Pascal** → 不在列
  2. 仅支持 macOS 15 Sequoia → 本机是 Ventura
  3. 笔记本 **Optimus 无 mux**，即便驱动可用也无法为内屏加速
- 结论：**保持禁用**，核显是唯一可用 GPU

---

## 三、最终稳定配置（要点）

```
SystemProductName       : MacBookPro15,4
device-id               : 0x3E9B             （Comet Lake-U 必须伪装）
AAPL,ig-platform-id     : 0x3E9B0000
framebuffer-fbmem       : 已配置（★ 2K 流畅的关键）
framebuffer-stolenmem   : 已配置（够用即可，非越大越好）
alc-layout-id           : 15 或 99           （输出全通；麦克风无解）
DisableRtcChecksum      : true               （★ 解决盒盖睡死）
独显                    : 禁用
CPUFriend               : 不使用（换对机型后无需）
```

---

## 四、可复用的方法论（本案例真正要教的）

1. **机型是"平台"不是"名字"**：改机型会同时改变核显驱动选择、电源/温度曲线、
   背光与电池支持、macOS 升级资格。
2. **先分轴再动手**：把症状归到轴一（睡死/崩溃）还是轴二（风扇/续航），
   再决定往哪改。混着改会来回打转（本例 16,2→16,4 正是如此）。
3. **副作用是信号不是噪音**：换机型后出现风扇狂转，说明轴二错了，
   **不要靠 CPUFriend 去压**——那是给错误地基打补丁。
4. **排查要有对照实验**：2K 卡顿那次的三组对照（单屏/降分辨率/换输入法）
   是锁定根因的关键，比"试一堆设置"高效得多。
5. **知道什么时候停**：麦克风问题走到 PinConfig 层面后，投入产出比已不划算，
   明确"保留输出、放弃麦克风"也是合理决策。
6. **每次改动前备份 + 一次只改一项 + Reset NVRAM**：本例所有翻车都能快速回滚，靠的就是这条。
