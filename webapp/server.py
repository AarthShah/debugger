import os
import sys
from pathlib import Path
from typing import List, Dict, Any

from flask import Flask, request, jsonify, send_from_directory

# Add repo root to sys.path so we can import auto_fix when running from webapp/
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure run endpoint uses the workspace venv python by default
VENV_DEFAULT = REPO_ROOT / ".venv" / "bin" / "python"
if os.environ.get("VENV_PY") is None and VENV_DEFAULT.exists():
    os.environ["VENV_PY"] = str(VENV_DEFAULT)

from auto_fix import build_prompt, run_model, extract_json, apply_edits

# Model default (no forced clamping)
FAST_MODEL = os.environ.get("FAST_MODEL", "gemini-2.5-pro")


def clamp_model_timeout(model_in: str | None, timeout_in: int | None) -> tuple[str, int]:
    # Respect requested model; fallback to FAST_MODEL only if not provided
    model = model_in or FAST_MODEL
    # Pass through timeout with a simple sane floor of 1s
    try:
        t = int(timeout_in) if timeout_in is not None else 60
    except Exception:
        t = 60
    return model, max(1, t)

app = Flask(__name__, static_folder="static", static_url_path="", template_folder="static")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    code = data.get("code", "")
    filename = data.get("filename", "snippet.py")
    model = data.get("model", FAST_MODEL)
    timeout = int(data.get("timeout", 60))
    model, timeout = clamp_model_timeout(model, timeout)

    if not os.environ.get("GOOGLE_API_KEY"):
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY is not set on the server"}), 500
    tmp = Path("/tmp/web_snippet.py")
    tmp.write_text(code, encoding="utf-8")

    prompt = build_prompt(tmp)
    spec = (
        "You are a precise code fixer. Return ONLY JSON edits. "
        "Keep changes minimal and focused. Prefer correctness over refactors. "
        "Preserve existing behavior unless a clear bug is identified.\n\n"
    )
    try:
        raw = run_model(model, spec + prompt, timeout_s=timeout)
        edits_json = extract_json(raw)
        return jsonify({"ok": True, "result": edits_json})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/apply", methods=["POST"])
