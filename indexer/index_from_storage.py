import os, io, hashlib, time, gc, argparse, re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from pypdf import PdfReader
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector
import numpy as np
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()
DATABASE_URL = os.environ["DATABASE_URL"].strip()

# Raíz local donde están los PDFs (una subcarpeta por colección)
PDF_ROOT = Path(os.getenv("PDF_ROOT", "/home/dev/workspaces/MaxConsultas/data/pdfs"))

# --- Ajustes embeddings ---
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))

# --- Chunking por caracteres (estable) ---
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1800"))       # ~400-600 tokens aprox
OVERLAP_CHARS = int(os.getenv("OVERLAP_CHARS", "250"))

# --- Batches / límites (para que macOS no lo mate) ---
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "16"))
INSERT_BATCH = int(os.getenv("INSERT_BATCH", "100"))
MAX_CHARS_PER_PAGE = int(os.getenv("MAX_CHARS_PER_PAGE", "20000"))
MAX_CHUNKS_PER_PAGE = int(os.getenv("MAX_CHUNKS_PER_PAGE", "200"))

# --- Throttling (por si OpenAI o red) ---
SLEEP_BETWEEN_PAGES = float(os.getenv("SLEEP_BETWEEN_PAGES", "0.0"))
SLEEP_BETWEEN_EMBED_BATCHES = float(os.getenv("SLEEP_BETWEEN_EMBED_BATCHES", "0.0"))

# Colecciones a indexar (si no pasas --bucket). Si está vacío,
# se autodetectan las subcarpetas de PDF_ROOT.
_env_buckets = os.getenv("INDEX_BUCKETS", "").strip()
DEFAULT_BUCKETS = [b.strip() for b in _env_buckets.split(",") if b.strip()] if _env_buckets else None

client = OpenAI(api_key=OPENAI_API_KEY)


def get_conn():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    register_vector(conn)
    return conn


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_clean_text(s: str) -> str:
    s = (s or "").replace("\x00", " ")
    s = " ".join(s.split())
    return s.strip()


