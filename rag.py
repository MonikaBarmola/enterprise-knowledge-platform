import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

CHROMA_DIR = Path("chroma_db")
DOCS_DIR = Path("company_docs")
AUDIT_DB = Path("audit_log.db")

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
TOP_K = 5


# -------------------- INGESTION --------------------

def document_id(path):
    return hashlib.sha256(
        str(path.resolve()).encode()
    ).hexdigest()


def load_document(path):
    if path.suffix.lower() == ".pdf":
        return PyPDFLoader(str(path)).load()

    if path.suffix.lower() == ".docx":
        return Docx2txtLoader(str(path)).load()

    if path.suffix.lower() == ".txt":
        return TextLoader(
            str(path), encoding="utf-8"
        ).load()

    return []


def ingest():
    DOCS_DIR.mkdir(exist_ok=True)
    CHROMA_DIR.mkdir(exist_ok=True)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
    )

    embeddings = OpenAIEmbeddings(
        model=EMBEDDING_MODEL
    )

    chunks = []

    for path in DOCS_DIR.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
            continue

        relative = path.relative_to(DOCS_DIR)

        # Department comes from:
        # company_docs/<Department>/file
        department = (
            relative.parts[0]
            if len(relative.parts) > 1
            else "General"
        )

        docs = load_document(path)
        split_docs = splitter.split_documents(docs)

        doc_id = document_id(path)

        for index, doc in enumerate(split_docs):
            doc.metadata.update({
                "department": department,
                "source": path.name,
                "document_id": doc_id,
                "chunk_index": index,
            })

        chunks.extend(split_docs)

    if not chunks:
        print("No documents found.")
        return

    ids = [
        f"{d.metadata['document_id']}_{d.metadata['chunk_index']}"
        for d in chunks
    ]

    db = Chroma(
        collection_name="enterprise_knowledge",
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )

    db.add_documents(chunks, ids=ids)

    print(f"Indexed {len(chunks)} chunks.")


# -------------------- SECURE RETRIEVAL --------------------

class NotIngestedError(Exception):
    pass


def get_vector_store():
    return Chroma(
        collection_name="enterprise_knowledge",
        embedding_function=OpenAIEmbeddings(
            model=EMBEDDING_MODEL
        ),
        persist_directory=str(CHROMA_DIR),
    )


def has_ingested_documents():
    if not CHROMA_DIR.exists():
        return False
    db = get_vector_store()
    return db._collection.count() > 0


def retrieve(question, allowed_departments):
    if not allowed_departments:
        return []

    if not has_ingested_documents():
        raise NotIngestedError(
            "No documents ingested yet. Run `python rag.py` first."
        )

    db = get_vector_store()

    retriever = db.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": TOP_K,

            # CUSTOM SECURITY LOGIC:
            # department must belong to the user's permissions.
            "filter": {
                "department": {
                    "$in": allowed_departments
                }
            },
        },
    )

    return retriever.invoke(question)


# -------------------- ANSWER GENERATION --------------------

def answer_question(question, chunks):
    context = "\n\n".join(
        f"[Source: {d.metadata.get('source')}]\n{d.page_content}"
        for d in chunks
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an enterprise knowledge assistant.
Answer ONLY from the supplied context.
Do not use outside knowledge.
If the context is insufficient, say so.
Do not invent facts.
Mention the source filename when useful.

CONTEXT:
{context}"""
        ),
        ("human", "{question}")
    ])

    llm = ChatOpenAI(
        model=CHAT_MODEL,
        temperature=0
    )

    response = (prompt | llm).invoke({
        "context": context,
        "question": question,
    })

    sources = list(dict.fromkeys(
        d.metadata.get("source")
        for d in chunks
        if d.metadata.get("source")
    ))

    return response.content, sources


# -------------------- AUDIT LOGGING --------------------

def log_query(username, role, question, departments, sources, chunk_count):
    conn = sqlite3.connect(AUDIT_DB)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            username TEXT,
            role TEXT,
            question TEXT,
            departments TEXT,
            sources TEXT,
            retrieved_chunks INTEGER
        )
    """)

    conn.execute("""
        INSERT INTO query_audit
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        username,
        role,
        question,
        json.dumps(departments),
        json.dumps(sources),
        chunk_count,
    ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    ingest()
