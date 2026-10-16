"""手动验证文档导入与检索。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_settings
from app.memory.documents import import_text
from app.memory.embedder import reset_embedder
from app.memory.rag import retrieve_documents_and_format
from app.memory.store import reset_chroma_client
from app.storage.db_SQlite import init_db


async def main():
    s = load_settings()
    reset_embedder()
    reset_chroma_client()

    await init_db(s)

    content = """
    soul_mate 是一个桌面 AI 助手，搭载遐蝶人格。

    遐蝶是死荫的侍女，冥河的女儿。她天生背负死亡之触，
    无法触碰任何生命，因此总是与人保持克制的距离。

    她喜欢制作羊毛毡玩偶，喜欢一切可爱的事物，
    也喜欢看书，尤其是能让她代入的故事。
    """

    doc = await import_text(
        s,
        title="遐蝶设定",
        content=content,
        source="manual",
    )
    print(f"导入文档：id={doc.id} chunks={doc.chunk_count}")

    query = "遐蝶喜欢什么"
    text = await retrieve_documents_and_format(s, query, top_k=2)
    print(f"\n查询：{query}")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())