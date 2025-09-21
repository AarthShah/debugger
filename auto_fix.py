import os
import argparse
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types


SYSTEM_SPEC = (
    "You are a precise Python code fixer. Given a Python file, "
    "return ONLY a JSON object describing minimal, safe edits. "
    "Use 1-based line numbers from the ORIGINAL file. "
    "Each edit replaces exactly one existing line with one or more lines (multi-line allowed). "
    "Output must be raw JSON (no markdown, no code fences). "
    "Keep changes simple and focused on correctness (e.g., basic input validation, division-by-zero). "
    "Avoid refactors, new dependencies, or stylistic overhauls. "
    "Prefer the smallest fix that would make straightforward unit tests pass."
)

USER_INSTRUCTIONS = (
    "Analyze the code and identify incorrect or buggy lines (syntax or logic).\n"
    "Output a compact JSON of this shape ONLY (no extra text, no markdown):\n"
    "{\n"
    "  \"file\": \"<relative file path>\",\n"
    "  \"explanation\": \"brief reason of the changes\",\n"
    "  \"edits\": [\n"
    "    { \"line\": <int>, \"new\": \"replacement text (can contain\\n for multi-line)\" }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- Use 1-based line numbers from the original file.\n"
    "- Keep edits minimal: only lines that must change to fix correctness.\n"
    "- Maintain proper indentation in 'new' for any block context.\n"
    "- Do NOT include markdown fences, extra commentary, or the entire original code.\n"
)


def extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to extract fenced JSON
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return json.loads(m.group(1))
    # Try to extract any JSON-like object
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        return json.loads(m.group(1))
    raise ValueError("Model did not return valid JSON edits.")


def apply_edits(file_path: Path, edits: List[Dict[str, Any]], dry_run: bool) -> None:
    original = file_path.read_text(encoding="utf-8").splitlines()
    lines = original.copy()
    # Sort by line asc
    sorted_edits = sorted(edits, key=lambda e: int(e["line"]))
    offset = 0
    print(f"\nProposed changes for {file_path}:")
    for e in sorted_edits:
        line_no = int(e["line"])  # 1-based
        new_text = e["new"].split("\n")
        idx = line_no - 1 + offset
        if idx < 0 or idx >= len(lines):
            print(f"  - Skipping out-of-range line {line_no}")
            continue
        old_line = lines[idx]
        print(f"  L{line_no}: OLD: {old_line}")
        if len(new_text) == 1:
            print(f"       NEW: {new_text[0]}")
        else:
            preview = new_text[0] + (" ..." if len(new_text) > 1 else "")
            print(f"       NEW: {preview} ({len(new_text)} lines)")
        # replace single line with possibly multi-line
        lines[idx:idx + 1] = new_text
        offset += len(new_text) - 1

    if dry_run:
        print("\nDry run: no changes written. Use --apply to write edits.")
        return

    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nApplied {len(sorted_edits)} edit(s) to {file_path}.")


def build_prompt(file_path: Path) -> str:
    content = file_path.read_text(encoding="utf-8")
    relative = file_path.as_posix()
    return (
        f"File: {relative}\n\n"
        f"Code:\n```python\n{content}\n```\n\n"
        f"{USER_INSTRUCTIONS}"
    )


def run_model(model_name: str, prompt: str, timeout_s: int = 60) -> str:
    print(f"[auto-fix] Calling model '{model_name}'...")
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    # Stream and collect text to a single string
    text = []
    for chunk in client.models.generate_content_stream(model=model_name, contents=contents):
        if getattr(chunk, "text", None):
            text.append(chunk.text)
    return "".join(text)


def main():
    parser = argparse.ArgumentParser(description="Auto-fix Python files using Google GenAI (Gemini) with line-based edits.")
    parser.add_argument("target", help="Path to a Python file to fix.")
    parser.add_argument("--apply", action="store_true", help="Apply the suggested edits to the file.")
    parser.add_argument("--model", default="gemini-1.5-pro-latest", help="Gemini model to use (default: gemini-1.5-pro-latest)")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds for model calls (default: 60)")
    args = parser.parse_args()

    # Require API key via environment for safety when pushing to GitHub
    if not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit("GOOGLE_API_KEY is not set. Export it before running.")

    file_path = Path(args.target)
    if not file_path.exists():
        raise SystemExit(f"Path not found: {file_path}")

    prompt = SYSTEM_SPEC + "\n\n" + build_prompt(file_path)

    # Try primary, then fallback to a fast model
    model_sequence = [args.model, "gemini-2.5-flash"]
    last_err: Optional[Exception] = None
    for m in model_sequence:
        try:
            raw = run_model(m, prompt, timeout_s=args.timeout)
            break
        except Exception as e:
            print(f"[auto-fix] Model '{m}' failed: {e}")
            last_err = e
    else:
        raise SystemExit(f"All model attempts failed: {last_err}")

    try:
        data = extract_json(raw)
    except Exception as e:
        print("Model output not valid JSON. Full response:\n", raw)
        raise SystemExit(f"Failed to parse model output: {e}")

    edits = data.get("edits", [])
    if not isinstance(edits, list) or not edits:
        print("No edits suggested by the model.")
        return

    apply_edits(file_path, edits, dry_run=(not args.apply))


if __name__ == "__main__":
    main()
