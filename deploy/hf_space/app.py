# Hugging Face Space launcher: downloads the app from the public GitHub repo and serves it on port 7860.
# To update the live app: push to GitHub, then restart this Space (Settings > Restart).
import io, os, sys, tarfile, urllib.request, importlib, threading
spaces = importlib.import_module("spaces") if os.environ.get("SPACE_ID") else None
gpu = spaces.GPU if spaces else (lambda f: f)
@gpu
def _zerogpu_noop(): return None
URL = os.environ.get("APP_TARBALL", "https://codeload.github.com/narwanRanak/ap-invoice-to-decision/tar.gz/refs/heads/main")
DEST = "/tmp/src"
PORT = int(os.environ.get("PORT", "7860"))
os.environ.setdefault("AP_RUNTIME_DIR", "/tmp/ap-runtime")
have = os.path.exists(DEST + "/app/main.py")
tar = None if have else tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(URL, timeout=60).read()), mode="r:gz")
top = None if have else tar.getmembers()[0].name.split("/")[0]
have or tar.extractall("/tmp", filter="data")
have or os.rename("/tmp/" + top, DEST)
sys.path.insert(0, DEST)
os.chdir(DEST)
import uvicorn
import gradio as gr
from app.main import app as ap_app
threading.Thread(target=uvicorn.run, args=(ap_app,), kwargs=dict(host="0.0.0.0", port=PORT), daemon=True).start()
with gr.Blocks() as demo: gr.HTML("Invoice to Decision app is served on the main port.")
demo.launch(server_name="0.0.0.0", server_port=PORT + 1)
