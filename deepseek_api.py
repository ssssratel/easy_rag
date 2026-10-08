"""通过兼容 OpenAI SDK 的接口调用 DeepSeek。"""

# Please install OpenAI SDK first: `pip3 install openai`
import os
from openai import OpenAI


def init_by_deepseek():
    """Create a client only when this process has a configured API key."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置；请在启动 api.py 的同一环境中设置")
    return OpenAI(api_key=key, base_url="https://api.deepseek.com")


def chat_by_deepseek(content, model_name='deepseek-v4-pro'):
    """非流式调用 DeepSeek，返回完整回答字符串"""
    try:
        print(f"deepseek 调用开始")
        client = init_by_deepseek()
        content = content.strip()

        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": ""},
                {"role": "user", "content": content},
            ],
            stream=False,
            temperature=0.7)

        return completion.choices[0].message.content
    except Exception:
        raise


def chat_by_deepseek_stream(content, model_name='deepseek-v4-pro'):
    """流式调用 DeepSeek，yield 逐段返回"""
    try:
        print(f"deepseek 流式调用开始")
        client = init_by_deepseek()
        content = content.strip()

        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": ""},
                {"role": "user", "content": content},
            ],
            stream=True,
            temperature=0.7)

        for env in completion:
            if env.choices:
                msg = env.choices[0].delta
                if msg and hasattr(msg, 'content') and msg.content:
                    yield msg.content
            else:
                yield ""
    except Exception:
        raise


if __name__ == "__main__":
    pass
