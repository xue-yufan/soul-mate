"""
导出运行数据为 zip 包。

包含：
- soul_mate.db（SQLite 全量，导出前做 WAL checkpoint）
- chroma/（向量库）
- documents/（导入的文档原件，如果有）
- meta.json（导出时间、版本、schema_version）

用法：
    # 默认导出到 D:\\文件\\backup\\soul-mate\\soul-mate-<时间戳>.zip
    uv run python scripts/export_data.py

    # 指定路径
    uv run python scripts/export_data.py D:/backup/custom-name.zip
"""

from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 允许从 backend/ 下运行：把 backend/ 加到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_settings
from app.storage.db_SQlite import fetch_one

# 默认导出目录，不存在会自动创建
_DEFAULT_EXPORT_DIR = Path(r"D:\文件\backup\soul-mate")


async def _read_schema_version(settings) -> str | None:
    """从 meta 表读 schema_version。失败返回 None。"""
    try:
        row = await fetch_one(
            settings,
            "SELECT value FROM meta WHERE key = 'schema_version'",
        )
        return row["value"] if row else None
    except Exception:
        return None


async def _checkpoint_wal(db_file: Path) -> None:
    """
    把 WAL 内容合并到主 db 文件。

    WAL 模式下，最新写入可能还在 soul_mate.db-wal 里，
    直接复制主文件会丢数据。checkpoint 后主文件即完整。
    """
    import aiosqlite

    try:
        conn = await aiosqlite.connect(db_file)
        try:
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await conn.commit()
        finally:
            await conn.close()
    except Exception as e:
        print(f"  警告：WAL checkpoint 失败：{e}")


async def export_data(output_path: Path) -> None:
    """执行导出。"""
    settings = load_settings()

    data_dir = settings.paths.data_dir
    if not data_dir.exists():
        raise SystemExit(f"数据目录不存在：{data_dir}")

    schema_version = await _read_schema_version(settings)

    # 元信息
    meta = {
        "app": settings.app.name,
        "app_version": settings.app.version,
        "schema_version": schema_version,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": str(data_dir.resolve()),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. meta.json
        zf.writestr(
            "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

        # 2. SQLite：checkpoint 后只打主文件
        db_file = settings.paths.sqlite_path
        if db_file.exists():
            await _checkpoint_wal(db_file)
            zf.write(db_file, "soul_mate.db")
            print(f"  + soul_mate.db（{db_file.stat().st_size} 字节）")

        # 3. Chroma 目录
        chroma_dir = settings.paths.chroma_dir
        if chroma_dir.exists():
            count = 0
            for f in chroma_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(data_dir)
                    zf.write(f, f"data/{rel}")
                    count += 1
            if count:
                print(f"  + chroma/（{count} 个文件）")

        # 4. documents 目录
        documents_dir = settings.paths.documents_dir
        if documents_dir.exists():
            count = 0
            for f in documents_dir.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(data_dir)
                    zf.write(f, f"data/{rel}")
                    count += 1
            if count:
                print(f"  + documents/（{count} 个文件）")

    size = output_path.stat().st_size
    print(f"\n导出完成：{output_path}")
    print(f"文件大小：{size} 字节（{size / 1024:.1f} KB）")


def main() -> None:
    """入口。无参数时用默认目录 + 时间戳命名。"""
    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = _DEFAULT_EXPORT_DIR / f"soul-mate-{ts}.zip"

    print(f"导出到：{output_path}\n")
    asyncio.run(export_data(output_path))


if __name__ == "__main__":
    main()