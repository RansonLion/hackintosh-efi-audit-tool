# SSDT 精准化审计：通用件 → 本机匹配

> opcore-simplify / SSDTTime 生成的是**广泛适用**的通用补丁。
> 能用 ≠ 精确匹配。**每一份 SSDT 都要拿本机 DSDT 核对引用路径。**

## 核对方法

1. 拿到 ACPI dump（opcore-simplify 的 `SysReport/` 下即有，或用 SSDTTime / `acpidump`）
2. 反编译：`iasl -d DSDT.aml SSDT-*.aml`（需 `iasl`；缺失时明确提示，不要静默跳过）
3. 对每个 SSDT，检查它引用的设备路径（`_SB.PCI0.LPCB.EC0`、`PR00`、`PNLF`…）在 DSDT 中**是否真实存在**
4. 检查是否存在**重复定义**（DSDT 已有同名设备又叠一份）

> **Phase 3 升级（v1.0.1，`audit_opcore_result.py` 已自动化）**：核对从「肉眼比对」升级为
> **完整路径 + 对象类型 + 重名**三层，且有 `iasl` 时自动反编译 `.aml`：
> - **完整路径**：`External` 声明按 `_SB.PCI0.X` 全路径在 DSDT 声明表里查（不是简单字符串包含）；
> - **对象类型**：`DeviceObj` / `ProcessorObj` 等类型与 DSDT 中实际声明类型不符 → 该 SSDT 加载失败；
> - **重名**：SSDT 新建 `Device`/`Processor` 与 DSDT **同路径** → `AE_ALREADY_EXISTS`
>   （典型翻车：DSDT 已有 EC，又叠 SSDT-EC 的 `Scope(\_SB.PCI0.LPCB){Device(EC)}`；
>   注意只查 Device/Processor——同名 Method 重定义是合法覆盖手法，如 SSDT-USB-Reset 覆盖 `RHUB._STA`）；
> - **裸名跳过**：`External (STAS, IntObj)` 这类既无路径又无 `_SB` 的裸名引用（实为 DSDT 深处
>   Field 单元）解析规则无法静态建模，跳过严格核对并计数，不误报；
> - 实机案例（ThinkCenter，7 份 SSDT）：SBUS/EC/MCHC/PLUG/USBX/RTCAWAC 通过，
>   **USB-Reset 被抓到真实缺陷**——`External (_SB_.PCI0.XHC_.RHUB)` 的 `XHC_` 与 Scope 的
>   `XHC` 不一致（多一个下划线），完整路径核对直接命中。

---

## 逐项审计表

| SSDT | 作用 | 核对要点 | 笔记本常见坑 |
|---|---|---|---|
| **EC / FakeEC / EC-USBX** | 嵌入式控制器（电池、电源管理基础） | DSDT 中是否**已有**真正的 `PNP0C09` 设备 | ★ 已有真 EC 却加 FakeEC → **重复定义冲突**。Apple 笔记本常见名 `EC0`；OEM 可能是 `EC`/`H_EC`/`ECDV`。**有真的就不加假的** |
| **PLUG** | 启用 XCPM 原生电源管理 | 第一个 CPU 对象路径（`PR00` / `C000` / `_PR.CPU0`）是否存在 | 路径错 → XCPM 不生效，**睿频与续航全废**。多 CPU 机型要确认指向的是**活跃**的那个 |
| **PNLF** | 内屏亮度调节 | 面板是否为 **eDP** | LVDS 面板加了无效；eDP 缺它则无亮度条 |
| **GPI0 / XOSI** | I2C 触控板 / GPIO 引脚 | 触控板是否真的是 **I2C** 总线 | 触控板是 PS2 时加了纯属冗余；是 I2C 却缺了 → 触控板不动或手势丢失 |
| **AWAC / RTCAWAC** | 启用 RTC 设备（AWAC → RTC） | 平台是否有 AWAC（300 系及更新） | ★ **只解决设备存在性，不解决 RTC 校验区被写坏**。盒盖睡死仍需 `DisableRtcChecksum` |
| **dGPU `_OFF`（Optimus）** | 禁用不兼容独显 | `_OFF` 方法路径**必须来自本机 DSDT** | ★ 照抄他人路径 → 独显没真关 → 持续耗电、睡眠/唤醒失败。禁用后应实测功耗下降 |
| **HPET / FixHPET** | IRQ 冲突修复 | 是否真的存在冲突 | 不需要时误加可能引入新问题 |
| **`_PRW`（GPRW / UPRW / HP）** | 防「合盖即醒」 | 是否有返回 0x6D/0x0D 的 `_PRW` | 笔记本唤醒源多（USB、网卡），缺修复 → 一睡就醒 |
| **RMNE / IMEI** | 网卡改名 / MEI 设备 | 改名目标路径是否存在 | 改名不匹配 → 网卡识别异常或 iServices 异常 |
| **USBX** | USB 电源属性 | 与 EC 配套 | 单独加而无 EC 时常无意义 |
| **ALS0 / BUS0 / MCHC / PMCR** | 环境光 / SMBus / 内存控制器 / PMC | 按平台需要 | 台式机向的补丁误用到笔记本上属常见冗余 |

