# hackintosh-efi-audit-tool

黑苹果（OpenCore）EFI 审计与引导式配置的 GLM Skill：笔记本 / 台式机通用，装机前静态审计 + 分步硬件采集 + 机型选型 + SSDT 核对 + 电源与外设验收。

## 核心能力

- **三轴机型决策**：核显代次 + TDP 功耗档位 + macOS 版本资格，定仿冒 MacBook / iMac / Mac mini / Mac Pro 机型（SMBIOS）
- **分步硬件事实采集**：核显 / 独显 / 网卡 / 触控板 / 键盘 / 声卡 / 麦克风 / 摄像头 / USB，Windows / macOS / Linux 跨平台
- **opcore-simplify 复核**：静态审计其在 Windows 下生成的 EFI（默认不采信其机型与 SSDT 结论）
- **SSDT 精准化审计**：逐项核对 SSDT 是否匹配本机 DSDT
- **电源四件套**：pmset / CPUFriend / CFG-Lock / DVMT，以及外设验收清单
- **实验路线覆盖**：NVIDIA Turing+（NullMoth，仅 Sequoia）与 11/12 代 Xe-LP（WhateverXe / NootedGreen）

## 目录结构

```
├── SKILL.md              # 技能入口：工作流 + 分诊
├── references/           # 13 篇专项手册（选型 / SSDT / 电源 / 外设 / 台式机验证等）
├── scripts/              # 4 个脚本 + models.json（仅依赖 Python 标准库）
│   ├── collect_hw.py           # 跨平台硬件采集（只读）
│   ├── recommend_smbios.py     # 三轴机型推荐（平台感知）
│   ├── efi_config_audit.py     # config.plist 审计（笔记本/台式机通用）
│   └── audit_opcore_result.py  # opcore-simplify 产物静态审计
└── templates/
    └── intake-checklist.md     # 勾选式进度表（跨会话断点续做）
```

## 安全说明

- 所有脚本**只读**，不修改任何文件、不改任何设置
- 仅使用 Python 标准库，无第三方依赖、无网络请求、无遥测
- `subprocess` 仅调用本地 `iasl` / `ocvalidate` 做校验（列表参数、无 shell 拼接），找不到时明确提示而不静默跳过
- BIOS 设置（CFG Lock / DVMT / Secure Boot 等）任何脚本都读不到，必须人工进 BIOS 目视确认，脚本会提示

## 适用人群

已有合法 macOS 使用授权、在自有硬件上进行技术调试与排错的用户。本工具仅做配置审计与建议，不分发系统镜像、不含 Apple 专有文件。

## 许可证

MIT License，详见 [LICENSE](LICENSE)。
