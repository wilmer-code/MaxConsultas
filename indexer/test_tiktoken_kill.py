import os, io
from dotenv import load_dotenv
from supabase import create_client
from pypdf import PdfReader
import tiktoken

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"].strip(), os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip())
enc = tiktoken.get_encoding("cl100k_base")

bucket="loboral"
path="RETRIBUCION_SALARIAL.pdf"

print("Descargando...", flush=True)
pdf_bytes = sb.storage.from_(bucket).download(path)
print("PDF bytes:", len(pdf_bytes), flush=True)

reader = PdfReader(io.BytesIO(pdf_bytes))
raw = reader.pages[0].extract_text() or ""
print("extract_text len:", len(raw), flush=True)

text = " ".join(raw.split()).strip()
print("clean len:", len(text), flush=True)

print("ANTES enc.encode()", flush=True)
tok = enc.encode(text)
print("DESPUES enc.encode()", flush=True)
print("tokens:", len(tok), flush=True)
