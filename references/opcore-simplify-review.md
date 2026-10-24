# opcore-simplify 输出复核手册

> **定位：它是优秀的「采集器 + 加速器」，不是权威。**
> 本技能对其输出的默认姿态是**复核后采信**，尤其对 **SMBIOS 机型**与 **SSDT 清单**两项。

---

## 一、它是什么、产出什么

`OpCore-Simplify`（lzhoang2801，GitHub 开源）在 **Windows** 下运行，流程是：

1. **导出硬件报告**：内置调用 **Hardware Sniffer**，生成
   `SysReport/Report.json` + **ACPI 表转储**（这是它最有价值的产出）
2. **校验**：`report_validator.py` 检查主板/CPU/显卡/网卡等必填字段
3. **串接决策**：兼容性检查 → 推荐 macOS 版本 → **选 SMBIOS 机型** → 勾选 ACPI 补丁与 kext
4. **构建**：`Results/EFI`，构建前自动拉取 OpenCorePkg 与最新版 kext
5. **收尾提示**：打印 BIOS/UEFI 设置清单，并要求用 **USBToolBox 做 USB 端口映射**

主要模块：`acpi_guru.py`（ACPI 补丁）、`kext_maestro.py`（kext 选型，约 85 项候选）。

### 它做对的部分（要明确承认，别全盘否定）

| 做对的事 | 说明 |
|---|---|
| **硬件报告采集** | Windows 下一键拿到完整硬件清单 + ACPI dump，省掉大量手工查询 |
| **kext 版本与兼容性** | 构建前自动拉取最新版并做版本校验，避免版本错配 |
| **SSDTTime 常规补丁自动化** | FakeEC、FixHPET、PLUG、RTCAWAC 等 |
| **禁用不支持的 PCI 设备** | 含 Optimus 独显（Optimus / Bumblebee 方法或 `disable-gpu` 属性）、不支持的 Wi-Fi、NVMe 控制器 |
| **`_PRW` 睡眠修复** | GPRW / UPRW / HP 变体，防「合盖即醒」 |
| **常用设备定义** | ALS0 / BUS0 / MCHC / PMCR / PNLF / RMNE / IMEI / USBX / XOSI / GPI0 |
| **BIOS 设置清单** | 构建后打印，方便自查 |
| **USB 映射提示** | 明确要求加 `UTBMap.kext`、删 `UTBDefault.kext`；端口 >15 才启用 `XhciPortLimit` |

---

## 二、三类系统性短板 → 复核动作

### 短板 1：不做 macOS 版本资格过滤，核显代次映射需人工确认

README 原文宣称 *"Prioritize SMBIOS optimized for both power management and performance"*，
其 CML-U → 16,3 的默认映射在双轴上其实成立（16,3 = CFL-U 15W / Gen9.5），
**但它不做 macOS 版本资格过滤**——16,3 最高只到 Sequoia，目标 Tahoe 时该建议不可用。

> **真实案例（Dell Inspiron 5498）**
> - 硬件：i7-10510U（**Comet Lake-U**，10 代）＋ **UHD 620（Gen9.5）**，真实 **15W**
> - 工具首推：**MacBookPro16,3**
> - 16,3 真实身份（Apple 官方规格页，2026-10-23 复核）：2020 13" 双雷雳3 =
>   **Coffee Lake-U 15W / Gen9.5**
> - ⇒ 双轴复核：轴一 Gen9.5 = Gen9.5 ✅；轴二 15W = 15W ✅ ——**这一次工具没有推错**
> - 本机最终落 **MacBookPro15,4**（同为 CFL-U 15W/Gen9.5，装机实测充分）；
>   期间的 GPU hang 归因于 SMBIOS 声明 **Gen11** 的时期（**16,2**，Ice Lake/28W），
>   初稿曾把 16,2 与 16,3 身份弄反，已勘误（见 `case-dell-5498.md`）
> - ⚠️ 若目标 macOS 26 Tahoe：16,3 不可升（最高 Sequoia），Gen9.5 可升族只剩
>   16,1 / 16,4（45W H 系），需要权衡轴二代价

**复核动作**：双轴一致的机型不必推翻，但**必须补齐工具不做的三件事**——
版本资格核对、SSDT 对照本机 DSDT、BIOS 目视确认：

```
工具建议  : MacBookPro16,3（CFL-U / Gen9.5 / 15W）
双轴复核  : 轴一 Gen9.5 = Gen9.5 ✅ ；轴二 15W = 15W ✅
版本资格  : 16,3 最高 Sequoia ❌（目标 Tahoe 时需改推 16,1/16,4 或调整目标版本）
最终落地  : MacBookPro15,4（同代同档；目标 Monterey/Ventura 下与 16,3 等效）
```

**同类高风险组合**（凡 CPU 世代 ≠ 核显代次，都要人工复核）：
- 10 代 **U** 系（Comet Lake-U，Gen9.5）→ 别误配到 16,2 / Air9,1（Ice Lake/Gen11）
- 10 代 **G** 系（Ice Lake，Gen11）→ 对应 16,2 / Air9,1，映射一致可采纳
- 11 代及更新 → 走实验路线（见 `references/gen12-xe.md`），属另一类问题

