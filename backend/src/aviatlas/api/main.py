"""HTTP routes and transport-level validation for AviAtlas."""
from dataclasses import asdict
import json
from pathlib import Path
from uuid import UUID
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from aviatlas.api.dependencies import Services, get_services
from aviatlas.api.models import DocumentUploadResponse, ParseResponse, QuestionAnswer, QuestionRequest, DocumentSummary, DocumentListResponse, ProcessingResponse
from aviatlas.api.models import CollectionInput, CollectionSummary, CollectionListResponse, DocumentCollections
from aviatlas.api.models import AskRequest, ScopeInput, SearchDebugResponse, SearchRequest, SearchResponse, SourceAwareAnswer
from aviatlas.api.models import (
    CitedSource, ConversationCreate, ConversationDetail, ConversationListResponse, ConversationRename,
    ConversationSummary, MessageOutput, MessageRequest,
)
from aviatlas.services.models import ScopeRequest, Selection
from aviatlas.services.errors import (
    CollectionNotFound, ConversationBusy, ConversationNotFound, DuplicateDocument,
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
    CollectionNotFound: 404, ConversationNotFound: 404, ConversationBusy: 409, DocumentBusy: 409, DuplicateDocument: 409, InvalidInput: 400, DocumentNotFound: 404, DocumentNotReady: 400,
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
                    auto_process: bool = Query(default=True), collection_id: UUID | None = Query(default=None), services: Services = Depends(get_services)):
    if collection_id is not None:
        collection_service(services).get(collection_id)
    record = services.documents.upload(file.filename, file.file)
    if collection_id is not None:
        collection_service(services).replace_memberships(record.id, [collection_id])
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


def scope_request(body: ScopeInput) -> ScopeRequest:
    return ScopeRequest(document_ids=body.document_ids, collection_id=body.collection_id)

def search_service(services: Services):
    if services.search is None:
        raise HTTPException(status_code=503, detail="Search is unavailable.")
    return services.search

@app.post("/api/v1/search", response_model=SearchResponse)
def search(request: SearchRequest, services: Services = Depends(get_services)):
    return asdict(search_service(services).search(request.query, scope_request(request), request.top_k, request.method))

@app.post("/api/v1/search/debug", response_model=SearchDebugResponse)
def search_debug(request: SearchRequest, services: Services = Depends(get_services)):
    run, scope = search_service(services).inspect(request.query, scope_request(request), request.top_k, request.method)
    return {**asdict(run), "document_ids": scope.document_ids, "skipped_document_ids": scope.skipped_document_ids}

def selection_of(body) -> Selection | None:
    return body.selection and Selection(body.selection.text, body.selection.document_id, body.selection.page_number)

@app.post("/api/v1/ask", response_model=SourceAwareAnswer)
def ask(request: AskRequest, services: Services = Depends(get_services)):
    return asdict(services.qa.answer(request.question, scope_request(request), mode=request.mode,
                                     selection=selection_of(request), top_k=request.top_k))


def conversation_service(services: Services):
    if services.conversations is None:
        raise HTTPException(status_code=503, detail="Conversations are unavailable.")
    return services.conversations

def conversation_summary(record) -> ConversationSummary:
    return ConversationSummary(**asdict(record))

def message_output(message) -> MessageOutput:
    payload = message.payload
    sources = [CitedSource(**source.model_dump(), citation=n) for n, source in message.sources]
    return MessageOutput(
        message_id=message.message_id, role=message.role, content=message.content, status=message.status,
        created_at=message.created_at, mode=payload.get("mode"), selection=payload.get("selection"),
        segments=payload.get("segments") or [], sources=sources,
        invalid_citations=payload.get("invalid_citations") or [], search_query=payload.get("search_query"),
        removed_source_count=max(0, (payload.get("source_count") or 0) - len(sources)),
        skipped_document_ids=payload.get("skipped_document_ids") or [], error=payload.get("error"))

@app.post("/api/v1/conversations", response_model=ConversationSummary, status_code=201)
def create_conversation(request: ConversationCreate, services: Services = Depends(get_services)):
    return conversation_summary(conversation_service(services).create(scope_request(request), request.title))

@app.get("/api/v1/conversations", response_model=ConversationListResponse)
def list_conversations(limit: int = Query(default=100, ge=1, le=500), services: Services = Depends(get_services)):
    return {"items": [conversation_summary(c) for c in conversation_service(services).list(limit)]}

