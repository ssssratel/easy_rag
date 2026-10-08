# RAG 项目 HTTP 接口文档

> 适用版本：当前 api.py 实现。接口和 WSL 部署代码已核对一致。
> 本文描述已实现的行为，示例内容不构成对实时数据库状态的承诺。

## 1. 服务入口与基本约定

服务使用 FastAPI，默认地址为 http://localhost:8080。远程客户端将 localhost 替换为服务主机地址。页面与 API 同源提供，当前未启用 CORS；跨域浏览器调用需要另外配置。

在 WSL 已安装依赖的环境中启动：

~~~bash
conda activate normal
cd /home/ratel/projects/easy_rag
python api.py --host 0.0.0.0 --port 8080
~~~

首次准备环境时执行 `pip install -r requirements-api.txt`。也可使用 `uvicorn api:app --host 0.0.0.0 --port 8080`。

| 地址 | 用途 |
|---|---|
| GET / | 登录页面 |
| GET /index.html | 登录页面别名 |
| GET /login.html | 登录页面 |
| GET /rag_web.html | 集合、文件管理和问答页面 |
| GET /docs | FastAPI Swagger 页面 |
| GET /redoc | FastAPI ReDoc 页面 |
| GET /openapi.json | 自动生成的 OpenAPI 定义 |

当前业务接口以 Request 手动解析请求体，自动生成的 OpenAPI 定义没有完整描述全部请求字段和响应；调用时请结合本文。

### 1.1 数据类型和命名限制

| 参数 | 类型 | 规则 |
|---|---|---|
| userid | string | 必须为字符串；JSON 使用 `"1"`，数字 `1` 会被拒绝。首字符为 ASCII 字母或数字，其余字符可为 ASCII 字母、数字、点、下划线、@、减号，长度 1～128；区分大小写。 |
| collection_name | string | 逻辑集合名，只能包含 ASCII 字母、数字、下划线，长度 1～64；区分大小写。 |
| filename | string | 支持 .txt、.md、.html、.htm，扩展名校验不区分大小写。UTF-8 编码后最多 240 字节；不得包含路径分隔符、控制字符或 Windows 特殊字符，不得以点开头或以点、空格结尾。 |
| collection_names | string[] | 问答集合名称数组；非空、不可重复，每个元素满足 collection_name 规则。 |
| question | string | 问题；去掉首尾空白后不能为空。 |
| top_k | integer | 1～10，默认 3；布尔值和浮点数会被拒绝。表示全部所选集合合并后的结果总数。 |

文档内容必须能按 UTF-8 读取。HTML 会提取可见文本，TXT 和 Markdown 直接读取文本。PDF、Word、图片目前不在上传支持范围内。

文件和目录位于：

~~~text
KNOWLEDGE_DIR/
  <userid>/
    <collection_name>/
      <filename>
~~~

逻辑集合名与 Milvus 物理集合名是不同概念。当前所有用户共用一个物理集合，并通过 userid、collection_name 过滤；接口不支持直接创建、删除或切换物理集合。

### 1.2 认证方式

| 操作 | 当前认证要求 |
|---|---|
| 测试登录、外部登录校验 | 按各自接口规则执行 |
| 创建/重新启用逻辑集合、删除逻辑集合、删除文件 | 用户 "1" 的有效测试 Cookie，或由 Java 服务验证的对应用户 Bearer Token |
| 查看集合、查看文件列表、上传文件、普通问答、流式问答 | 当前未校验调用者身份；请求中的 userid 是数据范围参数，不是身份凭证 |
| 页面及自动文档 | 当前无认证要求 |

Bearer 请求头格式：

~~~http
Authorization: Bearer YOUR_TOKEN
~~~

受保护的操作会逐次调用 Java 验证服务，并将验证结果与路径 userid 比对。外部登录校验成功不会建立本地 Cookie，也不会签发新 Token，后续受保护请求仍须携带 Bearer Token。

测试 Cookie 名为 `rag_demo_session`，仅允许管理用户 "1" 的集合和删除文件，有效期 12 小时；使用 HttpOnly、SameSite=Lax，HTTPS 请求下设置 Secure。签名密钥在进程启动时生成，重启服务后已有 Cookie 失效；多进程部署时不同进程之间也不共享该签名密钥。

