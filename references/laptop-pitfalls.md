# 笔记本黑苹果：高频故障排查手册

> 每条按 **症状 → 取证命令 → 根因 → 修法** 组织。取证优先于猜测。

## 官方依据（关键结论的可验证来源）

| 结论 | 官方依据 |
|---|---|
| `DisableRtcChecksum`（盒盖睡死修复） | OpenCore Configuration 文档 → `Kernel → Quirks`（`github.com/acidanthera/OpenCorePkg` → `Docs/Configuration.pdf`） |
| `alc-layout-id` 优先级高于 `layout-id`、AppleALC 注入位置 | AppleALC FAQ（`github.com/acidanthera/AppleALC/wiki/FAQ`）；codec 支持列表见同仓库 `Supported-codecs` |
| Dell ALC236 麦克风节点官方定义（0x12 内置麦 / 0x14 扬声器 / 0x21 耳机） | Linux 内核 `sound/pci/hda/patch_realtek.c` → `ALC255_FIXUP_DELL1_MIC_NO_PRESENCE`（`git.kernel.org`） |
| 帧缓冲内存属性（2K/4K 卡顿修复） | WhateverGreen FAQ（`github.com/acidanthera/WhateverGreen/wiki/FAQ`） |
| NullMoth NVIDIA 驱动的支持矩阵与限制 | `github.com/nullmoth/nvidia-macos-driver`（README + issues） |
| opcore-simplify 生成物结构（Report.json / ACPI 文件夹） | `github.com/lzhoang2801/OpCore-Simplify` |
| SSDT 通用样例的适用条件 | Dortania → Getting Started With ACPI（`dortania.github.io/Getting-Started-With-ACPI/`） |
| 触控板 / 键盘驱动（VoodooI2C / VoodooPS2） | `github.com/VoodooI2C/VoodooI2C` / `github.com/acidanthera/VoodooPS2` |

---

## 0. 诊断通则：先分清「谁在烧 CPU」

用户报告"某个 App 卡"时，第一步永远是判定负载归属：

| 判据 | 含义 |
|---|---|
| **切到别的 App 后 CPU 立刻回落、别的 App 不卡** | 负载来自**前台 App 自身的渲染**（非系统后台守护） |
| 切走后 CPU 仍高 | 后台守护进程（如 mediaanalysisd、backupd、coreaudiod） |

```bash
# 实时看谁在占 CPU
top -l 1 -o cpu -n 15

# 看有没有东西挡着睡眠
pmset -g assertions
```

---

## 1. 盒盖睡眠 → 次日提示「电脑因问题重启」

**★ 关键认知：这不是 kernel panic**，而是 **Sleep Wake failure in EFI**（固件层睡眠失败 → 强重启）。重启画面一直留到开盖才被看到，所以时间点看起来"错位"。

### 取证
```bash
ls -lt /Library/Logs/DiagnosticReports/          # 找 Sleep Wake Failure_*.diag
last reboot | head -6                             # 重启是否落在盒盖时段
uptime                                            # 反推上次启动时刻
```
diag 文件内典型特征：
- `Sleep Wake failure in EFI`
- `Failure code: 0x1f`
- `Time Awake Since Boot: 16s`（刚重启 16 秒即被 swd 抓到）

### 根因
**Dell 等厂商 BIOS 会对 RTC/CMOS 某段区域做校验和（checksum）校验。**
macOS 经 OpenCore 的 RTC 系 NVRAM 模拟/legacy 写入会改写该区域 → 校验和失效 →
固件在**睡眠交接**时校验不过 → 报 Sleep Wake failure in EFI 并强制重启。

**时间线自洽性**：Reset NVRAM 后校验和干净、睡眠短暂正常；之后几天 macOS 持续改写 RTC
累积污染 → 表现为"稳定运行多天后，突然开始每晚盒盖必崩"。这正好解释为什么它是**渐进出现**而非装机即崩。

### 修法
1. `Kernel → Quirks → DisableRtcChecksum` 由 `false` 改为 `true`：
   ```xml
   <key>DisableRtcChecksum</key>
   <true/>
   ```
