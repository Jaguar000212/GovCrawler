from portal.db.models import Database
import traceback

try:
    db = Database({"database": {"uri": "sqlite:///portal/data/mishacrawler.db"}})
    print(db.list_campaigns())
except Exception as e:
    traceback.print_exc()