测试登录无需凭证，上传与问答也未鉴权；当前这些入口适用于受控测试环境，正式接入需补齐身份验证。

### 1.3 错误返回

业务接口主动抛出的 HTTPException 通常返回：

~~~json
{"error": "错误原因"}
~~~

| HTTP 状态码 | 常见原因 |
|---|---|
| 200 | 成功，或上传因重名被正常拒绝；请检查业务字段。流式请求还需检查 SSE 完成或错误事件 |
| 201 | 创建或重新启用集合成功；上传并向量化成功 |
| 400 | 参数、JSON、文件名、文件内容无效，集合已停用等 |
| 401 | 缺少有效 Bearer；外部登录身份不匹配或 Token 无效 |
| 403 | Bearer 对应用户与路径 userid 不一致，或验证服务拒绝 |
| 404 | 删除的文件不存在，或访问了不存在的路由 |
| 405 | HTTP 方法不匹配 |
| 413 | 登录请求体或上传文件超限 |
| 415 | 外部登录 Content-Type 不是 application/json |
| 500 | 文件操作、模型加载、Milvus 或 DeepSeek 调用失败 |
| 503 | Java 身份验证服务未配置、不可用或返回无效响应 |

框架级错误（如 multipart 解析失败、路由错误）可能使用 `{"detail": ...}`；未捕获的异常也可能返回非 JSON 的 500。客户端应先检查 HTTP 状态和 Content-Type，再解析错误体。

## 2. 接口总览

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | /api/v1/auth/demo-login | 一键测试登录 |
| POST | /api/v1/auth/login | 外部 Token 登录校验 |
| GET | /api/v1/users/{userid}/collections | 列出已启用的逻辑集合 |
| POST | /api/v1/users/{userid}/collections/{collection_name} | 创建或重新启用逻辑集合 |
| DELETE | /api/v1/users/{userid}/collections/{collection_name} | 删除逻辑集合的原文件、向量和索引并停用 |
| GET | /api/v1/users/{userid}/collections/{collection_name}/files | 列出集合文件 |
| PUT | /api/v1/users/{userid}/collections/{collection_name}/files/{filename} | 单文件原始字节上传并向量化 |
| POST | /api/v1/users/{userid}/collections/{collection_name}/files/batch | 批量 multipart 上传并向量化 |
| DELETE | /api/v1/users/{userid}/collections/{collection_name}/files/{filename} | 删除文件、向量及索引记录 |
| POST | /ask | 普通知识问答 |
| POST | /ask_stream | SSE 流式知识问答 |

所有文件和集合路径中的 userid、collection_name、filename 都是 URL 路径参数；非 ASCII 文件名及特殊字符需要由客户端进行 URL 编码。

## 3. 登录

### 3.1 POST /api/v1/auth/demo-login

无需请求体或凭证。固定返回测试用户 "1"，并设置测试 Cookie。

成功响应：200。

~~~json
{"authenticated": true, "userid": "1"}
~~~

~~~bash
curl -i -c rag_cookies.txt -X POST http://localhost:8080/api/v1/auth/demo-login
~~~

浏览器通过该接口登录后再跳转到 /rag_web.html。读取接口 JSON 不能替代保存响应中的 Cookie。

### 3.2 POST /api/v1/auth/login

请求头：

~~~http
Content-Type: application/json
Content-Length: <请求体字节数>
~~~

Content-Length 必须存在且与实际请求体长度一致；curl 通常自动生成。请求体最多 8192 字节。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| userid | string | 是 | 要登录的用户 ID |
| token | string | 是 | 外部系统颁发的 Token；不能全为空白，最多 4096 字符 |

~~~json
{"userid": "1", "token": "YOUR_TOKEN"}
~~~

成功响应：200。

~~~json
{"authenticated": true, "userid": "1"}
~~~

~~~bash
curl -X POST http://localhost:8080/api/v1/auth/login -H 'Content-Type: application/json' -d '{"userid":"1","token":"YOUR_TOKEN"}'
~~~

