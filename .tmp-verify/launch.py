import os
import secrets

os.environ["LIFESIM_CONFIG"] = os.path.abspath(".tmp-verify/config.yaml")
os.environ.setdefault("VL1_SECRET", "local-" + secrets.token_urlsafe(32))
os.execvp("uv", ["uv", "run", "uvicorn", "app.run:app",
                 "--host", "127.0.0.1", "--port", "8747"])
