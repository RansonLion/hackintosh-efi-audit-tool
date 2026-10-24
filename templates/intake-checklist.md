# 笔记本黑苹果 · 采集进度表

> 用法：复制到工作区后边问边填。**支持跨会话断点续做**——
> 下次开始先读本文件，不要重复问已确认的事实。
> 拿不到答案的项填 `待实测`，并注明实测方法；**不要用猜测值填充**。

---

## Phase 0 · 环境判定

```
当前系统      : Windows / macOS / Linux / 待装
目标 macOS    : 
是否双系统    : 
EFI 可读      : 
```

## Phase 1 · 硬件事实

### 批 1 平台身份
```
笔记本型号    : 
主板 / 芯片组 : 
BIOS 版本     : 
```

### 批 2 CPU 与 GPU ★
```
CPU 型号      : 
CPU 后缀      : U / H / HK / Y / G
真实 TDP      : 
核显型号      : 
核显代次      : Gen9 / Gen9.5 / Gen11 / Gen12
核显 device-id: 
独显型号      : 
是否 Optimus  : （是否直连内屏）
当前仿冒机型  : 
```

### 批 3 存储与内存
```
NVMe 型号     : 
SATA          : 
内存容量/通道 : 
```

### 批 4 输入与输出 ★（工具看不到）
```
触控板总线    : I2C / PS2
键盘          : PS2 / USB
摄像头        : USB / 内建
屏幕接口      : eDP / LVDS
屏幕分辨率    : 
是否 mux      : 
```

### 批 5 网络与音频
```
无线网卡      : （芯片型号）
声卡 codec    : （ALCxxxx）
3.5mm 孔      : combo / 分离
```

### 批 BIOS ★（必须人工进 BIOS 目视）
```
CFG Lock      : 
DVMT Pre-Alloc: 
Secure Boot   : 
CSM / Legacy  : 
Above 4G      : 
VT-d          : 
Fast Boot     : 
```

## Phase 2 · 平台选型

```
工具建议机型  : （若用过 opcore-simplify / RapidEFI 等）
三轴复核      : 轴一（核显代次）⭕ / ❌
                轴二（TDP 档位）⭕ / ❌
                轴三（macOS 版本资格：目标 Tahoe 仅 16,1/16,2/16,4）⭕ / ❌
结论机型      : 
采纳 / 推翻   : 
理由          : 
```

## Phase 3 · SSDT 审计

| SSDT | 工具给了 | DSDT 路径存在 | 结论（保留/禁用/修改） |
|---|---|---|---|
| EC | | | |
| PLUG | | | |
| PNLF | | | |
| GPI0 / XOSI | | | |
| RTCAWAC | | | |
| dGPU `_OFF` | | | |
| HPET | | | |
| `_PRW` | | | |

## Phase 4 · 电源四件套

```
pmset    : 真睡过？Total Sleep/Wakes = ? ；挡睡断言 = ?
CFG Lock : 已解锁 / 未解锁（靠 quirk 兜底）
DVMT     : BIOS 值 = ? ；fbmem = ? ；stolenmem = ?
CPUFriend: 不使用 / 使用（向量来源 = 本机派生）
```

## Phase 5 · 外设验收

```
□ 扬声器      □ 耳机        □ 麦克风
□ 触控板      □ 键盘（含唤醒后复验）
□ 摄像头      □ USB 全口    □ 端口映射已完成
□ Wi-Fi       □ 蓝牙        □ 唤醒后自动重连
□ 电池显示    □ 离电续航
□ 亮度可调    □ 外接显示器（目标分辨率流畅度）
```

## Phase 6 · 回归

```
□ 盒盖睡眠 → 唤醒（不复现「提示重启」）
□ 无新增 gpuRestart / panic
□ 长期稳定性（建议 3–7 天观察）
```

---

## 改动记录（每次改 EFI 都记一行）

| 日期 | 改了什么 | 改前备份 | 结果 |
|---|---|---|---|
| | | | |
