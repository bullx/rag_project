"""
CLI Ingestion Script
Usage:
  python scripts/ingest_cli.py path/to/file.pdf
  python scripts/ingest_cli.py https://example.com/article
  python scripts/ingest_cli.py ./data/my_docs/   # ingest entire directory
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from backend.logging_config import setup_logging
setup_logging()

from backend.ingestion import ingest_source, ingest_file, get_pdf_ingest_message
from backend.vector_store import add_documents, collection_stats


def ingest(source: str):
    print(f"\n📥 Ingesting: {source}")
    docs = ingest_source(source)
    count = add_documents(docs)
    if source.lower().endswith(".pdf"):
        print(f"   {get_pdf_ingest_message(source, count)}")
    else:
        print(f"   ✅ Added {count} chunks from {source}")
    return count


def ingest_directory(dir_path: str):
    supported = {".pdf", ".docx", ".doc", ".csv", ".json"}
    files = [f for f in Path(dir_path).rglob("*") if f.suffix.lower() in supported]
    print(f"\n📂 Found {len(files)} files in {dir_path}")
    total = 0
    for f in files:
        try:
            total += ingest(str(f))
        except Exception as e:
            print(f"   ⚠️ Skipped {f.name}: {e}")
    return total


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ingest_cli.py <file_or_url_or_directory>")
        sys.exit(1)

    source = sys.argv[1]
    if os.path.isdir(source):
        total = ingest_directory(source)
    else:
        total = ingest(source)

    stats = collection_stats()
    print(f"\n📊 Knowledge base now has {stats['total_chunks']} chunks across {stats['total_sources']} sources.")
