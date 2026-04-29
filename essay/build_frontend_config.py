import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

api_base = os.getenv("API_BASE", "http://localhost:8080/api")
theme_default = os.getenv("THEME_DEFAULT", "dark")

content = (
    "window.APP_CONFIG = {\n"
    f"  API_BASE: \"{api_base}\",\n"
    f"  THEME_DEFAULT: \"{theme_default}\"\n"
    "};\n"
)

Path("config.js").write_text(content, encoding="utf-8")
print("config.js updated from .env")