2. **Reset NVRAM**（必须）—— 用来清掉当前已被污染的校验和，让它重新生成干净值。
3. 回归：盒盖睡眠 → 等待 → 开盖唤醒，确认不再出现重启提示。

### 回退方案
若仍偶发 EFI 睡眠失败：
- 加 **`RTCMemoryFixup.kext`** + 启动参数 `rtcfx_exclude` 保护 Dell 特定 RTC 字节
  （默认从 `58-5B` 起试；精确范围需据本机 DSDT/CMOS 布局定）。
- 排除其它睡眠路径因素：**USB 端口映射是否齐全**、NVMe 是否加 `NVMeFix.kext`。

### ⚠️ 高频误判
`SSDT-RTCAWAC.aml` **只解决 RTC 设备的存在性**（AWAC → RTC 切换），
**不解决校验区被写坏**。加载了它 ≠ 堵住了这一环。

---

## 2. 离电异常掉电（断崖式跌电量）

### 取证
```bash
pmset -g log | grep -E "Sleep|Wake"      # ★ 看 Total Sleep/Wakes 是否为 0
pmset -g assertions                       # 看谁在阻止睡眠
system_profiler SPPowerDataType           # 电池 MaxCapacity vs DesignCapacity
```

### 根因
**系统从未真正睡眠**（`Total Sleep/Wakes: 0`）→ 全程亮屏高负载耗电。
挡住睡眠的常见断言：
- **USB 耳机/外设** → `0x4=USB`（如 iStore Audio）
- **coreaudiod** → `PreventUserIdleSystemSleep`
- **backupd-helper** → Time Machine 定时备份

### 修法
1. 关 Time Machine：`sudo tmutil disable`（或系统设置改为手动）
2. 拔掉 USB 外设（尤其音频类），改用 3.5mm
3. 先核对电池健康排除硬件老化（本例 `MaxCapacity = DesignCapacity = 4474`，电池健康无问题 → 确认为软件/睡眠问题）

---

## 3. 唤醒后部分按键失灵（如 6/7/Y/U/H/J/N/M）

### 判据
失灵按键集中在**键盘矩阵的相邻两列** → 典型 **EC 防鬼影「锁列」**。

### 分型
- **A 类**：可自愈（等待片刻 / 再按几次恢复）
- **B 类**：需重启才恢复

### 修法
- 主流 VoodooPS2Controller（2.3.8，Acidanthera 主线）**没有** `InitKeyDelay` 参数；SSDT-EC 已正确声明。
- ⇒ **不改 EFI**。做 **EC 复位**：关机 → 拔掉电源适配器 → 长按电源键 15 秒 → 重新接电开机。
- 顺带清洁键帽下异物。

---

## 4. 声卡 / 麦克风无声

### ★ 纠正一个高频误判：AppleALC 完整性判断
- AppleALC **1.9.x 起，codec 资源内嵌在二进制里**，不再是独立的 `Resources/ALC236` 目录。
- ❌ **错误判据**：以"有没有 `Resources/ALC236` 目录"判断 kext 是否完整 → 会误判正常 kext 为损坏。
- ✅ **正确判据**：看二进制体积 + 是否声明了你的 codec。
  - 本例：正常 1.9.8 = **3.8 MB**；损坏的 1.9.7 = **1.8 MB**（资源段缺失）。
  - ALC236 = `0x10EC0236`。

### layout 实测数据（Dell 5498 / ALC236）
| layout | 扬声器 | 耳机 | 两个麦克风 | 备注 |
|---|---|---|---|---|
| 54 | ✅ | ❌ | ❌ | 仅外放 |
| 13 | ✅ | ❌ | ❌ | 仅外放 |
| **15** | ✅ | ✅ | ❌ | **输出全通，推荐** |
| **99** | ✅ | ✅ | ❌ | **输出全通，推荐** |
| 21 | ❌ | ❌ | ❌ | 整卡未识别 |
| 28 | ❌ | ❌ | ❌ | 整卡未识别 |

**结论**：stock AppleALC 的 ALC236 布局**没有一个能正确路由本机麦克风节点**（内置麦 0x12 / 线控麦 0x19，参照 Linux `ALSA: hda - fix headset mic problem for Dell machines with alc236`）。
⇒ 继续换 layout-id 是死胡同，必须下沉到 **PinConfig / PathMap 定制**。

