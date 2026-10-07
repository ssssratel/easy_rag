"""通过兼容 OpenAI SDK 的接口调用 DeepSeek。"""

# Please install OpenAI SDK first: `pip3 install openai`
import os
from openai import OpenAI
import traceback


def init_by_deepseek():
    """初始化 DeepSeek API 客户端。"""
    try:
        client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    except Exception as e:
        print(f"deepseek init失败:{e}")
        return None
    return client


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
    except Exception as e:
        print(f"error:{e}")
        print(traceback.format_exc())
        return f"调用失败: {e}"


def chat_by_deepseek_stream(content, model_name='deepseek-v4-pro'):
    """流式调用 DeepSeek，yield 逐段返回"""
    try:
        print(f"deepseek 流式调用开始")
        client = init_by_deepseek()
        content = content.strip()

        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个饥荒联机版知识专家，"},
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
    except Exception as e:
        print(f"error:{e}")
        print(traceback.format_exc())
        yield f"调用失败: {e}"


if __name__ == "__main__":
    pass
