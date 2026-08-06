"""
TTS MultiModel - SSE 流式生成测试脚本
======================================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 测试 VoxCPM2 引擎的 SSE (Server-Sent Events) 流式语音生成接口
核心技术栈: Python + requests (HTTP 流式请求) + SSE 事件解析

SSE 流式生成简介:
    SSE (Server-Sent Events) 是一种 HTTP 长连接技术，允许服务器在生成音频的过程中
    实时向客户端推送进度和音频片段，而无需等待整个音频生成完成。这可以显著降低
    用户感知的等待时间，适合实时对话、语音助手等场景。

API 端点:
    POST /api/generate/streaming_sse
    Content-Type: application/x-www-form-urlencoded

请求参数 (form-data):
    - text: 要合成的文本内容
    - persona_name: 音色角色名称（如 "gf1" 等预设角色）
    - instruction: 风格指令（可为空字符串）
    - lang: 语言代码，"Auto" 表示自动检测
    - cfg_value: CFG 引导系数，控制生成质量/风格强度（默认 2.0）
    - inference_timesteps: 扩散推理步数（默认 10）
    - denoise: 是否降噪 ("true"/"false")

SSE 事件类型:
    - progress: 生成进度更新（百分比）
    - audio: 音频片段数据（base64 编码的 WAV 片段）
    - done: 生成完成信号
    - error: 生成错误信息
    - time_estimate: 预计剩余时间

依赖要求:
    pip install requests

使用方法:
    1. 启动服务端: start.bat (Windows) 或 python bin/clean_launch.py
    2. 确保已加载 VoxCPM2 引擎（或启用自动加载）
    3. 确保存在测试用的 persona 音色（如 "gf1"）
    4. 运行脚本: python scripts/test_sse.py

输出信息:
    - HTTP 响应状态码
    - 接收的总行数统计
    - 接收的音频片段数量
    - 各事件类型的计数统计
    - done/error 事件的详细内容

注意事项:
    - 脚本使用 stream=True 启用 HTTP 流式响应
    - 超时设置为 120 秒，长文本生成可能需要更长时间
    - 此脚本仅用于测试 SSE 连接和事件解析，不保存音频文件
    - 实际应用中需要解析 audio 事件的 base64 数据进行拼接播放
"""

import json

import requests

# SSE 流式生成 API 端点地址
# 默认本地服务地址，端口 7869，如端口变更需修改此处
SSE_URL = "http://127.0.0.1:7869/api/generate/streaming_sse"

# 测试请求参数
# 可根据需要修改 text、persona_name 等参数进行测试
request_data = {
    "text": "测试文本",  # 要合成的测试文本
    "persona_name": "gf1",  # 使用的音色角色名称
    "instruction": "",  # 风格指令（空字符串表示默认风格）
    "lang": "Auto",  # 语言自动检测
    "cfg_value": "2.0",  # CFG 引导系数
    "inference_timesteps": "10",  # 推理步数
    "denoise": "true",  # 启用降噪
}


def test_sse_stream():
    """
    测试 SSE 流式生成接口

    功能说明:
        向 SSE 端点发送 POST 请求，使用流式模式接收响应，逐行解析 SSE 事件，
        统计各种事件类型的数量，并打印 done/error 事件的详细内容。

    工作流程:
        1. 发送带 stream=True 的 POST 请求到 SSE 端点
        2. 使用 iter_lines() 迭代读取响应流
        3. 以 "event:" 开头的行标识事件类型，统计各类事件数量
        4. 特别处理 done 事件（生成完成）和 error 事件（生成错误）
        5. 连接结束后输出统计摘要

    统计指标:
        - 接收的总行数
        - audio 事件数量（音频片段数）
        - 各事件类型的出现次数分布

    注意事项:
        - 此函数不解析 audio 事件中的具体音频数据，仅做事件统计
        - 生产环境中需要解析 data: 行获取 base64 编码的音频数据
        - 完整的 SSE 格式还包含 "data:" 行携带事件数据
    """
    # 发送流式 POST 请求，设置 120 秒超时
    r = requests.post(SSE_URL, data=request_data, stream=True, timeout=120)

    # 打印 HTTP 响应状态码
    print(f"Status: {r.status_code}")

    # 统计变量初始化
    lines_count = 0  # 接收的总行数
    audio_count = 0  # 音频事件计数
    event_types = {}  # 事件类型统计字典

    # 迭代读取响应流中的每一行
    for line in r.iter_lines():
        if line:
            lines_count += 1
            line_str = line.decode("utf-8")
            # 识别 SSE 事件类型行（以 "event:" 开头）
            if line_str.startswith("event:"):
                etype = line_str[6:].strip()  # 提取事件类型名称
                event_types[etype] = event_types.get(etype, 0) + 1
                # 统计音频片段数量
                if etype == "audio":
                    audio_count += 1
                elif etype == "done":
                    # 生成完成事件
                    print(f">>> DONE event: {line_str}")
                elif etype == "error":
                    # 生成错误事件
                    print(f">>> ERROR event: {line_str}")

    # 输出统计结果
    print(f"\nTotal lines: {lines_count}")
    print(f"Audio events: {audio_count}")
    print(f"Event types: {json.dumps(event_types)}")


if __name__ == "__main__":
    """
    脚本入口: 直接运行时执行 SSE 流式测试
    """
    test_sse_stream()
