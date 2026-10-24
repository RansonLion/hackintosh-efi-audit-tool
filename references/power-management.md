# 电源管理四件套：pmset / CPUFriend / CFG-Lock / DVMT

> 这四项决定了笔记本黑苹果的**续航、温度、风扇噪音与睡眠稳定性**。
> 优先级：**先换对机型（双轴轴二）> 解锁 CFG Lock > 调 pmset > 最后才考虑 CPUFriend。**

## 官方依据

| 结论 | 官方依据 |
|---|---|
| `AppleXcpmCfgLock` / `AppleCpuPmCfgLock` 应尽量避免 | OpenCore Configuration 文档 → `Kernel → Quirks` 节（原文 "should be avoided whenever possible"；`github.com/acidanthera/OpenCorePkg` → `Docs/Configuration.pdf`） |
| `framebuffer-fbmem` / `framebuffer-stolenmem` / `framebuffer-unifiedmem` 语义 | WhateverGreen FAQ → Intel Graphics 节（`github.com/acidanthera/WhateverGreen/wiki/FAQ`） |
| CPUFriend / ResourceConverter 用法与警告 | CPUFriend 仓库（`github.com/acidanthera/CPUFriend`，含 ResourceConverter 与官方警告） |
| `DisableRtcChecksum` | OpenCore Configuration 文档 → `Kernel → Quirks` 节 |
| `pmset` 各项语义 | 本机 `man pmset` |

---

## 一、pmset：确认「真的睡过」

### 基线采集

```bash
pmset -g                 # 当前电源策略
pmset -g assertions      # ★ 谁在阻止睡眠
pmset -g log             # 睡眠/唤醒历史
pmset -g cap             # 机器支持的电源能力
```

### ★ 挡睡断言排查（最高频根因）

```bash
pmset -g assertions | grep -i prevent
```

常见断言：

| 断言 | 来源 | 处理 |
|---|---|---|
| `0x4=USB` | USB 外设（如 USB 耳机/声卡）持续持有断言 | **拔掉**或换 3.5mm |
| `PreventUserIdleSystemSleep` | `coreaudiod`（音频播放中） | 停止播放；检查后台音频进程 |
| `backupd-helper` | Time Machine 备份 | `sudo tmutil disable` 或改手动 |
| `PreventSystemSleep` | 下载、渲染、SSH 会话等 | 结束任务后再睡 |

> **真实案例**：离电续航测试中电量 69% → 6% 断崖下跌。
> `pmset -g log` 显示 **`Total Sleep/Wakes: 0`** —— 系统整夜**从未真正睡眠**。
> 元凶是 USB 耳机的 `0x4=USB` 断言 + `coreaudiod` 的 `PreventUserIdleSystemSleep`。
> 处理：关 Time Machine + 拔 USB 耳机 → 恢复正常睡眠。
>
> **教训**：排查掉电时，**先看有没有真睡过**，不要一上来就怀疑电池。

### 验证「真的睡过」

```bash
pmset -g log | grep -E "Sleep|Wake" | tail -20
```

**判据**：`Total Sleep/Wakes` 计数**随测试增长**才算睡过。计数不动 = 一直在醒着耗电。

### 常用设定（按需，不要盲抄）

| 项 | 建议 | 说明 |
|---|---|---|
| `hibernatemode` | 笔记本默认 3（安全睡眠） | 改动前先理解含义，别照抄台式机方案 |
| `standby` / `powernap` | 离电场景可考虑关 `powernap` | Power Nap 会在睡眠中定期唤醒做网络/同步 |
| `tcpkeepalive` | 按需 | 睡眠中维持 TCP 连接会耗电 |
| `womp` / `proximitywake` | 按需关闭 | 网络唤醒 / 靠近唤醒会造成意外唤醒 |
| `lowpowermode` | 离电可开 | 降频换续航 |
| `displaysleep` / `sleep` | 按需 | 屏幕与系统睡眠计时 |

> ⚠️ 改 pmset 前先 `pmset -g` 记录原值；**一次只改一项**，改后实测。

---

## 二、CPUFriend：**默认不需要**

### 什么时候才该考虑

**只有一种正当理由**：平台档位（SMBIOS 机型的功耗曲线）**与 CPU 真实 TDP 不匹配**，
且**换机型这条路走不通**时。

> 若只是「风扇吵」，先考虑**换对机型**——这是治本。
> Dell 5498 的案例里，16,4（45W 档）压 15W CPU 导致风扇狂转，
> 最终回退 15,4（15W 档）彻底解决，**不需要任何 CPUFriend**。

### 安全姿势（若确需使用）

1. 用 **ResourceConverter** 从**本机实时频率向量**派生 DataProvider
2. **只压 PL1 / PL2**（长时/短时功耗墙）
3. **绝不移植其他机型的频率向量**

### ★ 绝对禁止：移植他机频率向量

> **真实翻车**：自制 CPUFriend 1.3.0 + 配套 kext 注入后，
> **睡眠唤醒后系统卡顿**——频率向量/功耗限制与 CPU 不匹配，唤醒后卡在低频无法睿频。
> 处理：删除 CPUFriend 相关 kext 后恢复。
>
> 教训：**"别人的机型同 CPU" ≠ 频率向量通用**。平台的 cTDP、散热、功耗墙都不同。

