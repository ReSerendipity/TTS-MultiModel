/**
 * TTSApp 命名空间 - 统一的前端 API 入口
 *
 * 子命名空间：
 *   TTSApp.audio   - 音频播放器
 *   TTSApp.theme   - 主题切换
 *   TTSApp.lang    - 语言切换
 *   TTSApp.model   - 模型切换/加载
 *   TTSApp.sse     - SSE 事件流
 *   TTSApp.sidebar - 侧边栏管理
 *   TTSApp.help    - 帮助面板
 *   TTSApp.health  - 健康监控
 *   TTSApp.keyboard - 键盘快捷键
 *   TTSApp.icons   - 图标转换
 *   TTSApp.micro   - 微交互
 */
var TTSApp = TTSApp || {};

// 在各模块加载后填充子命名空间
// 各模块通过 window 暴露的对象将被引用到这里
