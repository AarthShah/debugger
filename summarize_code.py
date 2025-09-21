import os
import argparse
from typing import List, Optional
from tqdm import tqdm

from langchain.text_splitter import Language, RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.chains import LLMChain


SUMMARY_PROMPT = """
You are an expert code analyst. Analyze the following Python code chunk and provide a concise summary.
Describe what the function or class does, its inputs, and its outputs.
Respond in the following format, and do not include the original code in your response:

Summary: [A short summary of the code's purpose]
Inputs: [Describe the inputs or parameters, or 'None']
Outputs: [Describe the return value, or 'None']

---
Code Chunk:
```python
{code}
```
---
Your Analysis:
"""


def load_and_split_code(directory: str, glob: str, chunk_size: int, chunk_overlap: int, limit: Optional[int] = None):
    print(f"Loading files from {directory} (pattern: {glob})...")
    loader = DirectoryLoader(directory, glob=glob, loader_cls=TextLoader)
    documents = loader.load()

    if limit is not None and limit > 0:
        documents = documents[:limit]

    print("Splitting documents into code chunks...")
    python_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.PYTHON, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return python_splitter.split_documents(documents)


def get_summarization_chain(llm):
    prompt = PromptTemplate(template=SUMMARY_PROMPT, input_variables=["code"])
    return LLMChain(llm=llm, prompt=prompt)


def maybe_redact(text: str) -> str:
    # Light redaction to avoid leaking obvious keys in summaries
    return text.replace(os.environ.get("GOOGLE_API_KEY", ""), "[REDACTED]") if os.environ.get("GOOGLE_API_KEY") else text


def main(directory: str, glob: str, chunk_size: int, chunk_overlap: int, limit: Optional[int] = None):
    os.environ["GOOGLE_API_KEY"] = "AIzaSyCCXDr-ewmrqqVLyVzJJH1KFNUsoLLl3-4"

    code_chunks = load_and_split_code(directory, glob, chunk_size, chunk_overlap)

    if not code_chunks:
        print("No chunks produced. Exiting.")
        return

    llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro-latest", temperature=0)
    summarization_chain = get_summarization_chain(llm)

    print(f"Summarizing {len(code_chunks)} code chunks with Gemini...")
    processed_docs: List[Document] = []

    for chunk in tqdm(code_chunks, desc="Summarizing Code"):
        try:
            summary = summarization_chain.run(code=chunk.page_content)
            summary = maybe_redact(summary)
            new_doc = Document(
                page_content=summary,
                metadata={
                    "source": chunk.metadata.get("source"),
                    "original_code": maybe_redact(chunk.page_content),
                },
            )
            processed_docs.append(new_doc)
        except Exception as e:
            src = chunk.metadata.get("source")
            print(f"\nError processing chunk from {src}: {e}")
            continue

    if not processed_docs:
        print("No documents were processed. Exiting.")
        return

    print("Creating embeddings and storing in FAISS vector database...")
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    vector_store = FAISS.from_documents(processed_docs, embeddings)
    vector_store.save_local("faiss_summary_index")

    print("\n✅ Successfully created and saved the summary index to 'faiss_summary_index'!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate summaries for a codebase and store them in a FAISS vector DB."
    )
    parser.add_argument("directory", help="Root directory of the codebase.")
    parser.add_argument("--glob", default="**/*.py", help="Glob pattern to find source files.")
    parser.add_argument("--chunk-size", type=int, default=3000, help="Chunk size for the text splitter (default: 3000, use higher for fewer chunks)")
    parser.add_argument(
        "--chunk-overlap", type=int, default=200, help="Chunk overlap for the text splitter (default: 200)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Optional limit on number of files to read before splitting."
    )
    parser.add_argument(
        "--api-key", type=str, default=None, help="Optional Google API key to set for this run."
    )

    args = parser.parse_args()
    main(args.directory, args.glob, args.chunk_size, args.chunk_overlap, args.limit)
