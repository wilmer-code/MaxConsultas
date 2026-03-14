import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

buckets = sb.storage.list_buckets()

names = []
for b in buckets:
    # según versión puede ser objeto o dict
    if hasattr(b, "name"):
        names.append(b.name)
    elif isinstance(b, dict) and "name" in b:
        names.append(b["name"])
    else:
        names.append(str(b))

print("BUCKETS:", names)
