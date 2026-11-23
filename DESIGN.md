# Design Authority: Quiet Studio Slate

本文件为 **Glimpse（瞬译）v3.1** 客户端 UI 的视觉与交互设计规范。

## 审美方向
- **主题气质**：安静、沉稳、克制的桌面工具质感（Quiet Studio Slate — 对齐现代 Windows 11 / VS Code / OBS 深色桌面效率工具，彻底移除 `MONO` 英文术语与刺眼高饱和霓虹配色）。

## 色彩令牌（Color Tokens）
| 用途 | Token | 色值 | 说明 |
| --- | --- | --- | --- |
| 主画布背景 | `BG_CANVAS` | `#202226` | 沉稳深板岩灰（随单窗不透明度动态调节） |
| 侧栏 / 输入底 | `BG_RAIL` | `#191B1F` | 左侧导航栏与输入框底色 |
| 控件常态底色 | `BG_CONTROL` | `#2C3038` | 按键与分组底色 |
| 控件悬停态 | `BG_HOVER` | `#353A44` | 柔和悬停反馈 |
| 品牌主强调 | `ACCENT_PRIMARY` | `#3B82F6` | 钴蓝主强调（唯一主强调色） |
| 就绪 / 正常状态 | `STATUS_OK` | `#34C77B` | 翡翠绿状态标 |
| 加载 / 注意提示 | `STATUS_WARN` | `#E5A93C` | 柔和琥珀黄 |
| 报错 / 危险操作 | `STATUS_ERR` | `#E05A5A` | 克制珊瑚红 |
| 主文字 | `TEXT_PRIMARY` | `#F1F3F5` | 高可读柔白（可在外观页自定义字色与描边） |
| 次要说明 / 原文 | `TEXT_SECONDARY` | `#9CA3AF` | 原文对照与提示说明 |

## 字体系统
- **全局中文 UI 与译文**：`"Microsoft YaHei UI", "Noto Sans SC", sans-serif`
- **遥测与毫秒计时**：`"Consolas", monospace`
- **平滑描边字体控件**：`StrokedTextLabel` 基于 `QPainterPath` 绘制逐行抗锯齿外描边（0–4 px），彻底替代 `LWA_COLORKEY` 硬抠像，消除低透明度毛边与锯齿。

## 多屏与窗口稳定性规范
- **原生窗口拖拽**：全部无边框窗口通过 `windowHandle().startSystemMove()` 交由 Windows DWM 原生接管拖拽，彻底消除跨不同 DPI 副屏拖动时的卡死与闪退。
- **单窗体 WDA 透明方案**：所有浮窗保持单顶层 HWND + 缓存 `QRegion` 圆角掩码 + `setWindowOpacity` + `WDA_EXCLUDEFROMCAPTURE = 0x11`。