可能错误：400、401、413、415、503。Java 服务未配置时，格式有效的请求返回 503。

## 4. 逻辑集合

### 4.1 GET /api/v1/users/{userid}/collections

列出该用户已启用的逻辑集合，结果按名称排序。用户不存在或没有已启用集合时返回空数组。

成功响应：200。

~~~json
{"userid": "1", "collections": ["c1", "c2"]}
~~~

~~~bash
curl http://localhost:8080/api/v1/users/1/collections
~~~

当前为未鉴权接口。可能错误：400。

### 4.2 POST /api/v1/users/{userid}/collections/{collection_name}

创建逻辑集合，或重新启用已停用集合。无需请求体，必须满足认证要求。

| 情况 | HTTP 状态 | created |
|---|---|---|
| 新建或重新启用 | 201 | true |
| 已存在且已启用 | 200 | false |

~~~json
{"userid": "1", "collection_name": "c1", "created": true}
~~~

使用测试 Cookie：

~~~bash
curl -b rag_cookies.txt -X POST http://localhost:8080/api/v1/users/1/collections/c1
~~~

使用外部 Token：

~~~bash
curl -X POST http://localhost:8080/api/v1/users/1/collections/c1 -H 'Authorization: Bearer YOUR_TOKEN'
~~~

空逻辑集合只建立目录，首次向量化时才准备共享物理集合。重新启用会清理此前删除留下的原文件、残留向量和索引记录，重新启用后为空集合。

可能错误：400、401、403、500、503。

### 4.3 DELETE /api/v1/users/{userid}/collections/{collection_name}

删除该用户、该逻辑集合的全部原文件、向量和索引记录，并停用集合。无需请求体，必须满足认证要求。

成功响应：200。

~~~json
{"userid": "1", "collection_name": "c1", "deleted": true, "files_preserved": false, "files_deleted": true}
~~~

~~~bash
curl -b rag_cookies.txt -X DELETE http://localhost:8080/api/v1/users/1/collections/c1
~~~

原文件和集合目录内的其他内容会被删除，仅保留停用标记；集合从已启用集合列表移除，后续上传、文件列表读取和问答会拒绝这个已停用集合。Milvus 共享物理集合仍存在。删除 default 时也会清理直接位于用户目录下、归属 default 的旧版文档。

接口先写停用标记，再删除向量，然后删除原文件并清理索引。向量删除失败时原文件保留、集合仍停用；文件删除失败时可能已删除部分内容，可重试 DELETE 完成清理。当前对不存在的逻辑集合也会建立停用标记并返回成功，不返回“集合不存在”的 404。

可能错误：400、401、403、500、503。

## 5. 文件

### 5.1 GET /api/v1/users/{userid}/collections/{collection_name}/files

返回集合支持的文件名，按名称排序。

成功响应：200。

~~~json
{"userid": "1", "collection_name": "c1", "files": ["心月狐-角色档案.txt"]}
~~~

~~~bash
curl http://localhost:8080/api/v1/users/1/collections/c1/files
~~~

不存在的启用目录或空集合返回 files 空数组；已停用集合返回 400。列表反映磁盘上的源文件，不能单凭列表判断文件是否已成功向量化。

当前为未鉴权接口。可能错误：400、500。

### 5.2 PUT /api/v1/users/{userid}/collections/{collection_name}/files/{filename}

兼容单文件客户端的上传接口，请求体为文件原始字节，不是 multipart 或 JSON。

请求头：

~~~http
Content-Type: application/octet-stream
Content-Length: <文件字节数>
~~~

接口强制检查 Content-Length，单文件最多 20 MiB（20971520 字节）。当前代码没有强制校验此接口的 Content-Type，但客户端应按上述格式发送。

成功响应：201；示例中的 size、chunks 随文件内容变化。

~~~json
{
  "userid": "1",
  "collection_name": "c1",
  "filename": "notes.md",
  "path": "1/c1/notes.md",
  "size": 15,
  "uploaded": true,
  "indexed": true,
  "chunks": 1
}
~~~

~~~bash
curl -X PUT http://localhost:8080/api/v1/users/1/collections/c1/files/notes.md -H 'Content-Type: application/octet-stream' --data-binary @notes.md
~~~

