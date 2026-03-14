import os
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

load_dotenv()

sb = create_client(
    os.environ["SUPABASE_URL"].strip(),
    os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip(),
)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

def embed_query(q: str):
    r = client.embeddings.create(model=EMBED_MODEL, input=q)
    return r.data[0].embedding

def retrieve(q: str, k: int = 8, collection: str | None = None):
    v = embed_query(q)

    if collection:
        resp = sb.rpc("match_chunks_in_collection", {
            "query_embedding": v,
            "match_count": k,
            "p_collection_name": collection,
        }).execute()
    else:
        resp = sb.rpc("match_chunks", {
            "query_embedding": v,
            "match_count": k,
        }).execute()

    return resp.data or []

def answer(q: str, collection: str | None = None):
    rows = retrieve(q, k=8, collection=collection)

    context_lines = []
    for r in rows:
        # si usas match_chunks_in_collection tendrá title/bucket/path
        title = r.get("title", "")
        bucket = r.get("storage_bucket", "")
        path = r.get("storage_path", "")
        page = f'{r.get("page_start")}'

        prefix = f"[{title} | {bucket}/{path} | pág {page}]".strip()
        context_lines.append(prefix + "\n" + r["content"])

    context = "\n\n---\n\n".join(context_lines)

    system = (
        "Eres un asistente experto en gestoría (España). "
        "Responde SOLO con la información del CONTEXTO. "
        "Si no está en el contexto, di: 'no lo tengo en la documentación cargada'. "
        "Cita la fuente indicando: documento y página."
    )

    msg = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"CONTEXTO:\n{context}\n\nPREGUNTA:\n{q}"},
    ]

    resp = client.chat.completions.create(
        model=os.getenv("CHAT_MODEL", "gpt-4.1-mini"),
        messages=msg,
        temperature=0.2,
    )
    return resp.choices[0].message.content

if __name__ == "__main__":
    q = "¿Qué es la retribución salarial y qué elementos la componen?"
    print(answer(q, collection="laboral"))