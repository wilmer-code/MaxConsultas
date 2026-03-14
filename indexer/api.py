import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client
from openai import OpenAI

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"].strip()

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
TOP_K = int(os.getenv("TOP_K", "6"))

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# CORS (solo una vez)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://maxconsulta.com",
        "https://app.maxconsulta.com",
        "https://api.maxconsulta.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskIn(BaseModel):
    question: str
    collection: str | None = None
    top_k: int | None = None

@app.get("/")
def root():
    return {"ok": True, "service": "api"}

@app.get("/health")
def health():
    return {"ok": True}

def embed_query(text: str):
    # embeddings API: input puede ser string o lista; aquí lo dejamos simple
    resp = client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding

def retrieve_chunks(query_embedding, match_count: int, collection_name: str | None):
    """
    IMPORTANTÍSIMO:
    Para evitar el error PGRST203 (overloads), llamamos SIEMPRE
    a match_chunks con la firma que incluye p_collection_name y p_storage_bucket.
    """
    payload = {
        "query_embedding": query_embedding,
        "match_count": match_count,
        "p_collection_name": collection_name,   # puede ser None
        "p_storage_bucket": None,               # forzamos firma única
    }

    res = sb.rpc("match_chunks", payload).execute()

    # Si Supabase/PostgREST devuelve error, normalmente viene en res.data/res.error
    # La lib de supabase-py a veces lanza excepción; por eso también protegemos arriba.
    return res.data or []

@app.post("/ask")
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        return {"answer": "Escribe una pregunta.", "sources": []}

    top_k = inp.top_k or TOP_K

    try:
        q_emb = embed_query(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generando embedding: {e}")

    try:
        rows = retrieve_chunks(q_emb, top_k, inp.collection)
    except Exception as e:
        # aquí es donde te salía PGRST203 / schema cache issues
        raise HTTPException(status_code=500, detail=str(e))

    # Contexto con citas
    context_blocks = []
    sources = []

    for r in rows:
        title = r.get("title") or "documento"
        p1 = r.get("page_start")
        p2 = r.get("page_end")
        content = (r.get("content") or "").strip()

        sources.append({
            "title": title,
            "page_start": p1,
            "page_end": p2,
            "similarity": float(r.get("similarity", 0) or 0),
        })

        cite = f"[{title}, pág. {p1}{'' if p1 == p2 else '-' + str(p2)}]"
        context_blocks.append(f"{cite}\n{content}")

    context = "\n\n---\n\n".join(context_blocks)

    system = (
        "Eres un asistente experto en normativa y gestión de gestoría (laboral, fiscal y seguridad social). "
        "Responde en español claro. "
        "Usa SOLO la información del CONTEXTO. "
        "Si falta información, dilo. "
        "Incluye citas al final de frases relevantes usando el formato [titulo, pág. x]."
    )

    user = f"PREGUNTA:\n{q}\n\nCONTEXTO:\n{context}"

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en chat completion: {e}")

    return {"answer": answer, "sources": sources}