path 是相对于知识库根目录的路径。只有文件保存、分块、向量生成、Milvus 写入提交和本地索引更新完成后，才返回 indexed: true。

当前为未鉴权接口。可能错误：400、413、500。

### 5.3 POST /api/v1/users/{userid}/collections/{collection_name}/files/batch

页面使用的批量上传接口。Content-Type 为 multipart/form-data，同一字段名 files 重复提交多个文件；一次请求中的所有文件属于同一个逻辑集合。

| 字段 | 类型 | 必填 | 限制 |
|---|---|---|---|
| files | 文件列表 | 是 | 1～20 个；每文件最多 20 MiB；同一请求中不可包含重复文件名 |

不支持额外普通表单字段。浏览器用 FormData 时应由浏览器生成带 boundary 的 Content-Type，不能手动只设置 multipart/form-data。

~~~bash
curl -X POST http://localhost:8080/api/v1/users/1/collections/c1/files/batch -F 'files=@notes.md' -F 'files=@handbook.txt'
~~~

成功响应：201。

~~~json
{
  "userid": "1",
  "collection_name": "c1",
  "files": [
    {"filename": "notes.md", "size": 15},
    {"filename": "handbook.txt", "size": 30}
  ],
  "uploaded": true,
  "indexed": true,
  "chunks": 2
}
~~~

files 保持本次提交顺序，size 单位为字节。chunks 是本次所有文件生成的文本块总数；每个文件可能生成多个块。

后端先保存这些文件，再在一次模型 encode 调用中批量生成文本块向量，写入 Milvus 并更新索引。请求会等待全部处理完成，客户端应为本地模型首次加载预留时间。

当前为未鉴权接口。可能错误：400、413、500；超出 multipart 解析器限制时可能先收到框架级错误。

### 5.4 上传、重名拒绝和失败语义

同一 userid、collection_name、filename 表示同一文档。集合中已存在同名文件时，PUT 和批量上传均正常返回 HTTP 200，拒绝上传、拒绝重新向量化：

~~~json
{
  "userid": "1",
  "collection_name": "c1",
  "uploaded": false,
  "indexed": false,
  "code": "FILE_EXISTS",
  "message": "文件已存在，拒绝上传：notes.md",
  "existing_files": ["notes.md"]
}
~~~

- 客户端检查 uploaded、indexed 和 code 判断结果，不能把 HTTP 200 当作上传成功。上传成功仍为 HTTP 201，uploaded: true、indexed: true。
- 批量请求先检查全部文件；只要有一个与现有文件同名，就拒绝整批，并在 existing_files 返回冲突文件列表，整批不进行向量化。
- 同一批次内重复提交同一文件名仍为请求参数错误，返回 400。不同用户或不同逻辑集合下的同名文件互相独立。
- 旧版 default 文档目录中存在同名文件时也会拒绝，避免生成两份来源。
- 文件写完后原子发布，若并发请求抢先创建同名文件，后提交者也会被拒绝，不覆盖已有文件；批量请求因这类重名被拒绝时会清理本请求已保存的新文件。
- 已停用集合拒绝上传。当前上传逻辑会为不存在且未停用的集合目录自动创建目录；客户端仍建议先调用集合创建接口。
- 文件保存、Milvus 删除/插入和本地索引更新不属于同一个事务，没有整体自动回滚。
- 保存后的内容读取或向量化失败，可能留下未入库的原文件；批量请求发生其他错误时也可能已有部分文件被保存。失败响应不会返回 indexed: true。
- 修复故障后，应核对文件状态；如果原文件已保存，先通过 DELETE 文件接口删除它，再重新上传，直接重传会按同名文件被拒绝。
- 删除集合会清理其原文件；重新创建后可以再次上传相同文件名。

### 5.5 DELETE /api/v1/users/{userid}/collections/{collection_name}/files/{filename}

删除一个源文件、该文件全部文本块向量和本地索引记录。必须满足认证要求。

成功响应：200。

~~~json
{"userid": "1", "collection_name": "c1", "filename": "notes.md", "deleted": true}
~~~