### 验证清单（改前 → 改后）

| 阶段 | 检查 |
|---|---|
| 改前基线 | `powermetrics` 记录功耗与风扇；`yes > /dev/null` 加压看能否睿频 |
| 改后复核 | ① 风扇是否更安静 ② 负载下频率是否仍正常 ③ **唤醒后是否还能睿频** ④ 有无 panic |
| 失败处理 | 立即删除并 Reset NVRAM；确认 EFI 无悬空引用（kext 删了要同步改 `Enabled`） |

---

## 三、CFG-Lock：原生电源管理的前提

### 它是什么

BIOS 会锁定 **MSR `0xE2`**。macOS 的 XCPM 原生 CPU 电源管理需要写这个寄存器；
被锁就写不了。

### 优先级

```
① 在 BIOS 里关掉 CFG Lock            ← 正解
② BIOS 无此选项 → 解锁隐藏菜单 / RU.efi / modGRUBShell 的 setup_var
③ 都不行 → 开 AppleCpuPmCfgLock / AppleXcpmCfgLock    ← 兜底，是代价不是解法
```

### 开 quirk 的代价

| Quirk | 作用 | 代价 |
|---|---|---|
| `AppleCpuPmCfgLock` | 绕过 MSR 0xE2 写保护（传统电源管理） | 非原生路径 |
| `AppleXcpmCfgLock` | 同上，针对 XCPM | **原生 CPU 电源管理被降级**，睿频策略与续航受损 |

**所以在审计工具里看到这两个 quirk 被开启，第一反应应是「CFG Lock 没解锁」，
而不是「配置已完备」。**

### 验证

```bash
# 看睿频是否真的在工作（需安装 Intel Power Gadget 或 CPU-S 类工具）
# 更朴素的方法：
sysctl -n machdep.cpu.brand_string
# 用 yes 加压，观察频率是否上到标称睿频
```

---

## 四、DVMT：BIOS 锁死时的绕过

### 问题

OEM 笔记本（尤其 Dell）常把 **DVMT Pre-Allocated** 锁在小值且不可改。
帧缓冲内存不足 → 核显走低效路径 → **外接 2K/4K 时明显卡顿**。

### 绕过（推荐，安全）

用 WhateverGreen 的帧缓冲属性覆盖驱动的内存请求：

| 属性 | 含义 | 备注 |
|---|---|---|
| **`framebuffer-fbmem`** | 帧缓冲 **surface 内存** | ★ **主要杠杆** |
| `framebuffer-stolenmem` | 开机预留的连续 stolen 内存 | 够用即可 |
| `framebuffer-unifiedmem` | 动态共享内存上限（常见 2048MB） | iGPU 实际干活主要靠它 |

注入位置：`DeviceProperties → Add → PciRoot(0x0)/Pci(0x2,0x0)`

### ★ 核心认知：fbmem 是主杠杆，stolenmem 不是越大越好

> **实测证据**：stolenmem 从 **32MB** 降到 **19MB**，同时**正确设置 fbmem** 后，
> 2K 反而变顺了。
>
> 这直接推翻「显存越大越快」的直觉。原因：
> - iGPU 起来后用的是 `unifiedmem`（动态共享，通常 2048MB）干所有活；
> - `stolenmem` 只是开机阶段预留的一小块连续内存；
> - 真正的收益来自 **fbmem 让帧缓冲被正确分配**，而非某个数值更大。
>
> **盲目加大 stolenmem**：无收益 + 预留浪费 RAM + 过大时触发
> **stolen memory 启动 panic**。

### 什么时候该调大 fbmem

当前分辨率下已顺畅就不动。**要加第二块高分辨率屏或上 4K 时**，
按总像素量调大 `framebuffer-fbmem`，`stolenmem` 维持合理值即可。

### 不推荐：硬改 BIOS DVMT

用 `setup_var` 直接改 BIOS 变量在 Dell 等机型上风险高（变量名/偏移不公开、
可能变砖、变量常被隐藏或锁定）。WG 的帧缓冲覆盖正是社区推荐的**安全替代方案**。

### 验证

```bash
system_profiler SPDisplaysDataType | grep -iE 'VRAM|Metal'
# 在 2K/4K 下做持续 UI 操作（如表格录入），观察是否还有卡顿
```

---

## 五、四件套的排查顺序（速查）

| 症状 | 先看 |
|---|---|
| 离电断崖掉电 | **pmset**：有没有真睡过（`pmset -g log`）+ 挡睡断言 |
| 风扇狂转 | **机型档位**（轴二）→ CFG Lock → 最后才想 CPUFriend |
| 唤醒后卡顿 / 不睿频 | 是否注入了**不匹配的 CPUFriend**；CFG Lock 是否靠 quirk 兜底 |
| 外接 2K/4K 卡顿 | **DVMT / fbmem** |
| 盒盖后第二天提示重启 | 不是电源管理，是 **RTC 校验区** → 见 `laptop-pitfalls.md` |
