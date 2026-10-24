# Gen12 / Xe-LP 核显路线：WhateverXe 与 NootedGreen（实验性）

> 11 代 Tiger Lake 与 12 代 Alder Lake-N 的 Xe-LP 核显，macOS 原生无驱动。
> 2026 年 10 月起出现两条实验路线。本文件给选型判断、前置条件与排错。
> **核心立场：均为实验性项目；本技能对「6–10 代原生支持平台」的推荐优先级不变。**

## 官方依据

| 内容 | 来源 |
|---|---|
| WhateverXe（Lilu 插件路线） | `github.com/b00t0x/WhateverXe`（README + `docs/device-selection.md`） |
| WhateverXe 配套修改版 OCLP | `github.com/b00t0x/OpenCore-Legacy-Patcher` |
| NootedGreen（Hookcase + /L/E 路线） | `github.com/sgiammori/NootedGreen`（InsanelyMac 主帖持续更新） |
| early-stage 参考 | `github.com/oldcat618/Appleinteltgldriver.kext`（开发早期，不建议生产使用） |

---

## 一、路线对比（先选路线，再动手）

| | **WhateverXe** | **NootedGreen** |
|---|---|---|
| 做法 | Lilu 插件，运行时改 Apple 驱动 + 修改版 OCLP 打 root patch | Hookcase + 替换 `/Library/Extensions` 里的 Apple 核显 kext/bundle |
| 支持硬件 | Tiger Lake（`9A40/9A49/9A78`）、Alder Lake-N / Twin Lake（`46D0–46D4`，需 PCI rev 0） | ICL（Gen11）/ TGL（Gen12）/ ADL（未知）/ RPL（WIP，开发者自述 ICL/TGL 约 90%） |
| GPU 识别 | 按 PCI device ID **自动**，不依赖 SMBIOS/CPU 型号 | 内联 CPUID 读真实 CPU 型号（device-ID 伪装骗不过它） |
| 依赖 | Lilu + WhateverXe.kext + 修改版 OCLP + **AMFIPass Mod** | Hookcase + 改权限的 Apple kext + `csrutil` 关闭 + kextcache 重建 |
| 侵入性 | 中（改系统驱动 + root patch） | **高（动 /L/E 里的 Apple 组件）** |
| 互斥 | **与 WhateverGreen 不能同时启用** | 同样不能与 WG 同开（社区参数 `-ngwegcoex`） |
| 已验证能力 | Metal + OpenGL 加速、HDMI/DP 输出、**双外显 1080p@60Hz**、HEVC/H.264 硬编解码（TGL 实测）、显示睡眠唤醒 | 开发者在 Sonoma 上推进；社区报告 3D 加速仍时好时坏 |
| 已知未验证 | 三屏、**完整系统睡眠**、更高分辨率、更多接口组合 | ADL 兼容性、RPL 未成 |

**选择建议**：
- 追求**侵入性较小、文档较完整** → WhateverXe；
- 机器是 ICL/TGL 且接受重装 `/L/E` 手术、想搏更高完成度 → NootedGreen；
- **oldcat618/Appleinteltgldriver**（含各 fork）：仅打开 accelerator user client、无 Metal 命令提交，**远离**。

---

## 二、WhateverXe 安装前置条件

1. **组件**：`Lilu.kext` → `WhateverXe.kext`（**Lilu 之后加载**）+ 修改版 OCLP（装 root patch）+ **AMFIPass Mod**；
2. **OC 配置**：kext 顺序 Lilu 在前；boot-args 必加：
   ```
   -allow3d -disablegfxfirmware
   ```
   Tiger Lake **无内屏**的机器再加 `-wex-nopanel`（ADL-N/Twin Lake 后端不需要）；
3. **BIOS**：DVMT Pre-Allocated **建议 96MB**（固定 60MB 的机器按 README 的替代配置）；
4. **★ 与 WhateverGreen 互斥**：多显卡机器也不例外——同时启用会冲突。上 WhateverXe 就要关 WG；
5. macOS 版本：路线图覆盖 Monterey–Tahoe，**以 Releases 实际支持为准**。

## 三、NootedGreen 安装前置条件（简述）

1. `csrutil` 关闭 + 隐私安全设置允许；
2. Hookcase 与改过 `IOPCIPrimaryMatch`（如 `0x9A498086`）的 `AppleIntelTGLGraphics` / `AppleIntelTGLGraphicsFramebuffer` 放入 `/Library/Extensions`，`chmod -R 755` + `chown -R root:wheel`；
3. 删 `AuxiliaryKernelExtensions.kc` → `sudo kextcache -i /` → 重启；
4. boot-args 视场景：`-ngreentglfb` / `-ngreentglwithgfx` / `-allow3d -disablegfxfirmware`；调度器 `ngreenSched=`（默认 3=GuC 固件；伪载设备/ RPL 建议 5=host preemptive，绕开 GuC/HuC 固件加载失败）；
5. 排错日志：`/private/var/log/Lilu_*.txt` + `log show --style syslog --predicate 'processID == 0'` 抓 `[IGFB]`。

---

## 四、排错速查（按症状）

| 症状 | 优先检查 |
|---|---|
| 完全无显示 / 启动卡死 | ① WG 与 WX 是否同开（**头号原因**）② boot-args 是否齐（`-allow3d -disablegfxfirmware`）③ DVMT 是否够 |
| 加速不起来（Metal 不可用） | OCLP root patch 是否装上；AMFIPass 是否生效；NootedGreen 路线查 kext 权限与 kextcache |
| 内屏不亮 / 只亮外屏 | TGL 无内屏场景确认 `-wex-nopanel`；有内屏不亮查 VBT/连接器配置 |
| 睡眠唤醒后黑屏 | WhateverXe 的「完整系统睡眠」尚未验证——已知边界，先降级到只睡显示器 |
| NootedGreen 上 GuC 相关崩溃 | 试 `ngreenSched=5`（host preemptive）绕开固件加载 |
| 需要回退 | 移除 WhateverXe.kext + 恢复 WG + 对应 boot-args 删除；NootedGreen 路线恢复 /L/E 原厂文件 + kextcache |

---

## 五、对本技能双轴选型的影响

1. **11/12 代 Intel 在 Apple 产品线没有真身**（Intel Mac 止于 10 代 Comet Lake），
   所以**不存在「正确的 Gen12 机型」**——机型仍按双轴落在 Gen11 机型族
   （`MacBookPro16,2` / `MacBookAir9,1`，TDP 档位照轴二选），GPU 由 WhateverXe 按 PCI ID 自行接管；
2. **审计时看到「机型 Gen11 ≠ 硬件 Gen12」不要一律报错**：若 config 启用了 WhateverXe，
   该错配由实验驱动兜底，降级为提示（审计脚本已按此处理）；
3. **macOS 版本**：路线图说 Monterey–Tahoe，但每版能力不同——以 Releases 与文档为准，
   别按「原生支持表」推断；
4. **优先级不变**：能选 6–10 代原生支持平台，仍然优先原生。实验路线是
   「已经买了 11/12 代机器」的补救，不是「买新电脑时的推荐」。

---

## 六、排错纪律

1. 先确认路线组件齐全（Lilu 顺序 / OCLP / AMFIPass / boot-args / DVMT），再谈玄学；
2. WG 与 WX 互斥是**配置错误不是兼容性问题**，看到冲突直接改；
3. 一次只改一项；每步记录「显示 / 加速 / 睡眠」三项状态；
4. 保留一份无实验驱动可启动的 EFI——**这是所有实验性驱动的共同底线**；
5. 社区项目迭代以周计，任何结论标注项目版本与提交时间。
