# Put Plotwise online (no terminal needed)

Plotwise is a Python program, so a webpage needs a computer running it. A free Hugging Face Space does that.
You set it up once in the browser; after that anyone with the link uses it with nothing to install.

## Hugging Face Space (free)

1. Make a free account at [huggingface.co](https://huggingface.co/join).
2. Click your picture › **New Space**. Give it a name (e.g. `plotwise`), choose **Docker** › **Blank** as the SDK,
   keep the free **CPU basic** hardware, and pick **Private** or **Public**. Create it.
3. In the new Space open **Files** › **Add file** › **Upload files** and upload three files: `plotwise.zip` itself,
   plus `README.md` and `Dockerfile` from inside it (unzip it on your computer to get those two). Click **Commit**.
   No folders are needed: the Space unpacks `plotwise.zip` while it builds. `README.md` tells Hugging Face how to
   run it. **To update the Space later**, upload the newer `plotwise.zip` the same way.
4. Open **Settings** › **Variables and secrets** › **New secret**:
   - `ANTHROPIC_API_KEY`: an API key from [console.anthropic.com](https://console.anthropic.com). Claude calls made
     by everyone who uses this copy are billed to this key. Without it the Space runs the offline rules only.
   - `APP_PASSWORD` (recommended): any password. Visitors are asked for it once per browser, which keeps strangers
     from using your API key.
5. Wait until the Space says **Running** (the first build takes a few minutes). Use the direct link,
   `https://<your-user-name>-<space-name>.hf.space`, to share it: it opens Plotwise full screen. You can also
   find it under the **⋮** menu › **Embed this Space**.

Good to know:

- A free Space goes to sleep after a couple of days without visitors and wakes on the next visit, which takes about
  a minute.
- Never upload the `sessions` or `.venv` folders from the copy you run on your computer: `sessions` holds your data.
- Sessions live on the Space's disk, which is wiped when the Space restarts or is rebuilt. Download the zip or use
  **Save to your GitHub** for anything you want to keep.
- Each browser only sees its own sessions. A session's link (the `?s=` part of the address) opens it from anywhere,
  so share it only with people who may see that data.
- The AI's plot code runs inside a guard that can't open other files or start programs and never sees your API
  key, but this is a shared server: for unpublished data, run Plotwise on your own computer instead.

## Render (alternative)

Render can build the same `Dockerfile` from a GitHub repository: **New** › **Blueprint**, pick the repository
(it reads `render.yaml`), and fill in `ANTHROPIC_API_KEY` and `APP_PASSWORD` when asked. The free plan sleeps
when idle and also forgets sessions on restart.

## On your own computer

Windows: unzip, double-click `run.bat`, open http://localhost:5050. macOS / Linux: `bash run.sh`.
With Claude Code installed and logged in, no API key is needed locally.
