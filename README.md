[English](README.en.md) | 简体中文
# Hackintosh EFI 审计工具

面向 **Intel 黑苹果笔记本与台式机**的 OpenCore 引导式配置与 EFI 审计工具——按三轴规则选定正确的 MacBook 仿冒机型，分步采集全部硬件事实，审计工具生成的 EFI，逐项核对本机 DSDT 与 SSDT 的匹配关系，最后完成电源管理与逐项外设验收。

> **核心原则**：先定机型（核显代次 + TDP 功耗档位 + macOS 版本资格三轴），再按机型推配置。机型选错，后面所有 kext 和补丁都是在错误的地基上盖楼。
>
> **第二原则**：**工具给的结论默认待复核。** [OpCore-Simplify](https://github.com/corpnewt/OpCore-Simplify) 是优秀的采集与加速器，但它不做 macOS 版本资格过滤（轴三），也看不见 BIOS 状态与本机 DSDT。从 10 代起核显代次与 CPU 世代脱钩——凡「按 CPU 世代映射」的输出必须人工复核。

## 覆盖能力

- **三轴 SMBIOS 选型** —— 核显代次 × TDP 档位 × macOS 版本资格（Tahoe 仅支持 16,1/16,2/16,4），并纠正社区流传最广的四条错误（例如 **MacBookPro16,2 = Ice Lake/Gen11，Tahoe ✅；16,3 = Coffee Lake-U/Gen9.5，Tahoe ❌**；10 代 Comet Lake-U 笔记本在 Apple 产品线中根本没有对应真身）。
- **7 阶段引导流程** —— 环境判定 → 硬件采集（分 5 批，每轮最多 4 问，每问附「怎么拿到答案」）→ 机型选型 → SSDT 审计 → 电源四件套（pmset / CPUFriend / CFG-Lock / DVMT）→ 外设验收 → 回归巡检。进度落盘为清单文件，支持跨会话断点续做。
- **OpCore-Simplify 产物复核** —— 在**机器还没装上 macOS 之前**即可静态审计 `Report.json` + ACPI 文件夹 + `config.plist`：三轴复核、核显属性、Quirk 风险、独显是否真禁、kext 冗余、SSDT 引用路径、USB 映射待办。自动识别 2026 两条实验性 GPU 路线，并强制检查**「WhateverGreen 与 WhateverXe 同时启用」**这类配置级冲突。
- **SSDT 精准化审计** —— 每个通用 SSDT 逐项对照本机 DSDT（EC/EC-USBX、PLUG、PNLF、GPI0/XOSI、AWAC、dGPU `_OFF`、HPET/`_PRW`），并给出对应的笔记本常见坑。
- **实验性硬件路线** —— NVIDIA Turing+ 独显（NullMoth，仅 Sequoia）与 11/12 代 Xe-LP 核显（WhateverXe / NootedGreen）：选型判断、前置条件、排错。实验 ≠ 稳定；这些路线只服务「已经买了这些硬件」的人。
- **台式机平台** —— iMac / Mac mini / Mac Pro 的机型选型与 EFI 审计（笔记本专属检查自动跳过/降级）。AMD CPU 平台不在范围内。

## 如何使用

装好本技能后，对 AI 助手直接说：

1. **复核 OpCore-Simplify 产物** —— 「请复核 opcore-simplify 生成的 EFI：Report.json 和 ACPI 文件夹发给你，config.plist 也一起。」→ `scripts/audit_opcore_result.py`
2. **挑选仿冒机型** —— 「我的机器是 i7-10510U + UHD620，想装 macOS Ventura，该仿冒哪个 MacBook？board-id 是多少？」→ `scripts/recommend_smbios.py`
3. **审计现有 EFI** —— 「这是我 EFI 里的 config.plist，帮我逐项审一遍。」→ `scripts/efi_config_audit.py`（✅/⚠️/❌ 分级结论，笔记本/台式机自动区分）
4. **从零装机或按症状排障** —— 「准备装黑苹果」或「盒盖睡死 / 核显没加速 / 离电掉电快 / 麦克风无声，帮我排查。」
5. **跨平台采集硬件事实** —— Windows / macOS / Linux 三平台只读命令 → `scripts/collect_hw.py`

## 仓库结构

```
SKILL.md                          # 技能本体（工作流、原则、纪律）
scripts/
  models.json                     # 机型单一数据源（代次 / TDP / Tahoe / T2 / board-id）
  recommend_smbios.py             # 三轴机型推荐（--platform laptop|desktop）
  audit_opcore_result.py          # OpCore-Simplify 产物静态审计（装机前可用）
  efi_config_audit.py             # config.plist 逐项审计（笔记本/台式机通用）
  collect_hw.py                   # 跨平台只读硬件采集
templates/
  intake-checklist.md             # 勾选式进度表（跨会话断点续做）
references/                       # 按需加载，一主题一文件
  smbios-selection.md             # ★ 三轴决策表 + macOS 版本资格专节
  intake-questions.md             # ★ 分批提问模板 + 采集命令
  opcore-simplify-review.md       # ★ 工具输出复核清单 + 已知误判
  ssdt-audit.md                   # ★ 逐 SSDT 审计判据
  power-management.md             # ★ pmset / CPUFriend / CFG-Lock / DVMT
  peripheral-checklist.md         # ★ 逐项外设验收
  nvidia-gpu.md                   # ★ NullMoth 路线（支持矩阵 / OC 前置 / 排错）
  gen12-xe.md                     # ★ WhateverXe / NootedGreen 对比与排错
  desktop-validation.md           # ★ 台式机平台验证
  laptop-pitfalls.md              # 按症状索引的排障手册
  config-workflow.md              # config.plist 清单 + EFI 安全修改流程
  case-dell-5498.md               # ★ 完整真实案例
```

所有脚本仅依赖 **Python 标准库**，Windows / macOS / Linux 通用。两个审计脚本均支持 `--json`（机器可读 findings，exit 0/2），结尾会**自动调用 `ocvalidate`**（找到就跑，找不到明确提示、绝不静默跳过）。

## 本技能强制对抗的反模式

- ❌ 照抄网上「同机型」EFI（三码必须自己生成；SSDT 要匹配本机 DSDT）
- ❌ 采信工具给的机型而不做三轴复核（10 代 U 系轴一/轴二必错；目标 Tahoe 还需核对轴三）
- ❌ 忽略 macOS 版本资格——Tahoe 只支持 16,1/16,2/16,4，其余机型止步 Sequoia
- ❌ 盲目加大 `framebuffer-stolenmem` 求「性能」（无收益，过大易启动 panic）
- ❌ 自制 CPUFriend 移植他机频率向量（实测：睡醒后卡低频、无法睿频）
- ❌ 把 `layout-id` 写进 boot-args（AppleALC 只认 DeviceProperties）
- ❌ 指望 Optimus 独显给内屏加速（物理上不同路，无 mux）
- ❌ 不解锁 BIOS CFG-Lock，长期靠 quirk 兜底
- ❌ 把 NullMoth / WhateverXe / NootedGreen 当稳定日常方案
- ❌ WhateverGreen 与 WhateverXe 同时启用（项目明文互斥，属配置错误）

## 安全纪律（动 ESP 之前必读）

1. **绝不硬编码盘号**——盘号会漂。用卷 UUID，或运行时查 `diskutil list | grep "EFI EFI"`。
2. **每次修改前先备份**（带时间戳，ESP 内 + 工作区各一份）。
3. **一次只改一项**，改机型/音频/RTC 相关项后必须 Reset NVRAM，然后回归测试。
4. 用 `plutil -lint` 校验语法；**`ocvalidate` 不通过的东西绝不交付**。
5. 写完后 `sync` → 卸载 → 重新挂载 → 复验 SHA（FAT 有写缓存）。
6. 移动/删除 kext ⇒ 同一遍里设 `Enabled=false` 或删条目。
7. 用 `plutil`/`plistlib` 改 plist，不要手写替换（`plutil -convert json` 遇 `<data>` 段会报错）。

## 许可证

见 [LICENSE](LICENSE)。
