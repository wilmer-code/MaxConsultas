import os, requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SR = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
ANON = os.environ["SUPABASE_ANON_KEY"].strip()

bucket="loboral"
path="GESTION_DE_RECURSOS_HUMANOS.pdf"

print("SR is JWT:", SR.count(".")==2)
print("SR prefix:", SR[:8], "len:", len(SR))
print("ANON prefix:", ANON[:8], "len:", len(ANON))

url = f"{SUPABASE_URL}/storage/v1/object/authenticated/{bucket}/{path}"
headers = {"Authorization": f"Bearer {SR}", "apikey": ANON}

r = requests.get(url, headers=headers)
print("STATUS:", r.status_code)
print("BODY:", r.text[:300])
