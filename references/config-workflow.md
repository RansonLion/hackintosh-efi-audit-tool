# 笔记本黑苹果：OpenCore 配置落地工作流

---

## 一、config.plist 笔记本关键清单

| 区块 | 关键项 | 笔记本要点 |
|---|---|---|
| `PlatformInfo` | `Generic/SystemProductName` | 按三轴决策定机型（核显代次 + TDP 档位 + macOS 版本资格，见 `smbios-selection.md`） |
| | `Generic/SystemSerialNumber` / `MLB` / `SystemUUID` | **必须自己生成**，不可沿用他人 EFI |
| | `Generic/ROM` | 填网卡 MAC |
| | `UpdateSMBIOSMode` | ⚠️ 语义**随 OC 版本变化**（`Create`/`Override`/`Custom` 各版含义不同），以所用版本文档为准；需 Windows 读到原厂表时选「保留原厂表」那档 |
| `DeviceProperties` | `PciRoot(0x0)/Pci(0x2,0x0)` | `device-id`、`AAPL,ig-platform-id`、`framebuffer-fbmem`、`framebuffer-stolenmem`、`framebuffer-unifiedmem` |
| | `PciRoot(0x0)/Pci(0x1f,0x3)` | 声卡：`alc-layout-id`（★ 优先于 `layout-id`） |
| `Kernel/Add` | kext 列表 | 见下节；**删除 kext 必须同步 `Enabled=false`** |
| `ACPI/Add` | SSDT 列表 | 见下节 |
| `Kernel/Quirks` | 各项 | 见下节 |
| `NVRAM/Add/.../boot-args` | 启动参数 | 按机型与硬件定制，勿堆砌无关参数；**危险参数见下节专表** |

---

## 一之补、★ boot-args 危险参数分级表（审计脚本按此判级）

| 参数 | 级别 | 说明 |
|---|---|---|
| `-igfxvesa` | ❌ **FAIL** | 核显强制 VESA：**Metal 完全无加速**（等于白丢核显），最常见于误信「修复花屏」偏方。正解是修 framebuffer / ig-platform-id / BusID，不是关加速 |
| `-radvesa` | ❌ **FAIL** | AMD 独显强制 VESA：Metal 完全无加速，删除 |
| `nv_disable=1` | ⚠️ WARN | 禁用 NVIDIA 独显属性注入（10.13 前老写法）；确认独显确实要禁用才有意义，现代做法是 WhateverGreen + SSDT 屏蔽，两者留一即可 |
| `-wegnoegpu` | ℹ️ INFO | 禁用独显：Optimus 笔记本属合理操作；台式机带独显直出时不应加 |
| `alcid=` / `layout-id=` | ❌ **FAIL** | ★ 反模式：AppleALC **只读** DeviceProperties 的 `alc-layout-id`，boot-arg 写法无效 |
| `-v` / `debug=0x100` | ⚠️ WARN | 调试参数：刷屏并拖慢启动，日常建议去掉；`keepsyms=1` 可保留（便于读 panic 日志） |

---

## 二、Kernel → Quirks：笔记本重点项

| Quirk | 建议 | 说明 |
|---|---|---|
| **`DisableRtcChecksum`** | ★ **`true`** | Dell 等 BIOS 校验 RTC/CMOS 校验和，macOS 写入会致其失效 → **盒盖睡眠后次日提示重启**。改后须 Reset NVRAM |
| `AppleXcpmCfgLock` | 视 BIOS | CFG Lock 无法关闭时启用 |
| `AppleXcpmExtraMsrs` | MSR 0xE2 未解锁时 | 部分平台需要 |
| `ProvideCurrentCpuInfo` | 视情况 | 提供 CPU 信息给内核 |
| `LapicKernelPanic` | `false`（一般） | 部分老平台需要 |
| `PowerTimeoutKernelPanic` | ★ **`true`** | 修复（而非触发）电源状态切换/睡眠唤醒时的 kernel panic，最常见于数字音频；Dortania 对全部平台推荐 `true` |
| `XhciPortLimit` | 仅映射期临时开 | 正式使用应做 **USB 端口映射**替代 |

---

## 三、必备 SSDT / ACPI

| SSDT | 作用 | 是否必需 |
|---|---|---|
| **SSDT-EC**（或 SSDT-EC-USBX） | 声明嵌入式控制器，电池/电源管理基础 | ★ 必需 |
| **SSDT-PLUG** | 启用 XCPM 原生电源管理 | ★ 必需 |
| **SSDT-RTCAWAC** | AWAC → RTC 设备切换 | 视平台（300 系 + 新主板常见） |
| **SSDT-PNLF** | 内屏亮度调节 | 笔记本必需 |
| USB 端口映射（SSDT / kext） | 修复睡眠、USB 供电与识别 | ★ 强烈建议 |
| SSDT-XOSI / 改名补丁 | 绕过某些 OEM 的 _OSI 检查 | 视情况 |

> ⚠️ **SSDT-RTCAWAC 只解决 RTC 设备存在性，不解决 RTC 校验区被写坏。**
> 加载了它 ≠ 堵住盒盖睡死这一环——那需要 `DisableRtcChecksum`。

---

## 四、Kext 清单（笔记本典型）

