import argparse
import os
from pathlib import Path

from langchain.text_splitter import RecursiveCharacterTextSplitter, Language
from langchain_community.document_loaders import DirectoryLoader, TextLoader


def main():
    parser = argparse.ArgumentParser(description="Split Python code into chunks using LangChain text splitter.")
    parser.add_argument("path", nargs="?", default=str(Path.cwd()), help="Root directory containing Python files (default: current directory)")
    parser.add_argument("--chunk-size", type=int, default=2000, help="Chunk size in characters (default: 2000)")
    parser.add_argument("--chunk-overlap", type=int, default=200, help="Chunk overlap in characters (default: 200)")
    parser.add_argument("--glob", default="**/*.py", help="Glob pattern to match files (default: **/*.py)")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        raise SystemExit(f"Provided path does not exist: {root}")

    python_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.PYTHON,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    loader = DirectoryLoader(str(root), glob=args.glob, loader_cls=TextLoader, use_multithreading=True, show_progress=True)
    documents = loader.load()

    if not documents:
        print("No documents matched the provided pattern. Nothing to split.")
        return

    code_chunks = python_splitter.split_documents(documents)

    print(f"Successfully split the project into {len(code_chunks)} chunks.")
    print("\n--- Example of the first chunk: ---")
    print(code_chunks[0].page_content)


if __name__ == "__main__":
    main()
