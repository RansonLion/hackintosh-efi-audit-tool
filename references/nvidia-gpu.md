# NVIDIA 独显路线：NullMoth 开源驱动（实验性）

> 2026 年 10 月起，社区开源驱动首次让 **Turing 及更新架构** 的 NVIDIA 显卡
> 可以在 macOS 15 Sequoia 上获得显示 + Metal 加速。本文件是选型与排错参考。
> **核心立场：这是实验性项目，不是稳定日常方案。** 推荐顺序永远是
> 「能换硬件解决 > 等驱动成熟 > 最后才上实验驱动」。

## 官方依据

| 内容 | 来源 |
|---|---|
| 驱动本体、支持列表、安装文档 | `github.com/nullmoth/nvidia-macos-driver`（README + `docs/CARD-SUPPORT.md`） |
| 自动安装工具 1401（Win/Mac） | `github.com/nullmoth/1401` |
| AM5 固件写入修复（v1.14.3+） | 同仓库 Release notes（依赖 OpenRuntime 的 `DisableVariableWrite`） |

---

## 一、支持矩阵（截至 2026-10）

| 维度 | 状态 |
|---|---|
| **GPU 架构** | **Turing 及更新**：GTX 16 系、RTX 20/30/40/50 系、TITAN RTX、受支持的 Quadro/RTX 工作站卡 |
| **明确不支持** | **Pascal 及更早**（MX150 / MX250 / GTX 10 系 / GTX 9 系）——列表原文即排除 |
| **macOS 版本** | **仅 15 Sequoia**（x86_64）；不支持 macOS 26 Tahoe，不支持 Apple Silicon |
| **目标机器** | Intel Mac + OpenCore PC（已能正常启动 macOS 的机器） |
| **验证程度** | 开发者主测 **RTX 5060**（15.7.x / 15.8.1）；RTX 5070/5080 为用户报告；**设备表覆盖 ≠ 全部实测** |
| **许可** | PolyForm 非商业（禁止用于出售整机） |

### 实现的接口

- **Metal 3**：argument buffers tier 2、光线追踪、mesh shaders、MPS、MetalFX 路径；
- **OpenGL**（经 Apple 的 GL-on-Metal）、**OpenCL**、Core Image、Core ML；
- ⚠️ **硬件视频编解码未实现**（特性列表未含），视频回放走 CPU；
- ⚠️ **HDMI/DP 音频未实现**（显示器热插拔时无音频）。

---

## 二、架构原理（排错时需要知道数据流）

```
Metal App → NVMTLDriver.bundle（Metal 插件）
  → libnvmtl_translate.dylib（AIR 着色器 → SPIR-V，Rust 实现，基于 metal2vulkan）
  → Mesa NVK（Vulkan 后端，NAK 编译器路径）
  → NVIDIA r610 开源 GPU 内核模块（macOS 上运行）
  → GSP 固件 610.57.04（位于 /Users/Shared/nvfw/nvidia/610.57.04）
```

内核侧四个组件：**NVRMFB**（显示帧缓冲）、**NVAccel**（WindowServer 合成加速）、
**NVRMAGDC**（显示策略 shim）、**NVRM**（核心内核模块）。

**排错意义**：故障定位时先判断断在哪段——插件没加载（Metal 不可用）/
固件缺失（初始化失败）/ 内核模块没跑（无显示）——三类的日志特征不同。

---

## 三、安装路线与前置条件

### 路线 A：1401 自动安装（推荐先试）

1. 从 Releases 下载 `nullmoth-nvidia-*.tar.gz` + `1401-Mac-*.dmg`；
2. 先按常规流程启动一次 macOS（不带驱动）；
3. 运行 1401：它会**自动改 OpenCore 配置 + 制作安装器**（可从 Win 或 Mac 环境构建，相当于"反向 Boot Camp"）；
4. 首次启动后在 **系统设置 → 隐私与安全性**里允许加载的扩展；
5. 有 Windows 版 1401（`nullmoth/1401`），可在装机阶段从 Windows 侧准备。

