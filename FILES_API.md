# 按 userid 存储与检索文件

知识库根目录由环境变量 `KNOWLEDGE_DIR` 指定，未设置时沿用原路径 `/mnt/f/Workspace/wsl_py/knowledge_base`。每个用户的文件直接存入该根目录下以 userid 命名的文件夹：

```text
knowledge_base/
  alice/
    handbook.md
  bob/
    handbook.md
```

`userid` 支持字母、数字、点、下划线、`@` 和连字符，必须以字母或数字开头，最多 128 字符。支持 `.txt`、`.md`、`.html`、`.htm` 文件。

## HTTP 上传

使用 `PUT /api/v1/users/{userid}/files/{filename}`，请求体为文件原始字节，最大 20 MiB。相同用户的同名文件会被原子替换；不同用户可以使用同一文件名。

```bash
curl -X PUT 'http://localhost:8080/api/v1/users/alice/files/handbook.md' \
  -H 'Content-Type: text/markdown' \
  --data-binary '@handbook.md'
```

成功时返回 `201` 和 `userid`、`filename`、相对 `path`、`size`。该接口目前只接收 userid，没有 token 校验；userid 只是请求方声明的值，**不能把目录隔离视为身份授权**。在接入认证前，只应在受控网络中使用上传接口。

## 其他服务直接写入

外部服务将文件写到 `${KNOWLEDGE_DIR}/${userid}/${filename}`。请先写临时文件，再原子重命名为最终文件名，避免构建任务读到未写完的内容。构建脚本只扫描用户文件夹下一层的受支持文件。

## 入库与查询

上传或直接写入文件后，运行 `python rag_builder.py` 增量入库。索引键采用 `userid/filename`，Milvus 记录 `userid` 与 `doc_id`；相同文件名在不同用户之间不会冲突。`/ask` 和 `/ask_stream` 请求现在必须包含 `userid`，搜索按该字段过滤。命令行问答须传 `--userid`。

```json
{"userid": "alice", "question": "文档说了什么？", "top_k": 3}
```

旧版索引和现有向量没有 userid。请先确定旧文件所属用户，将文件移入对应用户文件夹，然后在 WSL 中显式运行 `python rag_builder.py --rebuild`。**不要在完成文件归属整理前运行重建**；重建会删除旧 Milvus 集合。新版增量构建遇到旧索引会停止并提示迁移，不会自行归属旧文件。