---

## ★ 一个高效手法：统计 ACPI 补丁的命中数

拿到 `config.plist` 的 `ACPI → Patch` 后，**逐条在 DSDT 里数 Find 数据出现几次**：

| 命中数 | 判读 |
|---|---|
| **1** | 精确命中 → 补丁是**对着本机 DSDT 生成**的（opcore-simplify / SSDTTime 的正确行为） |
| **0** | 补丁无效（DSDT 里没有这段）→ 删掉，否则浪费且误导 |
| **≥2** | ⚠️ 可能命中了不该改的位置 → 需人工确认 |

> 实例（X250）：EC `_STA→XSTA` 命中 1、HPET `_CRS→XCRS` 命中 1、
> PIC/RTC/TIMR IRQ 各命中 1、`_OSI→XOSI` 命中 11（全局改名，正常）。
> 全部精确命中 → **可以确认这些补丁不是从网上抄的**，这一项应给工具记功。

这个手法比「看 SSDT 文件名猜」可靠得多，建议每次审计都做。

## 平台差异速记

| 项 | Haswell / Broadwell | Skylake 及更新 |
|---|---|---|
| 音频控制器路径 | `Pci(0x1b,0x0)` | `Pci(0x1f,0x3)` |
| CPU 对象 | `_PR.CPU0` | `_PR.PR00` |
| 是否需要 RTCAWAC | 通常**不需要**（无 AWAC） | 常有，需要 |

---

## 判读规则（三条）

1. **路径不存在 → 先禁用，不要硬留。** 引用不存在路径的 SSDT 要么无效，要么触发
   `AE_NOT_FOUND`，后者会在开机日志里刷屏甚至拖慢启动。
2. **重复定义 → 保留"真的"那份。** 尤其是 EC：OEM 的 DSDT 里通常已有 EC 设备，
   此时 FakeEC 是多余的。
3. **不确定就做减法。** 从「最小可用集」开始（EC、PLUG、PNLF 视面板、RTCAWAC 视 AWAC、
   dGPU 禁用），跑通后再按症状补。一次加十几份 SSDT 出问题根本没法定位。

---

## 最小可用集（笔记本起步建议）

| 必需 | 条件 |
|---|---|
| EC（或确认 DSDT 已有真 EC 则不加） | 几乎总是需要 |
| PLUG | 需要 XCPM 原生电源管理 |
| PNLF | 面板为 eDP |
| RTCAWAC | 平台有 AWAC |
| dGPU `_OFF` / `disable-gpu` | 有不兼容独显（绝大多数 Optimus 本） |
| `_PRW` 修复 | 出现「合盖即醒」症状时 |
| GPI0 / XOSI | 触控板为 I2C |

**其它一律按需添加，不做预防性堆砌。**

---

## 验证命令（装好后）

```bash
# 看 ACPI 报错（AE_NOT_FOUND 等）
log show --predicate 'process == "kernel" AND eventMessage CONTAINS "ACPI"' --last 1h

# 看 SSDT 是否真的加载
# OpenCore 引导日志 opencore-*.txt 中搜对应 SSDT 名

# 电池 / EC 是否工作
ioreg -l | grep -i -E 'AppleSmartBattery|EC0|AppleACPIEC'

# 亮度是否可调（PNLF）
# 系统设置 → 显示器，拖动亮度条

# 独显是否真关（功耗对比）
# 关机前/后对比 battery 放电速率，或用 powermetrics 看 package power
```
