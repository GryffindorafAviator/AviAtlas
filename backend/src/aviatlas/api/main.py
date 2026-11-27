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
from aviatlas.api.models import ContentBlock, DocumentContent
from aviatlas.api.models import (
    AttachCorrection, ChunkOutput, ChunkUpdate, DocumentChunks, LogDetail, LogListResponse, LogSummary,
    OrphanedCorrectionOutput,
)
from aviatlas.services.corrections import UNSET
from aviatlas.chunking.structured_chunker import CHUNKER_VERSION
from aviatlas.parsing.formats import MEDIA_TYPES
from aviatlas.services.models import ScopeRequest, Selection
from aviatlas.services.errors import (
    ChunkNotFound, CorrectionNotFound, CollectionNotFound, ConversationBusy, ConversationNotFound, DuplicateDocument,
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
    ChunkNotFound: 404, CorrectionNotFound: 404, CollectionNotFound: 404, ConversationNotFound: 404, ConversationBusy: 409, DocumentBusy: 409, DuplicateDocument: 409, InvalidInput: 400, DocumentNotFound: 404, DocumentNotReady: 400,
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


def summarize(record, collection_ids=None, pending: int = 0) -> DocumentSummary:
    return DocumentSummary(
        document_id=record.id, filename=record.filename, title=record.title,
        status=record.status, uploaded_at=record.uploaded_at,
        last_updated_at=record.last_updated_at, parsed_at=record.parsed_at,
        indexed_at=record.indexed_at, page_count=record.metadata.get("page_count"),
        blocks_count=len(record.blocks), chunks_count=len(record.chunks),
        last_error=record.metadata.get("last_error"), collection_ids=collection_ids or [],
        source_format=record.metadata.get("source_format") or "pdf",
        text_layer=record.metadata.get("text_layer"), ocr_page_count=len(record.metadata.get("ocr_pages") or []),
        needs_reprocess=record.status == "indexed" and record.metadata.get("chunker") != CHUNKER_VERSION,
        edited_chunks=sum(c.edited_text is not None for c in record.chunks),
        excluded_chunks=sum(c.excluded for c in record.chunks), pending_corrections=pending,
    )

@app.get("/api/v1/documents", response_model=DocumentListResponse)
def list_documents(limit: int = Query(default=20, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), collection_id: UUID | None = Query(default=None), services: Services = Depends(get_services)):
    ids = collection_service(services).document_ids(collection_id) if collection_id is not None else None
    records, total = services.documents.list(limit, offset, document_ids=ids)
    return DocumentListResponse(items=[summarize(record, services.collections.collection_ids(record.id) if services.collections else [],
                                                 pending_corrections(services, record.id)) for record in records],
                                total=total, limit=limit, offset=offset)

@app.get("/api/v1/documents/{document_id}", response_model=DocumentSummary)
def document_detail(document_id: UUID, services: Services = Depends(get_services)):
    return summarize(services.documents.get(document_id), services.collections.collection_ids(document_id) if services.collections else [],
                     pending_corrections(services, document_id))

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
        raise DocumentNotFound("Original file is missing. Upload it again.")
    fmt = record.metadata.get("source_format") or "pdf"
    # Only PDFs render inline (in the reader); other formats download.
    return FileResponse(record.filepath, media_type=MEDIA_TYPES.get(fmt, "application/octet-stream"),
                        filename=record.filename, content_disposition_type="inline" if fmt == "pdf" else "attachment",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/v1/documents/{document_id}/content", response_model=DocumentContent)
def document_content(document_id: UUID, services: Services = Depends(get_services)):
    """Parsed blocks in reading order, for the text reader."""
    record = services.documents.get(document_id)
    return DocumentContent(document_id=record.id, source_format=record.metadata.get("source_format") or "pdf", blocks=[
        ContentBlock(block_id=b.id, type=b.type.value, content=b.content,
                     page_number=b.provenance[0].page_number if b.provenance else None) for b in record.blocks])


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


def pending_corrections(services: Services, document_id: UUID) -> int:
    if services.corrections is None:
        return 0
    return sum(c.status == "orphaned" for c in services.corrections.repository.for_document(document_id))


def correction_service(services: Services):
    if services.corrections is None:
        raise HTTPException(status_code=503, detail="Chunk editing is unavailable.")
    return services.corrections


def chunk_output(record, position: int, chunk) -> ChunkOutput:
    blocks = {b.id: b for b in record.blocks}
    pages = [p.page_number for b in chunk.block_ids if b in blocks for p in blocks[b].provenance]
    return ChunkOutput(
        chunk_id=chunk.id, position=position, content=chunk.resolved_text, original=chunk.content,
        edited=chunk.edited_text is not None, excluded=chunk.excluded, correction_id=chunk.correction_id,
        block_ids=chunk.block_ids, page_range=(min(pages), max(pages)) if pages else None,
        section_title=chunk.section_title, kind=chunk.metadata.get("kind", "text"),
        part=chunk.metadata.get("part"), parts=chunk.metadata.get("parts"))


@app.get("/api/v1/documents/{document_id}/chunks", response_model=DocumentChunks)
def document_chunks(document_id: UUID, services: Services = Depends(get_services)):
    record, orphans = correction_service(services).overview(document_id)
    return DocumentChunks(
        document_id=record.id, status=record.status,
        chunks=[chunk_output(record, i, c) for i, c in enumerate(record.chunks, 1)],
        orphaned=[OrphanedCorrectionOutput(
            correction_id=o.correction.correction_id, original_text=o.correction.original_text, text=o.correction.text,
            excluded=o.correction.excluded, context=o.correction.context, candidate_chunk_ids=o.candidate_chunk_ids,
            updated_at=o.correction.updated_at) for o in orphans])


@app.patch("/api/v1/documents/{document_id}/chunks/{chunk_id}", response_model=ChunkOutput)
def update_chunk(document_id: UUID, chunk_id: UUID, request: ChunkUpdate, services: Services = Depends(get_services)):
    fields = request.model_fields_set
    if not fields & {"text", "excluded"} or ("excluded" in fields and request.excluded is None):
        raise HTTPException(status_code=422, detail="Send text and/or excluded.")
    chunk = correction_service(services).update(
        document_id, chunk_id, text=request.text if "text" in fields else UNSET,
        excluded=request.excluded if "excluded" in fields else UNSET)
    record = services.documents.get(document_id)
    return chunk_output(record, next(i for i, c in enumerate(record.chunks, 1) if c.id == chunk.id), chunk)


@app.post("/api/v1/documents/{document_id}/corrections/{correction_id}/attach", response_model=ChunkOutput)
def attach_correction(document_id: UUID, correction_id: UUID, request: AttachCorrection,
                      services: Services = Depends(get_services)):
    chunk = correction_service(services).attach(document_id, correction_id, request.chunk_id)
    record = services.documents.get(document_id)
    return chunk_output(record, next(i for i, c in enumerate(record.chunks, 1) if c.id == chunk.id), chunk)


@app.delete("/api/v1/documents/{document_id}/corrections/{correction_id}", status_code=204)
def dismiss_correction(document_id: UUID, correction_id: UUID, services: Services = Depends(get_services)):
    correction_service(services).dismiss(document_id, correction_id)
    return Response(status_code=204)


def log_service(services: Services):
    if services.logs is None:
        raise HTTPException(status_code=503, detail="Q&A logs are unavailable.")
    return services.logs


def log_summary(log) -> dict:
    return {"log_id": log.log_id, "created_at": log.created_at, "kind": log.kind, "question": log.question,
            "mode": log.mode, "status": log.status, "source_count": len(log.sources), "model": log.model,
            "timings_ms": log.timings_ms, "error": log.error}


@app.get("/api/v1/logs", response_model=LogListResponse)
def list_logs(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
              status: str | None = Query(default=None, pattern="^(complete|no_evidence|stopped|failed)$"),
              services: Services = Depends(get_services)):
    items, total = log_service(services).list(limit, offset, status)
    return {"items": [log_summary(log) for log in items], "total": total, "limit": limit, "offset": offset}


@app.get("/api/v1/logs/{log_id}", response_model=LogDetail)
def log_detail(log_id: UUID, services: Services = Depends(get_services)):
    log = log_service(services).get(log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Log entry not found.")
    return {**asdict(log), **log_summary(log)}


@app.delete("/api/v1/logs/{log_id}", status_code=204)
def delete_log(log_id: UUID, services: Services = Depends(get_services)):
    if not log_service(services).delete(log_id):
        raise HTTPException(status_code=404, detail="Log entry not found.")
    return Response(status_code=204)


@app.delete("/api/v1/logs", status_code=204)
def clear_logs(services: Services = Depends(get_services)):
    log_service(services).clear()
    return Response(status_code=204)