def chunk_text_chars(text: str):
    """
    Chunking por caracteres:
    - siempre avanza (no bucles)
    - overlap controlado
    """
    if not text:
        return []

    n = len(text)
    chunks = []
    start = 0

    # seguridad: overlap nunca puede ser >= chunk
    overlap = min(OVERLAP_CHARS, max(CHUNK_CHARS - 1, 0))

    while start < n:
        end = min(start + CHUNK_CHARS, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break

        new_start = end - overlap
        if new_start <= start:
            new_start = end
        start = new_start

        if len(chunks) >= MAX_CHUNKS_PER_PAGE:
            break

    return chunks


def embed_texts(texts):
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vectors.extend([d.embedding for d in resp.data])

        if SLEEP_BETWEEN_EMBED_BATCHES > 0:
            time.sleep(SLEEP_BETWEEN_EMBED_BATCHES)

    for v in vectors:
        if len(v) != EMBED_DIM:
            raise ValueError(f"Embedding dim {len(v)} != {EMBED_DIM}. Ajusta EMBED_DIM.")
    return vectors


def get_or_create_collection(conn, name: str):
    row = conn.execute(
        "SELECT id FROM collections WHERE name = %s", (name,)
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "INSERT INTO collections (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row["id"]


def doc_chunks_count(conn, doc_id) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE document_id = %s", (doc_id,)
    ).fetchone()
    return row["n"] or 0


def upsert_document(conn, bucket: str, path: str, collection_id, title: str, source: str, published_date, checksum: str):
    existing = conn.execute(
        "SELECT id, checksum FROM documents WHERE storage_bucket = %s AND storage_path = %s",
        (bucket, path),
    ).fetchone()

    if existing:
        doc_id = existing["id"]
        same_checksum = (existing["checksum"] == checksum)

        if same_checksum:
            cnt = doc_chunks_count(conn, doc_id)
            if cnt == 0:
                print("Documento existe pero sin chunks. Forzando reindex...", flush=True)
                return doc_id, False
            return doc_id, True

        # PDF cambió: borramos chunks y actualizamos doc
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        conn.execute(
            """
            UPDATE documents
               SET collection_id = %s, title = %s, source = %s,
                   published_date = %s, checksum = %s
             WHERE id = %s
            """,
            (collection_id, title, source, published_date, checksum, doc_id),
        )
        conn.commit()
        return doc_id, False

    row = conn.execute(
        """
        INSERT INTO documents
            (collection_id, title, source, published_date,
             storage_bucket, storage_path, checksum)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (collection_id, title, source, published_date, bucket, path, checksum),
    ).fetchone()
    conn.commit()
    return row["id"], False


def download_pdf(bucket: str, path: str) -> bytes:
    # lectura local: PDF_ROOT/<bucket>/<path>
    fpath = PDF_ROOT / bucket / path
    with open(fpath, "rb") as f:
        return f.read()


def slug_to_title(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"\s+", " ", base).strip().lower()
    return base


def list_pdf_files(bucket: str):
    """
    Lista los PDFs de la subcarpeta local PDF_ROOT/<bucket>.
    """
    folder = PDF_ROOT / bucket
    if not folder.is_dir():
        return []
    pdfs = [p.name for p in folder.iterdir() if p.suffix.lower() == ".pdf"]
    return sorted(pdfs)


def list_collections():
    """
    Autodetecta las colecciones = subcarpetas de PDF_ROOT que contienen PDFs.
    """
    if not PDF_ROOT.is_dir():
        return []
    cols = []
    for p in sorted(PDF_ROOT.iterdir()):
        if p.is_dir() and any(f.suffix.lower() == ".pdf" for f in p.iterdir()):
            cols.append(p.name)
    return cols


def index_pdf_from_storage(conn, bucket: str, path: str, collection_name: str, title: str, source: str, published_date=None):
    t0 = time.time()

    pdf_bytes = download_pdf(bucket, path)
    print(f"\n==> {bucket}/{path}", flush=True)
    print("Leído OK. Bytes:", len(pdf_bytes), flush=True)

    checksum = sha256_bytes(pdf_bytes)

    collection_id = get_or_create_collection(conn, collection_name)
    doc_id, already = upsert_document(conn, bucket, path, collection_id, title, source, published_date, checksum)
    if already:
        print("Documento ya indexado (sin cambios).", flush=True)
        return

    reader = PdfReader(io.BytesIO(pdf_bytes))
    total_pages = len(reader.pages)
    chunk_counter = 0
    inserted_total = 0

    for page_num, page in enumerate(reader.pages, start=1):
        print(f"Página {page_num}/{total_pages}...", flush=True)

        try:
            raw = (page.extract_text() or "").strip()
            print(f"  - extract_text OK (len={len(raw)})", flush=True)
        except Exception as e:
            print(f"  - extract_text ERROR: {e}", flush=True)
            continue

        text = safe_clean_text(raw)
        if not text:
            print("  - sin texto, skip", flush=True)
            continue

        if len(text) > MAX_CHARS_PER_PAGE:
            print(f"  - WARNING: demasiados chars ({len(text)}). Limito a {MAX_CHARS_PER_PAGE}", flush=True)
            text = text[:MAX_CHARS_PER_PAGE]

        chunks = chunk_text_chars(text)
        print(f"  - chunk_text OK (chunks={len(chunks)})", flush=True)
        if not chunks:
            continue

        print("  - embeddings...", flush=True)
        vectors = embed_texts(chunks)
        print("  - embeddings OK", flush=True)

        batch_rows = []
        for chunk, vec in zip(chunks, vectors):
            batch_rows.append(
                (
                    doc_id,
                    chunk_counter,
                    page_num,
                    page_num,
                    None,
                    chunk,
                    np.array(vec, dtype=np.float32),
                )
            )
            chunk_counter += 1

        for i in range(0, len(batch_rows), INSERT_BATCH):
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks
                        (document_id, chunk_index, page_start, page_end,
                         section, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    batch_rows[i:i + INSERT_BATCH],
                )
            conn.commit()

        inserted_total += len(batch_rows)
        print(f"  - insert OK (total_inserted={inserted_total})", flush=True)

        # liberar memoria
        del raw, text, chunks, vectors, batch_rows
        gc.collect()

        if SLEEP_BETWEEN_PAGES > 0:
            time.sleep(SLEEP_BETWEEN_PAGES)

    dt = time.time() - t0
    print(f"Indexado OK: {inserted_total} chunks. Tiempo: {dt:.1f}s", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default=None, help="Bucket a indexar (ej: fiscal)")
    parser.add_argument("--file", default=None, help="Archivo PDF concreto dentro del bucket (ej: IRPF.pdf)")
    parser.add_argument("--source", default="manual", help="source para documents (default: manual)")
    args = parser.parse_args()

    if args.bucket:
        buckets = [args.bucket]
    elif DEFAULT_BUCKETS:
        buckets = DEFAULT_BUCKETS
    else:
        buckets = list_collections()

    print(f"Colecciones a indexar: {buckets}", flush=True)

    conn = get_conn()
    try:
        for bucket in buckets:
            # collection_name = el nombre de la subcarpeta
            collection_name = bucket

            if args.file:
                files = [args.file]
            else:
                print(f"\n--- Listando PDFs de: {bucket} ---", flush=True)
                files = list_pdf_files(bucket)
                print(f"Encontrados: {len(files)} PDFs", flush=True)

            for path in files:
                title = slug_to_title(path)
                index_pdf_from_storage(
                    conn,
                    bucket=bucket,
                    path=path,
                    collection_name=collection_name,
                    title=title,
                    source=args.source,
                    published_date=None,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()