def apply():
    data = request.get_json(force=True)
    code = data.get("code", "")
    edits = data.get("edits", [])
    tmp = Path("/tmp/web_snippet_apply.py")
    tmp.write_text(code, encoding="utf-8")
    try:
        apply_edits(tmp, edits, dry_run=False)
        new_code = tmp.read_text(encoding="utf-8")
        return jsonify({"ok": True, "code": new_code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/run", methods=["POST"])
def run_py():
    import subprocess, tempfile, textwrap
    data = request.get_json(force=True)
    code = data.get("code", "")
    timeout = data.get("timeout")
    # Basic guard: limit size
    if len(code) > 200_000:
        return jsonify({"ok": False, "error": "Code too large."}), 400
    # Write to a temp file and run with the venv python
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name
        py = os.environ.get("VENV_PY", sys.executable)
        run_kwargs = {"capture_output": True, "text": True}
        try:
            t = int(timeout) if timeout is not None else None
        except Exception:
            t = None
        if t and t > 0:
            run_kwargs["timeout"] = t
        proc = subprocess.run([py, tmp_path], **run_kwargs)
        return jsonify({
            "ok": True,
            "exitCode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": f"Execution timed out after {timeout}s"}), 408
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route("/api/crosscheck", methods=["POST"])
def crosscheck():
    data = request.get_json(force=True)
    code = data.get("code", "")
    model = data.get("model", FAST_MODEL)
    timeout = int(data.get("timeout", 60))
    model, timeout = clamp_model_timeout(model, timeout)
    # Ensure API key is present (align with /api/analyze behavior)
    if not os.environ.get("GOOGLE_API_KEY"):
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY is not set on the server"}), 500

    # Prompt: Ask for STRICT JSON with overall status and per-test pass/fail, NO CODE.
    prompt = (
        "You are a senior Python reviewer. Given the following Python code, cross-check its correctness, "
        "identify edge cases, and mentally design 3-7 meaningful unit tests. Infer whether the code would PASS or FAIL "
        "each test based on static reasoning. DO NOT output any test code or code snippets.\n\n"
        "Return ONLY a compact JSON object with this exact shape (no extra text, no markdown fences):\n"
        "{\n"
        "  \"overall\": \"pass|fail|mixed\",\n"
        "  \"summary\": \"short overall assessment\",\n"
        "  \"tests\": [\n"
        "    { \"name\": \"brief test name\", \"description\": \"what it checks\", \"status\": \"pass|fail\", \"reason\": \"why\" }\n"
        "  ]\n"
        "}\n\n"
        "Constraints: Do not include any fields other than those shown. Do not include any code or stack traces.\n\n"
        "Code (for analysis):\n```python\n" + code + "\n```\n"
    )
    try:
        raw = run_model(model, prompt, timeout_s=timeout)
        data = extract_json(raw)
        # Sanitize to ensure no code is leaked
        overall = str(data.get("overall", "mixed")).lower()
        if overall not in {"pass", "fail", "mixed"}:
            overall = "mixed"
        summary = str(data.get("summary", ""))[:5000]
        tests_in = data.get("tests", [])
        tests_out = []
        if isinstance(tests_in, list):
            for t in tests_in[:20]:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name", "Unnamed test"))[:200]
                desc = str(t.get("description", ""))[:500]
                status = str(t.get("status", "fail")).lower()
                status = status if status in {"pass", "fail"} else "fail"
                reason = str(t.get("reason", ""))[:500]
                tests_out.append({
                    "name": name,
                    "description": desc,
                    "status": status,
                    "reason": reason,
                })
        # Compute counts
        passed = sum(1 for t in tests_out if t["status"] == "pass")
        failed = sum(1 for t in tests_out if t["status"] == "fail")
        return jsonify({
            "ok": True,
            "overall": overall,
            "summary": summary,
            "tests": tests_out,
            "counts": {"pass": passed, "fail": failed}
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/fix_from_crosscheck", methods=["POST"])
def fix_from_crosscheck():
    data = request.get_json(force=True)
    code = data.get("code", "")
    cross = data.get("crosscheck", {})
    model = data.get("model", FAST_MODEL)
    timeout = int(data.get("timeout", 60))
    model, timeout = clamp_model_timeout(model, timeout)

    if not os.environ.get("GOOGLE_API_KEY"):
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY is not set on the server"}), 500

    # Compose a precise instruction to fix code based on cross-check findings
    # Reuse the auto-fix JSON format for edits
    cross_summary_lines = []
    try:
        overall = str(cross.get("overall", "mixed"))
        summary = str(cross.get("summary", ""))
        counts = cross.get("counts", {}) or {}
        tests = cross.get("tests", []) or []
        cross_summary_lines.append(f"overall: {overall}")
        cross_summary_lines.append(f"summary: {summary}")
        cross_summary_lines.append(f"counts: pass={counts.get('pass',0)}, fail={counts.get('fail',0)}")
        for t in tests[:20]:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", ""))
            desc = str(t.get("description", ""))
            status = str(t.get("status", ""))
            reason = str(t.get("reason", ""))
            cross_summary_lines.append(f"- {name} | {status} | {desc} | reason: {reason}")
    except Exception:
        # If structure unexpected, just stringify
        cross_summary_lines = [str(cross)[:4000]]

    instruction = (
        "You are a precise code fixer. Modify the given Python code minimally so that it satisfies the "
        "failing tests described in the cross-check report. Preserve existing correct behavior. Return ONLY the JSON edits in this exact shape (no code blocks, no extra text):\n"
        "{\n  \"file\": \"snippet.py\",\n  \"explanation\": \"brief reason\",\n  \"edits\": [ { \"line\": <int>, \"new\": \"replacement text (can contain\\n)\" } ]\n}\n"
        "Rules: Use 1-based line numbers of the original code. Keep edits minimal. Maintain proper indentation in 'new'.\n\n"
        "Cross-check report (no code shown):\n" + "\n".join(cross_summary_lines) + "\n\n"
        "Code to fix:\n```python\n" + code + "\n```\n"
    )

    try:
        raw = run_model(model, instruction, timeout_s=timeout)
        edits_json = extract_json(raw)
        # Apply edits to a temp file and return updated code
        tmp = Path("/tmp/web_snippet_fix.py")
        tmp.write_text(code, encoding="utf-8")
        edits = edits_json.get("edits", [])
        if isinstance(edits, list) and edits:
            apply_edits(tmp, edits, dry_run=False)
            new_code = tmp.read_text(encoding="utf-8")
        else:
            new_code = code
        return jsonify({"ok": True, "edits": edits_json, "code": new_code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# Vision-based analyze endpoint
@app.route("/api/vision_analyze", methods=["POST"])
def vision_analyze():
    try:
        payload = request.get_json(force=True)
    except Exception:
        # If multipart/form-data, fall back to files/fields
        payload = None

    code = ""
    prompt = ""
    image_b64 = None
    image_url = None
    model_in = FAST_MODEL
    timeout_in = 60

    if payload:
        code = payload.get("code", "")
        prompt = payload.get("prompt", "")
        image_b64 = payload.get("imageBase64")
        image_url = payload.get("imageUrl")
        model_in = payload.get("model", FAST_MODEL)
        try:
            timeout_in = int(payload.get("timeout", 60))
        except Exception:
            timeout_in = 60
    else:
        # Support multipart form
        code = request.form.get("code", "")
        prompt = request.form.get("prompt", "")
        model_in = request.form.get("model", FAST_MODEL)
        try:
            timeout_in = int(request.form.get("timeout", "60"))
        except Exception:
            timeout_in = 60
        file = request.files.get("image")
        if file:
            import base64
            image_b64 = base64.b64encode(file.read()).decode("ascii")

    model, timeout = clamp_model_timeout(model_in, timeout_in)

    # Prepare multimodal content for Gemini via google-generativeai
    if not os.environ.get("GOOGLE_API_KEY"):
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY is not set on the server"}), 500

    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        vis_model = genai.GenerativeModel(model)

        parts = []
        system = (
            "You are a precise code fixer with vision. Compare the intended UI/function from the image "
            "and the user's prompt against the given Python code. Identify minimal edits to correct the code "
            "so that it matches the behavior/appearance implied by the image + prompt. Return ONLY JSON edits "
            "using this shape: {\n  \"file\": \"snippet.py\",\n  \"explanation\": \"brief\",\n  \"edits\": [ { \"line\": <int>, \"new\": \"...\" } ]\n}\n."
        )
        parts.append({"text": system + "\n\nUser prompt:\n" + (prompt or "")})
        if image_b64:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": image_b64,
                }
            })
        elif image_url:
            parts.append({"image_url": image_url})

        parts.append({"text": "\n\nCode to fix:\n```python\n" + code + "\n```"})

        resp = vis_model.generate_content(parts)
        # Extract text safely
        txt = getattr(resp, "text", None) or getattr(resp, "candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not txt and hasattr(resp, "prompt_feedback"):
            txt = str(resp)

        edits_json = extract_json(txt)

        # Apply to temp and return updated code
        tmp = Path("/tmp/web_snippet_vision.py")
        tmp.write_text(code, encoding="utf-8")
        edits = edits_json.get("edits", []) if isinstance(edits_json, dict) else []
        if isinstance(edits, list) and edits:
            apply_edits(tmp, edits, dry_run=False)
            new_code = tmp.read_text(encoding="utf-8")
        else:
            new_code = code

        return jsonify({"ok": True, "edits": edits_json, "code": new_code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5174"))
    app.run(host="0.0.0.0", port=port, debug=True)
