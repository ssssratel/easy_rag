"""验证本地嵌入模型能否加载并生成向量。"""

from sentence_transformers import SentenceTransformer

# 使用本地路径加载模型
model = SentenceTransformer('/home/ratel/models/bge-m3')

# 测试编码
test_texts = ["这是一个测试句子", "Hello World"]
embeddings = model.encode(test_texts)
print(f"✅ 模型加载成功！向量维度: {embeddings.shape[1]}")