### 短板 2：SSDT 是「广泛适用」的通用件，不保证路径匹配本机 DSDT

工具集成的 SSDTTime 补丁按**常见命名**生成（如 `EC0`、`PR00`），
但 OEM 的 DSDT 未必用这些名字。

**复核动作**：拿 `SysReport/` 里的 **ACPI dump**（这是工具给我们最好的东西），
逐项核对每个 SSDT 引用的设备路径在本机 DSDT 中是否真实存在。
→ 逐项判据见 `references/ssdt-audit.md`。

特别注意 **EC**：若本机 DSDT 已有真正的 `PNP0C09` 设备，
再叠加 FakeEC 会**重复定义**导致冲突——此时应**禁用**该 SSDT，而不是保留。

### 短板 3：看不见物理事实与 BIOS 状态

`Report.json` 里**没有**的信息：

- **BIOS 设置**：CFG Lock 是否解锁、DVMT 预分配多少、Secure Boot、CSM、VT-d、Above 4G
- **面板接口**：eDP 还是 LVDS（决定 PNLF 该不该加）
- **触控板总线**：I2C 还是 PS2（决定 VoodooI2C 还是 VoodooPS2）
- **摄像头连接方式**：USB 还是内建
- **是否 mux**：独显是否直连屏幕

**复核动作**：用 `references/intake-questions.md` 的 **批 4** 与 **批 BIOS** 补齐。
这两批是工具的能力盲区，必须由人工确认。

---

## 三、装机前的 Windows 静态审计（开不了机时唯一手段）

```bash
python3 scripts/audit_opcore_result.py <OpCore-Simplify 目录>     # Results/ 与 SysReport/ 的父目录
```

审计项与判据：

| # | 审计项 | 判据 / 风险提示 |
|---|---|---|
| 1 | **SMBIOS 双轴复核** | 从 Report.json 抽 CPU + 核显 → 与 `SystemProductName` 比对 → 输出「一致 / 代次错配 / 档位错配 / 两轴皆错」 |
| 2 | **核显属性** | `device-id` / `AAPL,ig-platform-id` / `framebuffer-fbmem` / `framebuffer-stolenmem` 是否齐全 |
| 3 | **`AppleXcpmCfgLock` / `AppleCpuPmCfgLock`** | **开了 = CFG Lock 没解锁** → 提示优先解锁，而非长期靠 quirk |
| 4 | **`DisableRtcChecksum`** | **笔记本盒盖睡死高发项**，缺失即告警 |
| 5 | **`XhciPortLimit`** | 仅临时用途，端口映射完成后应移除（⚠️ 在较新 macOS 上会导致端口异常） |
| 6 | **独显是否已禁用** | Optimus 必须禁；确认禁用方式（`_OFF` SSDT / `disable-gpu` 属性 / `-wegnoegpu`） |
| 7 | **kext 冗余 / 缺失** | 如无 I2C 设备却挂 VoodooI2C、VoodooI2C 与 VoodooPS2 并存等 |
| 8 | **SSDT 引用路径** | 解析 ACPI dump，核对其引用的路径是否存在（无 `iasl` 时降级跳过并明确提示） |
| 9 | **USB 映射待办** | `UTBMap` 未生成 / `UTBDefault` 未删 → 列为装后第一步 |

**脚本工程约束（写新脚本时复用）**：
- **只用 Python 标准库**（`plistlib` / `json` / `re` / `pathlib`），三平台通用；
- `config.plist` 一律用 **`plistlib`** 解析。⚠️ 实测踩坑：`plutil -convert json`
  遇 `<data>` 二进制段会报 `invalid object in plist for destination format`；
  `plistlib` 把 `<data>` 原生读作 `bytes`，无需 base64 解码；
- ACPI 反编译需 `iasl`，缺失时降级并**明确提示**未做该项检查，不可静默跳过。

---

## 四、复核产物的固定格式

不允许只说「建议改成 X」。每次复核必须输出：

```
┌─ 项目：SMBIOS 机型 ────────────────────
│ 工具建议 : 
│ 双轴复核 : 轴一 ⭕/❌ ；轴二 ⭕/❌
│ 结论     : 采纳 / 推翻 / 降级（需实测后定）
│ 理由     : 工具为什么这么推 + 为什么该推翻
└────────────────────────────────────────
```

---

## 五、拿不准时的站位原则

1. **工具与双轴冲突 → 听双轴的**（轴一错误会睡死，代价远大于其它）。
2. **工具给出了但本机 DSDT 里没有对应路径 → 先禁用，实测后再决定**。
3. **工具没给但本机明显需要**（如 Gen9.5 + 15W 却没给合适机型）→ 直接补上并说明理由。
4. **工具提示要做的收尾事项（USB 映射、BIOS 清单）→ 完整转达，不要省略**。
