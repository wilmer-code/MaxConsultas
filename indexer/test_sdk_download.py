import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

data = sb.storage.from_("loboral").download("GESTION_DE_RECURSOS_HUMANOS.pdf")
print("DESCARGA OK. Bytes:", len(data))