### 正确写法
- 用 **`alc-layout-id`**，优先级 **高于** `layout-id`，可无条件锁定、杜绝残留幽灵值
  （本机 ioreg 曾出现 `layout-id=7` 的幽灵回退值）。
- 位置：`DeviceProperties → Add → PciRoot(0x0)/Pci(0x1f,0x3)`（HDEF）。
- ❌ **不要写进 boot-args**：AppleALC 只认 DeviceProperties，boot-args 里的 `layout-id=` 会被无视。
  ```xml
  <key>PciRoot(0x0)/Pci(0x1f,0x3)</key>
  <dict>
      <key>alc-layout-id</key>
      <integer>99</integer>
  </dict>
  ```

### HAL 插件冲突
第三方 HAL 插件（Aunboxsoft/ScreenRec、Vrdaudio、BlackHole 等）会崩掉
**Core-Audio-Driver-Service** → 输出异常。
处理：移到 `/Library/Audio/Plug-Ins/HAL.disabled-backup-<时间戳>/`，重启 coreaudiod（需 sudo）。

---

## 5. 外接高分辨率屏（2K/4K）操作卡顿 ★ 核心修复

### 症状
2K（2560×1440）外屏下表格/录入操作卡顿，CPU + GPU 双高；
**一切换到其他 App，CPU 立刻回落、其他 App 不卡**。

### 排查（排除法，务必按序）
1. 关内屏，仅用外屏 2K → **仍卡** ⇒ 排除「双屏带宽」
2. 外屏降到 1080p → **不卡** ⇒ 锁定「分辨率」
3. 输入法在 1080p 下（搜狗 / 系统拼音）→ **均不卡** ⇒ 排除「输入法」

### 根因
**帧缓冲内存未正确分配** → iGPU 在高分辨率下走低效路径（甚至 VESA 回退），
合成开销暴涨 → CPU 等 GPU → 双双飙高。

### 修复：显式设置帧缓冲内存
在 `DeviceProperties → Add → PciRoot(0x0)/Pci(0x2,0x0)` 下显式设置：
```xml
<key>framebuffer-fbmem</key>
<data>...按分辨率配置...</data>      <!-- ★ 主要杠杆 -->
<key>framebuffer-stolenmem</key>
<data>AAAwAQ==</data>                <!-- 00 00 30 01 ≈ 19 MB，够用即可 -->
<key>framebuffer-unifiedmem</key>
<data>AAAAgA==</data>                <!-- 00 00 00 80 = 2048 MB 动态共享 -->
```
数据为 `<data>` 类型，按**小端** 32 位读取。

> ⚠️ **关于 fbmem 具体数值，不要盲抄**（同一台机出现过两个值）：
> - 本机最终**实际生效**的是 `fbmem ≈ 48 MB`（`00 00 00 03`），2K 下顺畅
> - 社区/早期流传的另一组是 `fbmem = 00 00 90 00`（≈9 MB）+ `stolenmem = 00 00 30 01`（≈19 MB），
>   也曾被报告有效
>
> 两组都有效的共同点是：**显式给了 fbmem**。
> 说明收益来自「帧缓冲 surface 内存被正确分配」，而不是某个固定魔法值。
> **建议**：先照当前配置实测；只有加第二块高分辨率屏或上 4K 时才调大 fbmem。

### ★ 反直觉但至关重要
**增大 `stolenmem` 不能提升性能。**
本机实测中 `stolenmem` 从 **32MB（`00 00 00 02`）降到 19MB（`00 00 30 01`）**，
同时**正确设置了 `fbmem`**，2K 反而变顺了。
⇒ 真正的杠杆是 **`fbmem`（帧缓冲 surface 内存）**，不是「显存越大越好」。
`stolenmem` 只是开机预留的连续系统 RAM；iGPU 实际干活用的是动态 `unifiedmem`（2048 MB）。
盲目加大 `stolenmem`：无收益，且过大会「stolen memory」启动 panic、白白占用 RAM。

