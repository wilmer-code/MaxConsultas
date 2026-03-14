import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

buckets = sb.storage.list_buckets()
print("BUCKETS:", [b["name"] for b in buckets])