~~~bash
curl -b rag_cookies.txt -X DELETE http://localhost:8080/api/v1/users/1/collections/c1/files/notes.md
~~~

执行顺序是删除并提交向量、删除源文件、清理索引。向量删除失败时源文件保留；后续步骤失败时可能已完成前面的删除操作，没有整体回滚。不存在的文件返回 404，已停用集合返回 400。

可能错误：400、401、403、404、500、503。

## 6. 知识问答

### 6.1 公共请求参数

/ask 与 /ask_stream 接收相同 JSON 请求体：

~~~json
{
  "userid": "1",
  "collection_names": ["c1", "c2"],
  "question": "心和玄翎雀关系怎么样",
  "top_k": 3
}
~~~

| 字段 | 必填 | 说明 |
|---|---|---|
| userid | 是 | 字符串用户 ID |
| collection_names | 是（或提供兼容字段） | 至少选择一个集合，不可重复 |
| question | 是 | 非空问题，首尾空白会去除 |
| top_k | 否 | 默认 3，取值 1～10 |

旧客户端可使用单个 collection_name 替代 collection_names：

~~~json
{"userid": "1", "collection_name": "c1", "question": "文档讲了什么？"}
~~~

collection_names 未提供或为 null 时，才回退使用 collection_name；否则优先使用 collection_names，空数组不会触发回退。已停用集合会使整次请求失败；名称格式有效但目录不存在的集合不会单独返回 404，其检索结果通常为空。

处理过程：用与文档相同的嵌入模型将问题编码一次，逐集合按 userid 和 collection_name 检索；使用 IP 相似度、归一化向量和 Strong 读取一致性，将结果按分数合并后取全局 top_k，再构造资料提示词调用 DeepSeek。

当前未配置最低相关度阈值；检索数量不等于文档数量，同一文件的多个文本块可能同时入选。没有命中时直接返回“未找到相关文档。”，不调用 DeepSeek。

### 6.2 POST /ask

请求头：Content-Type: application/json。

~~~bash
curl -X POST http://localhost:8080/ask -H 'Content-Type: application/json' -d '{"userid":"1","collection_names":["c1","c2"],"question":"心和玄翎雀关系怎么样","top_k":3}'
~~~

成功响应：200。

~~~json
{
  "answer": "根据资料，心和玄翎雀关系比较亲近。",
  "sources": [
    ["c2/心月狐-角色故事.txt", 5],
    ["c2/心月狐-角色故事.txt", 11]
  ]
}
~~~

| 响应字段 | 类型 | 说明 |
|---|---|---|
| answer | string | 完整答案 |
| sources | array | 每项为 [来源标签, 文本块序号]；标签通常为 集合名/文件名，序号从 1 开始，不是原文页码 |

无命中响应：

~~~json
{"answer": "未找到相关文档。", "sources": []}
~~~

当前为未鉴权接口。可能错误：400、500。

### 6.3 POST /ask_stream

使用 POST 请求提交 JSON，响应为 SSE。它不是 GET EventSource 接口；浏览器可用 fetch 加 ReadableStream 读取。

~~~bash
curl -N -X POST http://localhost:8080/ask_stream -H 'Content-Type: application/json' -d '{"userid":"1","collection_names":["c1","c2"],"question":"心和玄翎雀关系怎么样","top_k":3}'
~~~

成功开始响应时 HTTP 200，主要响应头：

~~~http
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
~~~

每个事件以 data: 开头，后面是 JSON；事件之间以空行分隔。响应中的字符集参数可能由框架补充。

~~~text
data: {"token": "根据资料，"}

data: {"token": "心和玄翎雀关系比较亲近。"}

data: {"done": true, "sources": [["c2/心月狐-角色故事.txt", 5]]}

~~~

| 事件 | 字段 | 客户端处理 |
|---|---|---|
| 文本增量 | token: string | 按顺序直接拼接；一个 token 事件可能包含多个字符，不对应固定词数 |
| 完成 | done: true, sources: array | 标记整次回答完成，并显示来源 |
| 错误 | error: string | 标记失败；已有文本可能只是部分答案 |