### 若仍卡顿
1. 确认不是 VESA 回退：`system_profiler SPDisplaysDataType` 看 **Metal Support** 与芯片组名是否为通用名。
2. 降分辨率 / 确认刷新率 60Hz。
3. 减少合成负担：系统设置 → 辅助功能 → 显示 → 开「减少透明度」「减少动态效果」。
4. 减少同时打开的标签页 / 大工作簿。

---

## 6. HiDPI 不能用来缓解卡顿（常见误区）

- HiDPI 的本质是「UI 放大 + 更清晰」，**不改变 GPU 需要推送的像素量**。
- 2K 面板上：
  - HiDPI「看起来像 1280×720」= 2× 渲染 → 仍输出 2560×1440 ⇒ **像素不变，卡顿不变**
  - 「看起来像 1920×1080」（macOS 在 2K 上给的缩放档）= 渲染 3840×2160 再降采样 ⇒ **像素翻倍，更卡**
- ⇒ HiDPI 与「缓解高分辨率卡顿」**方向相反**。
- 命令行工具（display override plist / `displayplacer` / RDM）只能造「看起来像」的缩放档，**无法减少像素吞吐**。

---

## 7. 独显（Optimus / 笔记本 dGPU / NVIDIA 实验路线）

### 7.1 先过物理关：mux 与输出路径

- 绝大多数笔记本独显**未直连内屏**，内屏只由核显输出；macOS 无 mux 机制把它当渲染协处理器。
- ⇒ **无 mux 的 Optimus 双显卡本：独显无论架构一律禁用**（SSDT 屏蔽 / `-wegnoegpu`）。
  保留会引发睡眠/唤醒失败与空耗电——**这一条与驱动是否支持无关，是物理限制**。
- 有 mux / 独显直显输出、或只用外接输出的笔记本，以及台式机：才具备启用条件（再走 7.2 判断）。

### 7.2 架构分流（决定走哪条路）

| 独显架构 | 代表型号 | 路线 |
|---|---|---|
| **Pascal 及更早** | MX150 / MX250 / GTX 10 系 / GTX 9 系 | ❌ NullMoth 驱动明确不支持；**继续禁用** |
| **Turing 及更新** | GTX 16 / RTX 20 / 30 / 40 / 50 / TITAN RTX / Quadro RTX | ⚠️ NullMoth 实验路线（`nvidia-macos-driver`），**仅 macOS 15 Sequoia（x86_64）**；前置条件、排错与回退见 `references/nvidia-gpu.md` |

### 7.3 NullMoth 驱动的关键事实（速查）

- 实现 Metal 3（argument buffers tier 2 / 光追 / mesh shaders / MPS / MetalFX）、
  OpenGL（GL-on-Metal）、OpenCL、Core Image、Core ML；
  **未实现硬件视频编解码与 HDMI/DP 音频**；
- 安装路线：1401 工具自动改 OC（Win/Mac 均可）或手动 install.sh；需调整 SIP / SecureBootModel；
- ★ **AM5 平台（Ryzen 7000/8000/9000）必须确认 OpenRuntime `DisableVariableWrite` 在位**
  （v1.14.3 起修复「macOS 写主板固件致变砖」问题）；
- 实验性：主测 RTX 5060；设备表覆盖 ≠ 全部实测；已知全屏闪烁、热插拔无音频等问题；
- **双显卡笔记本注意**：即使独显受支持，内屏与合成仍走核显——**核显双轴照旧**（见 §0 与案例一）。

---

## 8. 应用级卡顿（如 WPS 表格录入）

- 部分非原生 AppKit 渲染的 App（如 WPS 表格）在**单元格编辑态会持续重绘**网格 / 公式栏 / 光标，
  把整窗交给 GPU 合成。原生 App 走系统高效文本管线，无此问题。
- 这解释了"录入就卡、切走就好"。
- 缓解（与分辨率无关，各分辨率均有效）：
  1. **自动计算改手动**（需要时 `F9` 重算）→ 录入时不再整表实时重算，CPU 大幅下降
  2. 关文档云同步 / 定时备份
  3. 关拼写检查、实时预览、条件格式实时刷新
  4. 降表格缩放比例、少开标签页 / 大工作簿
  5. 系统与 App 的动画/透明度特效减负

---

## 9. 改名/换机型的正确顺序 ★

