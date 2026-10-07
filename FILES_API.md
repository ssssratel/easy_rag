# 按 userid 和 collection_name 存储与检索文件

`collection_name` 是用户下的**逻辑集合名称**；目前所有用户和逻辑集合仍共用一个 Milvus 物理集合。入库记录包含 `userid`、`collection_name`、`doc_id`，查询同时按前两个字段过滤。`collection_name` 不是 Milvus 集合名。共享的物理集合在首次入库时由 `vector_store.prepare()` 自动创建。

知识库根目录由环境变量 `KNOWLEDGE_DIR` 指定。文件目录为：

```text
knowledge_base/
  alice/
    project_a/
      handbook.md
    project_b/
      handbook.md
  bob/
    project_a/
      handbook.md
```

`userid` 支持字母、数字、点、下划线、`@` 和连字符，最多 128 字符，须以字母或数字开头。`collection_name` 仅支持英文字母、数字和下划线，最长 64 字符。文件支持 `.txt`、`.md`、`.html`、`.htm`。Milvus **物理**集合名仍只能使用英文字母、数字和下划线，须以字母或下划线开头。

## 逻辑集合创建与删除

这两个操作都要求请求头 `Authorization: Bearer <token>`。服务会通过 `JAVA_AUTH_VERIFY_URL` 向 Java 身份服务验证 token，返回的用户必须与路径中的 `userid` 一致；未配置身份服务时返回 `503`。

```bash
curl -X POST 'http://localhost:8080/api/v1/users/alice/collections/project_1' \
  -H 'Authorization: Bearer YOUR_TOKEN'
curl -X DELETE 'http://localhost:8080/api/v1/users/alice/collections/project_1' \
  -H 'Authorization: Bearer YOUR_TOKEN'
```

`POST` 创建或重新启用逻辑集合目录；已有且启用时返回 `200`，新建或重新启用时返回 `201`。空逻辑集合不会立即创建新的 Milvus 物理集合，首次入库时会自动准备共享物理集合。

`DELETE` 只删除 `userid + collection_name` 对应的向量并停用该逻辑集合，**保留上传的原文件**。停用期间查询和上传会被拒绝，增量构建也会跳过这些文件。若向量删除阶段失败，集合仍保持停用，可重试删除。重新创建后运行 `python rag_builder.py`，保留的文件会重新入库。

## HTTP 上传

使用 `PUT /api/v1/users/{userid}/collections/{collection_name}/files/{filename}`，请求体为文件原始字节，最大 20 MiB。同一用户不同集合可使用相同文件名；同一目录下同名文件会被原子替换。

```bash
curl -X PUT 'http://localhost:8080/api/v1/users/alice/collections/project_a/files/handbook.md' \
  -H 'Content-Type: text/markdown' \
  --data-binary '@handbook.md'
```

成功时返回 `201`，包含 `userid`、`collection_name`、`filename`、相对 `path` 和 `size`。上传接口目前仍未接入身份认证；客户端填写的 `userid` 不是身份凭证。接入认证前，只应在受控网络中使用。

外部服务也可将文件写入 `${KNOWLEDGE_DIR}/${userid}/${collection_name}/${filename}`。请先写临时文件，再原子重命名，避免构建任务读到未写完的内容。

## 入库与查询

上传后运行 `python rag_builder.py` 增量入库。索引键为 `userid/collection_name/filename`。普通和流式问答都必须提交 `collection_name`：

```json
{"userid": "alice", "collection_name": "project_a", "question": "文档说了什么？", "top_k": 3}
```

命令行问答使用 `--userid alice --collection-name project_a`。检索只返回同时匹配这两个字段的向量。

## 旧数据迁移

原来直接位于 `${KNOWLEDGE_DIR}/${userid}/${filename}` 的文件，扫描时会归入逻辑集合 `default`。若同名文件同时存在于旧目录和 `default` 目录，构建会报错，需先手动消除冲突。

旧索引键只有 `userid/filename` 的数据，以及此前使用 `collection_id` 字段的索引和 Milvus 向量，都需要迁移。增量构建会拒绝这些旧索引；旧向量也无法通过新的 `collection_name` 过滤条件检索或按新字段删除。确认文件归属并备份后，显式运行 `python rag_builder.py --rebuild`，把所有文件按新字段重新入库。**`--rebuild` 会删除并重建共享的 Milvus 物理集合。**
