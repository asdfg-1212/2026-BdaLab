"""面向用户的错误分类；不回显模型服务响应正文或密钥。"""


def error_message(error):
    if isinstance(error, (RuntimeError, FileNotFoundError)):
        return str(error)
    return {
        "AuthenticationError": "模型服务认证失败，请检查 LLM_API_KEY。",
        "APIConnectionError": "无法连接模型服务，请检查 LLM_BASE_URL 和网络。",
        "APITimeoutError": "模型服务响应超时，请稍后重试。",
        "RateLimitError": "模型服务限流或额度不足，请检查账户后重试。",
        "BadRequestError": (
            "模型服务拒绝请求（HTTP 400），请核对完整 API Key、接口地址、模型名及工具调用支持；"
            "仅凭此状态无法确定具体原因。"
        ),
    }.get(type(error).__name__, f"执行异常：{type(error).__name__}，详见后端日志。")