```
① 先完善 SSDT（EC / PLUG / AWAC-RTC / 亮度 / USB 映射 …）
② 再换 SMBIOS 机型 + 重新生成三码
③ 最后才重做 CPUFriend（如需）
```

**理由**：SSDT 是地基，机型决定电源/温度曲线与平台语义，CPUFriend 是最后一层微调。
反过来做（先换机型再补 SSDT）会导致每次改 SSDT 都要重新核对机型相关的电源管理。

### 三码纪律
- 三码（SN / MLB / SystemUUID）**必须自己生成**，绝不可沿用网上 EFI 的
- ROM 填网卡 MAC
- `UpdateSMBIOSMode`：语义**随 OpenCore 版本变化**（`Create` / `Override` / `Custom` 各版本含义不同），
  ⚠️ **不要凭印象断言必须是哪档**——以所用 OC 版本文档为准。
  需要 Windows 双系统读到厂商原厂表时，选「保留原厂表」的那一档。
- 换机型后**必须 Reset NVRAM**

---

## 10. CPUFriend 的正确与错误姿势

| | 正确 | 错误 |
|---|---|---|
| 数据来源 | 从**本机实时频率向量**用 ResourceConverter 派生 | 移植别的机型的频率向量 |
| 调整范围 | 只压 PL1 / PL2 | 改频率向量本身 |
| 结果 | 功耗/温度可控 | ★ 实测：睡眠唤醒后卡低频、无法睿频 |

> 用户曾自制 CPUFriend 1.3.0 注入，导致"中午睡眠 → 下午唤醒后整机卡顿"。
> 处理：删除该 kext 并重置相关配置。**若非明确需要，笔记本默认不加 CPUFriend。**

---

## 11. 实验驱动排错速查（NullMoth / WhateverXe / NootedGreen）

> 详细前置条件见 `references/nvidia-gpu.md` 与 `references/gen12-xe.md`；
> 本节只做「症状 → 第一动作」。

| 症状 | 第一动作 |
|---|---|
| 11/12 代机器装 WhateverXe 后无显示 / 卡死 | 查 **WhateverGreen 与 WhateverXe 是否同开**（项目明文互斥，头号原因）；再查 boot-args `-allow3d -disablegfxfirmware` |
| Xe 机器 Metal 不可用 | OCLP root patch 是否装上；AMFIPass 是否生效；DVMT 是否 ≥96MB（固定 60MB 按 README 替代配置） |
| Xe 内屏不亮、只亮外屏 | TGL 无内屏场景确认 `-wex-nopanel`；有内屏不亮查 VBT/连接器配置 |
| Xe 睡眠唤醒后黑屏 | **已知边界**（完整系统睡眠未验证）——先降级到只睡显示器，别叠改动 |
| NootedGreen GuC 相关崩溃 | 试 `ngreenSched=5`（host preemptive）绕开 GuC/HuC 固件加载 |
| NVIDIA 驱动装完黑屏 / 闪烁 | 查 GSP 固件是否就位（`/Users/Shared/nvfw/nvidia/610.57.04`）；回退用「start once without driver」但注意**该选项在某些机器上冻结屏幕** |
| NVIDIA Metal 不可用 | 插件 bundle / 翻译器位置；SIP 与 SecureBootModel 是否按文档；隐私与安全性里是否允许了扩展 |
| NVIDIA 外接显示器无声音 | **已知未实现**（HDMI/DP 音频），不是配置错误 |
| AM5 机器出现固件异常 | 确认 OpenRuntime `DisableVariableWrite` 在位（v1.14.3+ 修复项） |
| 任何实验驱动需要回退 | 移除对应 kext + 恢复原配置（WG 恢复 / /L/E 还原）+ 恢复 EFI 备份；**保留一份无实验驱动可启动的 EFI 是底线** |

### 通用纪律

1. 先核对「路线组件清单」再谈玄学（顺序 / boot-args / DVMT / 权限）；
2. WG 与 WX 同开、驱动版本与 OC 配置同时改——都属**配置错误**，不是兼容性问题；
3. 一次只改一项，每步记录「显示 / 加速 / 睡眠」三项状态；
4. 结论必须标注**驱动版本 + macOS 小版本**（这些项目以周为单位迭代，结论时效极短）。
