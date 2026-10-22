"""AviAtlas FastAPI Application."""

import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException

from aviatlas.api.models import DocumentUploadResponse
from aviatlas.storage.document_registry import registry

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
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a PDF document to the server.
    Returns a document_id for subsequent processing.
    """
    # 1. Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    # 2. Register the document in memory first to get a UUID
    # We use a temporary path initially, will update after saving
    temp_record = registry.register(file.filename, Path("")) 
    
    # 3. Save the file to disk using the UUID as the filename to prevent collisions
    file_extension = Path(file.filename).suffix
    saved_filename = f"{temp_record.id}{file_extension}"
    file_path = UPLOAD_DIR / saved_filename

    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # 4. Update the record with the correct filepath
    # (In a real app, we might use a database update here)
    temp_record.filepath = file_path

    return DocumentUploadResponse(
        document_id=temp_record.id,
        filename=file.filename,
        status=temp_record.status,
        message="File uploaded successfully."
    )