| Kext | 用途 | 备注 |
|---|---|---|
| **Lilu** | 补丁平台 | 必须放第一位 |
| **WhateverGreen** | 核显帧缓冲 / 背光 / 独显屏蔽 | ★ 必需 |
| **AppleALC** | 板载声卡 | ★ 注意版本完整（1.9.x 资源内嵌二进制，体积约 3.8MB） |
| **VirtualSMC** + **SMCBatteryManager** | SMC 模拟 / 电池电量 | ★ 必需 |
| **VoodooPS2Controller** | PS2 键盘（多数 PC 笔记本） | 与 I2C 二选一或并存 |
| **VoodooI2C**（+ 卫星 kext） | I2C 触控板 | 视机型 |
| **AirportItlwm** / **itlwm** | Intel 无线网卡 | 按 macOS 版本选 |
| **NVMeFix** | NVMe 电源管理 / 睡眠 | 建议 |
| **ECEnabler** 等 | 电池读数 | 视机型 |
| **CPUFriend** | 频率/功耗微调 | ⚠️ 慎用，见 pitfalls §10 |
| **XHCI-unsupported** | 非原生 USB 控制器兼容 | ⚠️ **多数平台冗余**：Dortania 清单仅 H370/B360/H310/Z390(pre-Mojave)/X79/X99/部分 ASRock 需要；AMD 明确不需要；Ice Lake+ 与 400 系（B460/Z490+）为原生控制器。确认 USB 各口正常后可移除 |

---

## 四之补、UEFI → Drivers 联动检查（审计脚本按此判级）

| Driver | 联动规则 |
|---|---|
| **OpenRuntime.efi** | ★ 必备（没有它 NVRAM 写入/启动卷宗解析都受影响） |
| **OpenCanopy.efi** | 与 `Misc → Security → PickerMode = External` 配套；PickerMode 为 `Builtin`/`External` 之外的值与 Opened 不一致时报 |
| **ResetNvramEntry.efi** | 装了它但 `AllowNvramReset` 未开 → picker 不显示重置入口（白装）；改机型/音频/RTC 后必须 Reset NVRAM |
| **apfs_aligned.efi** | APFS 写对齐，建议加载 |
| **HfsPlus.efi** | 安装介质/旧 HFS+ 卷需要 |

> `Kernel/Quirks` 与 `UEFI/Drivers` 的 Enabled × 文件一致性（Enabled=false 残留、孤儿文件）
> 由审计脚本自动核对，见 SKILL.md 的 `--json` 输出。

---

## 五、★ EFI 安全修改流程（改 ESP 必走）

```
1. 挂载 EFI（按卷 UUID，不硬编码盘号）
2. 备份 config.plist（带时间戳，ESP 内 + 工作区各一份）
3. 一次只改一项
4. plutil -lint 语法校验
5. ★ 交付前过 ocvalidate（OpenCorePkg 自带，比 plutil -lint 更严格，能抓
   quirks / kext 引用 / 字段类型类错误；不通过不要交付）。两个审计脚本结尾会
   **自动调用**它（PATH 或 OpenCorePkg/Utilities 下找到就跑，找不到明确提示）
6. Reset NVRAM
7. 重启并回归测试
8. 异常则回滚备份
```

```bash
# 查 EFI 分区（不硬编码盘号）
diskutil list | grep "EFI EFI"

# 挂载
diskutil mount disk0s1

# 备份
cp /Volumes/EFI/EFI/OC/config.plist \
   /Volumes/EFI/EFI/OC/config.plist.bak-$(date +%Y%m%d-%H%M%S)

# 改配置优先用 plutil（保持 XML、改动最小）
plutil -replace 'Kernel.Quirks.DisableRtcChecksum' -bool true /Volumes/EFI/EFI/OC/config.plist

# 校验
plutil -lint /Volumes/EFI/OC/config.plist

# 落盘（FAT 有写缓存）
sync
diskutil unmount disk0s1 && diskutil mount disk0s1
```

### 铁律
1. **绝不硬编码盘号**（盘号会漂）。
2. **改前备份**，带时间戳。
3. **一次只改一项**。
4. **改完 `plutil -lint` 校验**；损坏立即回滚。
5. **写完 `sync` → 卸载 → 重挂 → 复验 sha**。
6. **改机型 / 音频 / RTC 相关项后必须 Reset NVRAM**。
7. **移动或删除 kext 必须同步改 `Enabled=false` 或删条目**。
8. **优先用 `plutil`**，不要手写替换 plist。

---

## 六、改后验证

```bash
# 机型与 board-id
sysctl -n hw.model
/usr/libexec/PlistBuddy -c "Print :PlatformInfo:Generic:SystemProductName" /Volumes/EFI/EFI/OC/config.plist

# 核显加速状态（★ 关键：确认不是 VESA 回退）
system_profiler SPDisplaysDataType | grep -iE 'Metal|Chipset|VRAM'

# 睡眠日志（确认真的睡了）
pmset -g log | grep -E "Sleep|Wake" | tail -20
pmset -g assertions
```

### 回归测试清单
- [ ] 内屏亮度可调节
- [ ] 声卡输出正常
- [ ] HDMI 外显 + 音频
- [ ] 触控板手势
- [ ] 蓝牙键鼠
- [ ] **盒盖睡眠与唤醒**（★ 笔记本高发故障点）
- [ ] Wi-Fi 自动连接
- [ ] 电池续航无异常掉电
