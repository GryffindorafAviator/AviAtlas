"""AviAtlas FastAPI Application."""

import logging
import shutil
from datetime import datetime, timezone
from uuid import UUID

from dotenv import load_dotenv
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from aviatlas.api.models import DocumentUploadResponse, QuestionAnswer, QuestionRequest
from aviatlas.chunking.greedy_chunker import GreedyChunker
from aviatlas.parsing.docling_parser import DoclingParser
from aviatlas.answering.generator import AnswerGenerator, AnswerConfigurationError, AnswerGenerationError
from aviatlas.retrieval.semantic_retriever import SemanticRetriever
from aviatlas.storage.document_registry import registry

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
logger = logging.getLogger(__name__)
retrievers: dict[UUID, SemanticRetriever] = {}

# Initialize the FastAPI app
app = FastAPI(
    title="AviAtlas API",
    description="Backend API for AviAtlas - Intelligent Document Parsing and Retrieval",
    version="0.1.0",
)

# Directory to store uploaded files
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)  # Create directory if it doesn't exist


@app.get("/")
def read_root():
    """Root endpoint for health check."""
    return {"message": "AviAtlas API is running 🚀", "status": "healthy"}


@app.get("/api/v1/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/api/v1/documents/upload", response_model=DocumentUploadResponse)
def upload_document(file: UploadFile = File(...)):
    """Upload a PDF document so it can be parsed and queried later."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    temp_record = registry.register(file.filename, Path(""))
    file_extension = Path(file.filename).suffix
    saved_filename = f"{temp_record.id}{file_extension}"
    file_path = UPLOAD_DIR / saved_filename

    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(exc)}") from exc

    temp_record.filepath = file_path
    return DocumentUploadResponse(
        document_id=temp_record.id,
        filename=file.filename,
        status=temp_record.status,
        message="File uploaded successfully.",
    )


@app.post("/api/v1/documents/parse")
def parse_document(document_id: str):
    """Parse a previously uploaded PDF and chunk it for retrieval."""
    try:
        target_id = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document_id.") from exc

    record = registry.get(target_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    if not record.filepath or not record.filepath.exists():
        raise HTTPException(status_code=404, detail="Uploaded file is missing from disk.")

    record.set_status("parsing")
    retrievers.pop(target_id, None)
    try:
        parsed_doc = DoclingParser().parse(record.filepath)
        chunks = GreedyChunker(max_chars=1000).chunk(
            parsed_doc.blocks, document_id=target_id, knowledge_base_id=None,
        )
        if not chunks:
            raise ValueError("No searchable text was extracted from the PDF.")
        record.set_status("indexing")
        retriever = SemanticRetriever(chunks, parsed_doc.blocks)
        record.blocks = parsed_doc.blocks
        record.chunks = chunks
        record.title = parsed_doc.metadata.title
        record.update_metadata(**parsed_doc.metadata.model_dump())
        record.parsed_at = datetime.now(timezone.utc)
        record.set_status("indexed")
        retrievers[target_id] = retriever
    except Exception as exc:
        record.set_status("failed")
        logger.exception("Document processing failed: %s", target_id)
        raise HTTPException(status_code=500, detail="Document processing failed. Check server logs and retry parsing.") from exc

    return {
        "document_id": str(target_id),
        "status": record.status,
        "blocks_count": len(parsed_doc.blocks),
        "chunks_count": len(chunks),
        "message": "Document parsed and indexed successfully.",
    }


@app.post("/api/v1/documents/ask", response_model=QuestionAnswer)
def ask_question(request: QuestionRequest):
    """Answer a question against the content of an uploaded book."""
    record = registry.get(request.document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    if not record.chunks:
        raise HTTPException(status_code=400, detail="Document has not been indexed yet. Please parse it first.")

    query = request.question.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    retriever = retrievers.get(request.document_id)
    if record.status != "indexed" or retriever is None:
        raise HTTPException(status_code=400, detail="Document is not ready. Please parse it first.")
    try:
        results = retriever.search(query, request.top_k)
    except Exception as exc:
        logger.exception("Document retrieval failed")
        raise HTTPException(status_code=503, detail="Semantic retrieval failed. Please retry.") from exc
    if not results:
        return QuestionAnswer(
            document_id=request.document_id, question=request.question,
            answer="文档中没有可用于回答此问题的内容。", sources=[],
        )
    try:
        answer = AnswerGenerator().generate(query, results)
    except AnswerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AnswerGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return QuestionAnswer(
        document_id=request.document_id, question=request.question,
        answer=answer, sources=results, created_at=datetime.now(timezone.utc),
    )