### 路线 B：手动安装（install.sh）

按 README 逐项放置组件（插件 bundle、翻译器、GPUBundles、固件、内核扩展），
再手动改 OpenCore。**只在 1401 不覆盖你的场景时使用**，且必须对照 README 的组件清单逐项核对。

### OpenCore / 系统前置条件

| 项 | 要求 | 说明 |
|---|---|---|
| OpenCore | 已能正常引导 macOS | 驱动面向"已有可用 OpenCore 的机器" |
| SIP | 需按文档调整 | 属安全边界削弱，报告须明示 |
| SecureBootModel | 按文档调整 | 同上 |
| Above 4G / Resizable BAR | 建议开启 | 大显存卡需要 |
| **AM5 平台必做** | OpenRuntime 的 **`DisableVariableWrite`** | ★ v1.14.3 起修复"macOS 可写主板固件致变砖"问题；Ryzen 7000/8000/9000 用户**升级前必须确认此项** |
| 备份 | 整个 EFI + Time Machine | 驱动回退依赖它们 |

---

## 四、笔记本边界：先过物理关，再谈驱动

**这条比驱动本身更重要，能省掉无数次无效尝试：**

1. **Optimus 无 mux 的笔记本**（内屏只由核显输出的双显卡机器，如 Dell 5498 + MX250 这类结构）：
   **独显无法给内屏加速**——macOS 没有 mux 切换机制把独显当渲染协处理器。
   **即使架构受支持，也不要为内屏启用独显。**
2. **可行的笔记本形态**：独显直连输出（有 mux / 独显出屏）的机型、或只用外接显示器输出的场景；
3. **台式机 / 准系统**：无上述限制，是这条路线的主战场。

**判据**：拆机看显示输出是否走独显，或查机型规格（mux 是否存在）。查不到就按"无 mux"处理。

---

## 五、排错速查（按症状）

| 症状 | 优先检查 |
|---|---|
| 装完黑屏 / 花屏闪烁 | ① 固件是否就位（`/Users/Shared/nvfw/...`）② 回退选择器里的 "start once without driver" 项——注意该项在某些机器上会**冻结屏幕**，慎用 |
| Metal 不可用（`system_profiler SPDisplaysDataType` 看不到加速） | 插件 bundle / 翻译器是否装对位置；SIP 与 SecureBootModel 是否按文档 |
| 内核扩展没加载 | 隐私与安全性里是否点了允许；kextcache 是否需要重建 |
| HDMI/DP 显示器无声音 | **已知未实现**，不是配置错误，等驱动更新 |
| 视频播放卡 / 占用高 | **已知未实现硬解**，属预期行为 |
| AM5 机器升级后异常 | 确认 OpenRuntime `DisableVariableWrite` 在位（v1.14.3+ 修复项） |
| 需要回退 | 用 1401 的卸载路径或移除组件 + 恢复 EFI 备份；保留一份装驱动前的完整 EFI 是底线 |

---

## 六、与双轴选型的关系

启用 NVIDIA 独显**不改变**「核显 + SMBIOS 双轴」的基本盘：

- 双显卡笔记本：内屏与合成仍走核显，**双轴照旧**（案例一的睡死根因与独显路线无关）；
- 纯 NVIDIA 机器（无核显）：机型按驱动文档要求选（社区常见 `iMacPro1,1` / `MacPro7,1`），
  **以官方安装文档为准，不要套用 Intel 双轴表**；
- 无论哪条路线，**机型仍是平台**——电源曲线、升级资格、外设语义都随之改变。

---

## 七、排错纪律（与主技能一致）

1. 先备份后安装，保留无驱动可启动的 EFI；
2. 一次只改一项（驱动版本 / OC 配置二选一）；
3. 每步记录 Metal 支持状态与显示输出状态（`system_profiler SPDisplaysDataType`）；
4. 遇不可解问题先回退到可用状态，再收集日志——**不要留着半坏的驱动继续叠改动**；
5. 报告任何结论时标注驱动版本与 macOS 小版本（此项目迭代极快，结论时效以周计）。
