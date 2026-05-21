import chromadb

# 你的数据库路径
DB_PATH = "/Users/liyihan/projects/xhs-rag-assistant/data/chroma_db"

# 连接
client = chromadb.PersistentClient(path=DB_PATH)

# 列出集合
print("\n===== 向量库集合 =====")
collections = client.list_collections()
if not collections:
    print("向量库为空")
    exit()

for coll in collections:
    print(f"- {coll.name}")

# 获取集合
collection = client.get_collection("xhs_notes")
print(f"\n===== 集合：xhs_notes | 总条数：{collection.count()} =====")

# 修复后的正确写法！
data = collection.get(limit=5, include=["documents", "metadatas"])

# 打印
for i in range(len(data["documents"])):
    print(f"\n--- 第 {i+1} 条 ---")
    print(f"ID: {data['ids'][i]}")          # ids 不用写在 include 里！
    print(f"元数据: {data['metadatas'][i]}")
    print(f"内容: {data['documents'][i][:500]}...")