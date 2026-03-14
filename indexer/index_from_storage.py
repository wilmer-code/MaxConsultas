import os, io, hashlib, time, gc, argparse, re
from datetime import datetime
from dotenv import load_dotenv
from pypdf import PdfReader
from supabase import create_client
from openai import OpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()

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

# Buckets a indexar (si no pasas --bucket)
DEFAULT_BUCKETS = os.getenv("INDEX_BUCKETS", "loboral,fiscal,seguridad_social,boe,docs").split(",")

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


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


def get_or_create_collection(name: str):
    res = sb.table("collections").select("id").eq("name", name).execute().data
    if res:
        return res[0]["id"]
    return sb.table("collections").insert({"name": name}).execute().data[0]["id"]


def doc_chunks_count(doc_id: str) -> int:
    resp = sb.table("chunks").select("id", count="exact").eq("document_id", doc_id).limit(1).execute()
    return resp.count or 0


def upsert_document(bucket: str, path: str, collection_id: str, title: str, source: str, published_date, checksum: str):
    existing = (
        sb.table("documents")
        .select("id,checksum")
        .eq("storage_bucket", bucket)
        .eq("storage_path", path)
        .execute()
        .data
    )

    if existing:
        doc_id = existing[0]["id"]
        same_checksum = (existing[0].get("checksum") == checksum)

        if same_checksum:
            cnt = doc_chunks_count(doc_id)
            if cnt == 0:
                print("Documento existe pero sin chunks. Forzando reindex...", flush=True)
                return doc_id, False
            return doc_id, True

        # PDF cambió: borramos chunks y actualizamos doc
        sb.table("chunks").delete().eq("document_id", doc_id).execute()
        sb.table("documents").update(
            {
                "collection_id": collection_id,
                "title": title,
                "source": source,
                "published_date": published_date,
                "checksum": checksum,
            }
        ).eq("id", doc_id).execute()
        return doc_id, False

    doc = (
        sb.table("documents")
        .insert(
            {
                "collection_id": collection_id,
                "title": title,
                "source": source,
                "published_date": published_date,
                "storage_bucket": bucket,
                "storage_path": path,
                "checksum": checksum,
            }
        )
        .execute()
        .data[0]
    )
    return doc["id"], False


def download_pdf(bucket: str, path: str) -> bytes:
    # descarga por SDK (con service role)
    return sb.storage.from_(bucket).download(path)


def slug_to_title(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    base = base.replace("_", " ").replace("-", " ")
    base = re.sub(r"\s+", " ", base).strip().lower()
    return base


def list_pdf_files(bucket: str):
    """
    Lista archivos del bucket (nivel raíz). Si luego usas carpetas, te lo adapto.
    """
    items = sb.storage.from_(bucket).list()
    pdfs = []
    for it in items:
        name = it.get("name")
        if name and name.lower().endswith(".pdf"):
            pdfs.append(name)
    return sorted(pdfs)


def index_pdf_from_storage(bucket: str, path: str, collection_name: str, title: str, source: str, published_date=None):
    t0 = time.time()

    pdf_bytes = download_pdf(bucket, path)
    print(f"\n==> {bucket}/{path}", flush=True)
    print("Descargado OK. Bytes:", len(pdf_bytes), flush=True)

    checksum = sha256_bytes(pdf_bytes)

    collection_id = get_or_create_collection(collection_name)
    doc_id, already = upsert_document(bucket, path, collection_id, title, source, published_date, checksum)
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
                {
                    "document_id": doc_id,
                    "chunk_index": chunk_counter,
                    "page_start": page_num,
                    "page_end": page_num,
                    "section": None,
                    "content": chunk,
                    "embedding": vec,
                }
            )
            chunk_counter += 1

        for i in range(0, len(batch_rows), INSERT_BATCH):
            sb.table("chunks").insert(batch_rows[i:i + INSERT_BATCH]).execute()

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

    buckets = [args.bucket] if args.bucket else [b.strip() for b in DEFAULT_BUCKETS if b.strip()]

    for bucket in buckets:
        # collection_name = el mismo bucket (puedes cambiarlo si quieres)
        collection_name = bucket

        if args.file:
            files = [args.file]
        else:
            print(f"\n--- Listando PDFs de bucket: {bucket} ---", flush=True)
            files = list_pdf_files(bucket)
            print(f"Encontrados: {len(files)} PDFs", flush=True)

        for path in files:
            title = slug_to_title(path)
            index_pdf_from_storage(
                bucket=bucket,
                path=path,
                collection_name=collection_name,
                title=title,
                source=args.source,
                published_date=None,
            )


if __name__ == "__main__":
    main()