在请求解析或向量检索阶段失败，会直接返回 400/500 和 JSON 错误体。响应流开始后，DeepSeek 等阶段失败会在 HTTP 200 的流中发送错误事件：

~~~text
data: {"error": "DEEPSEEK_API_KEY 未配置；请在启动 api.py 的同一环境中设置"}

~~~

错误事件后不会发送 done。客户端必须检查 error 和 done，不能只依赖 HTTP 200；连接结束但未收到 done 也应视为未完整完成。

无命中时发送一个“未找到相关文档。”文本事件，再发送 done: true 和空 sources。

页面的“停止生成”通过 AbortController 取消接收请求；服务端和上游模型调用是否立即停止取决于连接及 SDK 的执行状态，不保证上游立即中断。

当前为未鉴权接口。

## 7. Java 身份服务对接

配置 JAVA_AUTH_VERIFY_URL 后，Python 向该固定地址发送 POST：

~~~json
{"token": "YOUR_TOKEN"}
~~~

请求超时为 5 秒，读取响应上限为 8192 字节。Python 不会把登录请求中的 userid 发给 Java，而是使用 Java 返回的用户 ID 核对身份。

Java 有效响应：

~~~json
{"valid": true, "userid": "1"}
~~~

Java 拒绝响应：

~~~json
{"valid": false}
~~~

valid 必须为 JSON 布尔值；有效响应中的 userid 必须为合法字符串。Java 返回 401/403 被视为拒绝，其余 HTTP 错误、网络失败、超大或格式错误的响应被视为服务不可用。

## 8. 服务配置与维护边界

| 环境变量 | 用途 | 代码默认值 |
|---|---|---|
| KNOWLEDGE_DIR | 原文件目录 | /mnt/f/Workspace/wsl_py/knowledge_base |
| EMBEDDING_MODEL | 文档及问题嵌入模型路径 | /home/ratel/models/bge-m3 |
| MILVUS_URI | Milvus 服务地址 | http://localhost:19530 |
| VECTOR_COLLECTION | 项目使用的共享物理集合名 | knowledgebase_DST |
| DEEPSEEK_API_KEY | DeepSeek 凭证 | 无；需要调用模型时必须配置 |
| JAVA_AUTH_VERIFY_URL | Java Token 验证服务地址 | 无；受保护的 Bearer 操作和外部登录需要配置 |

WSL 环境变量可覆盖默认值；本项目部署曾将 KNOWLEDGE_DIR 配为 /home/ratel/projects/easy_rag/k_dir。密钥必须存在于实际启动 API 进程的环境中，不能仅存在于另一个终端。

示例仅使用占位凭证：

~~~bash
export KNOWLEDGE_DIR='/home/ratel/projects/easy_rag/k_dir'
export DEEPSEEK_API_KEY='YOUR_DEEPSEEK_API_KEY'
python api.py --port 8080
~~~

模型和向量库在进程中有缓存，修改相关环境变量后应重启服务。文档与问题必须使用一致的模型、向量维度及预处理；更换模型或切换物理集合不会自动迁移已有向量。

没有 HTTP 健康检查、登出、独立检索结果、物理集合管理或后台异步索引任务接口。网页上传已同步入库，不需要再手动运行构建脚本；由其他程序直接写入目录的文件可用 python rag_builder.py 增量处理。--rebuild 会删除并重建当前配置的共享物理集合，属于独立维护操作。

## 9. 建议调用顺序

1. 测试客户端调用 POST /api/v1/auth/demo-login 并保存 Cookie；外部客户端准备可验证的 Bearer Token。
2. GET 用户集合列表，按需 POST 创建逻辑集合。
3. POST files/batch 上传一个或多个文件，等待 201 且 indexed: true。
4. POST /ask 或 /ask_stream，指定需要检索的逻辑集合。
5. 通过 DELETE 文件接口清理单个文档；通过 DELETE 集合接口删除整个逻辑集合的原文件、向量和索引并停用集合。

不要在删除、新建和上传失败后假定整个流程已回滚；先根据接口返回状态核对文件、向量和集合状态。重名拒绝为正常业务返回，客户端应显示 message，不应自动再次上传同名文件。
