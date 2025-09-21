import getpass
import os

if not os.environ.get("GOOGLE_API_KEY"):
  os.environ["GOOGLE_API_KEY"] = "AIzaSyBgIL1nv7XWB6zmcUxtom26zSh-r3vcLI8"

from langchain.chat_models import init_chat_model

model = init_chat_model("gemini-2.5-flash", model_provider="google_genai")
import sys
import subprocess
from pathlib import Path

# Ensure Google API key is available (auto_fix sets it too, but keeping consistency)

repo_root = Path(__file__).parent
auto_fix_script = repo_root / "auto_fix.py"
target_file = repo_root / "samples" / "hello.py"

cmd = [sys.executable, str(auto_fix_script), str(target_file), "--apply"]
print("Running:", " ".join(cmd))
completed = subprocess.run(cmd, check=False)
sys.exit(completed.returncode)

