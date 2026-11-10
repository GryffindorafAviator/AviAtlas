"""HTTP routes and transport-level validation for AviAtlas."""
from dataclasses import asdict
from pathlib import Path
from uuid import UUID
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.models import DocumentUploadResponse, ParseResponse, QuestionAnswer, QuestionRequest, DocumentSummary, DocumentListResponse, ProcessingResponse
from aviatlas.services.errors import (
    DocumentBusy, ServiceError, InvalidInput, DocumentNotFound, DocumentNotReady,
    DocumentProcessingFailed, RetrievalUnavailable, GenerationUnavailable, GenerationFailed,
)

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
app = FastAPI(
    title="AviAtlas API",
    description="Backend API for AviAtlas - Intelligent Document Parsing and Retrieval",
    version="0.1.0",
)

_ERROR_STATUS = {
    DocumentBusy: 409, InvalidInput: 400, DocumentNotFound: 404, DocumentNotReady: 400,
    DocumentProcessingFailed: 500, RetrievalUnavailable: 503,
    GenerationUnavailable: 503, GenerationFailed: 502,
}

@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=_ERROR_STATUS.get(type(exc), 500), content={"detail": str(exc)})

@app.get("/")
def read_root():
    return {"message": "AviAtlas API is running 🚀", "status": "healthy"}

@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/v1/documents/upload", response_model=DocumentUploadResponse)
def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...),
                    auto_process: bool = Query(default=True), services: Services = Depends(get_services)):
    record = services.documents.upload(file.filename, file.file)
    if auto_process:
        record = services.documents.enqueue(record.id)
        background_tasks.add_task(services.documents.process_queued, record.id)
    return DocumentUploadResponse(
        document_id=record.id, filename=record.filename, status=record.status,
        message="File uploaded and queued for processing." if auto_process else "File uploaded successfully.",
    )

@app.post("/api/v1/documents/parse", response_model=ParseResponse)
def parse_document(document_id: str, services: Services = Depends(get_services)):
    try:
        target_id = UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document_id.") from exc
    return asdict(services.documents.parse(target_id))

@app.post("/api/v1/documents/ask", response_model=QuestionAnswer)
def ask_question(request: QuestionRequest, services: Services = Depends(get_services)):
    return asdict(services.qa.ask(request.document_id, request.question, request.top_k))


def summarize(record) -> DocumentSummary:
    return DocumentSummary(
        document_id=record.id, filename=record.filename, title=record.title,
        status=record.status, uploaded_at=record.uploaded_at,
        last_updated_at=record.last_updated_at, parsed_at=record.parsed_at,
        indexed_at=record.indexed_at, page_count=record.metadata.get("page_count"),
        blocks_count=len(record.blocks), chunks_count=len(record.chunks),
        last_error=record.metadata.get("last_error"),
    )

@app.get("/api/v1/documents", response_model=DocumentListResponse)
def list_documents(limit: int = Query(default=20, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), services: Services = Depends(get_services)):
    records, total = services.documents.list(limit, offset)
    return DocumentListResponse(items=[summarize(record) for record in records],
                                total=total, limit=limit, offset=offset)

@app.get("/api/v1/documents/{document_id}", response_model=DocumentSummary)
def document_detail(document_id: UUID, services: Services = Depends(get_services)):
    return summarize(services.documents.get(document_id))

@app.post("/api/v1/documents/{document_id}/process", response_model=ProcessingResponse, status_code=202)
def process_document(document_id: UUID, background_tasks: BackgroundTasks,
                     services: Services = Depends(get_services)):
    record = services.documents.enqueue(document_id)
    background_tasks.add_task(services.documents.process_queued, document_id)
    return ProcessingResponse(document_id=document_id, status=record.status)

@app.post("/api/v1/documents/{document_id}/retry", response_model=ProcessingResponse, status_code=202)
def retry_document(document_id: UUID, background_tasks: BackgroundTasks,
                   services: Services = Depends(get_services)):
    record = services.documents.enqueue(document_id, retry_only=True)
    background_tasks.add_task(services.documents.process_queued, document_id)
    return ProcessingResponse(document_id=document_id, status=record.status)

@app.delete("/api/v1/documents/{document_id}", status_code=204)
def delete_document(document_id: UUID, services: Services = Depends(get_services)):
    services.documents.delete(document_id)
    return Response(status_code=204)