@app.get("/api/v1/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation_detail(conversation_id: UUID, services: Services = Depends(get_services)):
    conversations = conversation_service(services)
    record = conversations.get(conversation_id)
    return ConversationDetail(**asdict(record), messages=[message_output(m) for m in conversations.messages(conversation_id)])

@app.patch("/api/v1/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: UUID, request: ConversationRename, services: Services = Depends(get_services)):
    return conversation_summary(conversation_service(services).rename(conversation_id, request.title))

@app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, services: Services = Depends(get_services)):
    conversation_service(services).delete(conversation_id)
    return Response(status_code=204)


def sse_event(event) -> str:
    data = event.data
    if event.name == "started":
        body = {"user": message_output(data["user"]).model_dump(mode="json"),
                "assistant": message_output(data["assistant"]).model_dump(mode="json")}
    elif event.name == "retrieval":
        body = {**data, "sources": [CitedSource(**s.model_dump(), citation=n).model_dump(mode="json")
                                    for n, s in enumerate(data["sources"], 1)]}
    elif event.name in ("done", "error"):
        body = {**data, "message": data["message"] and message_output(data["message"]).model_dump(mode="json")}
    else:
        body = data
    return f"event: {event.name}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

@app.post("/api/v1/conversations/{conversation_id}/messages")
def post_message(conversation_id: UUID, request: MessageRequest, services: Services = Depends(get_services)):
    """Server-Sent Events: started, retrieval, delta*, then done or error."""
    turn = conversation_service(services).ask(conversation_id, request.question, mode=request.mode,
                                              selection=selection_of(request), top_k=request.top_k)

    async def stream():
        events = turn.events()
        try:
            while (event := await run_in_threadpool(next, events, None)) is not None:
                yield sse_event(event)
        finally:
            # A disconnected client stops generation; the turn saves what it has.
            turn.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def summarize(record, collection_ids=None) -> DocumentSummary:
    return DocumentSummary(
        document_id=record.id, filename=record.filename, title=record.title,
        status=record.status, uploaded_at=record.uploaded_at,
        last_updated_at=record.last_updated_at, parsed_at=record.parsed_at,
        indexed_at=record.indexed_at, page_count=record.metadata.get("page_count"),
        blocks_count=len(record.blocks), chunks_count=len(record.chunks),
        last_error=record.metadata.get("last_error"), collection_ids=collection_ids or [],
    )

@app.get("/api/v1/documents", response_model=DocumentListResponse)
def list_documents(limit: int = Query(default=20, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), collection_id: UUID | None = Query(default=None), services: Services = Depends(get_services)):
    ids = collection_service(services).document_ids(collection_id) if collection_id is not None else None
    records, total = services.documents.list(limit, offset, document_ids=ids)
    return DocumentListResponse(items=[summarize(record, services.collections.collection_ids(record.id) if services.collections else []) for record in records],
                                total=total, limit=limit, offset=offset)

@app.get("/api/v1/documents/{document_id}", response_model=DocumentSummary)
def document_detail(document_id: UUID, services: Services = Depends(get_services)):
    return summarize(services.documents.get(document_id), services.collections.collection_ids(document_id) if services.collections else [])

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


@app.get("/api/v1/documents/{document_id}/file")
def document_file(document_id: UUID, services: Services = Depends(get_services)):
    record = services.documents.get(document_id)
    if not record.filepath or not record.filepath.is_file():
        raise DocumentNotFound("Original PDF is missing. Upload it again.")
    return FileResponse(record.filepath, media_type="application/pdf",
                        filename=record.filename, content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


def collection_service(services: Services):
    if services.collections is None:
        raise HTTPException(status_code=503, detail="Collection storage is unavailable.")
    return services.collections


@app.get("/api/v1/collections", response_model=CollectionListResponse)
def list_collections(services: Services = Depends(get_services)):
    return {"items": [asdict(c) for c in collection_service(services).list()]}


@app.post("/api/v1/collections", response_model=CollectionSummary, status_code=201)
def create_collection(request: CollectionInput, services: Services = Depends(get_services)):
    return asdict(collection_service(services).create(request.name))


@app.patch("/api/v1/collections/{collection_id}", response_model=CollectionSummary)
def rename_collection(collection_id: UUID, request: CollectionInput, services: Services = Depends(get_services)):
    return asdict(collection_service(services).rename(collection_id, request.name))


@app.delete("/api/v1/collections/{collection_id}", status_code=204)
def delete_collection(collection_id: UUID, services: Services = Depends(get_services)):
    collection_service(services).delete(collection_id)
    return Response(status_code=204)


@app.get("/api/v1/documents/{document_id}/collections", response_model=DocumentCollections)
def document_collections(document_id: UUID, services: Services = Depends(get_services)):
    services.documents.get(document_id)
    return {"collection_ids": collection_service(services).collection_ids(document_id)}


@app.put("/api/v1/documents/{document_id}/collections", response_model=DocumentCollections)
def update_document_collections(document_id: UUID, request: DocumentCollections, services: Services = Depends(get_services)):
    return {"collection_ids": collection_service(services).replace_memberships(document_id, request.collection_ids)}
