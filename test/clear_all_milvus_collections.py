#!/usr/bin/env python3
"""删除当前 Milvus 数据库中的全部物理集合。

默认连接 MILVUS_URI 指定的地址；未设置时使用 http://localhost:19530。
脚本会先列出待删除集合，并要求输入 DELETE ALL 确认。传入 --yes 可跳过确认。
"""

import argparse
import os
import sys


CONFIRMATION_TEXT = "DELETE ALL"


def delete_all_collections(client, confirmed=False, input_func=input):
    """列出并删除 client 当前数据库内的所有集合，返回删除数量。"""
    collections = sorted(client.list_collections())
    if not collections:
        print("当前 Milvus 数据库中没有集合，无需删除。")
        return 0

    print("即将删除以下 Milvus 物理集合：")
    for name in collections:
        print(f"  - {name}")

    if not confirmed:
        answer = input_func(f"请输入 {CONFIRMATION_TEXT} 确认删除：").strip()
        if answer != CONFIRMATION_TEXT:
            print("确认内容不匹配，已取消。")
            return 0

    failures = []
    for name in collections:
        try:
            client.drop_collection(collection_name=name)
            print(f"已删除：{name}")
        except Exception as exc:
            failures.append((name, exc))
            print(f"删除失败：{name}: {exc}", file=sys.stderr)

    if failures:
        failed_names = ", ".join(name for name, _ in failures)
        raise RuntimeError(f"以下集合删除失败：{failed_names}")

    print(f"删除完成，共删除 {len(collections)} 个集合。")
    return len(collections)


def main():
    parser = argparse.ArgumentParser(
        description="删除当前 Milvus 数据库中的全部物理集合"
    )
    parser.add_argument(
        "--uri",
        default=os.environ.get("MILVUS_URI", "http://localhost:19530"),
        help="Milvus 地址，默认读取 MILVUS_URI",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认，直接删除",
    )
    args = parser.parse_args()

    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=args.uri)
        delete_all_collections(client, confirmed=args.yes)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
