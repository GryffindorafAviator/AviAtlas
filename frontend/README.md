# AviAtlas frontend

Local MVP: PDF upload and library, processing status, PDF.js reader, document Q&A and source page navigation.

Start the backend in a separate terminal (from the repository root):

```sh
cd backend
source .venv/bin/activate
uvicorn aviatlas.api.main:app --reload
```

Then start the frontend (Node.js 20+):

```sh
cd frontend
npm ci
npm run dev
```

Open the Local URL printed by Vite (normally http://127.0.0.1:5173). `/api` requests are proxied to FastAPI at port 8000. Configure the model provider in `backend/.env`; no provider credentials belong in the frontend.

```sh
npm run build
npm test
```

Upload a PDF, wait for “可提问”, open it, ask a question, expand a source, and select its page link. PDFs can be read before indexing completes. Status refreshes every three seconds. Click Send or press Enter to ask; Shift+Enter adds a newline. Conversations remain only in the current reader session and are not saved or sent as history. No streaming, collections or coordinate highlighting yet.

This is a local development setup. A production static host needs to route `/api` to the backend; Vite's proxy is development-only. There is no authentication, so do not expose this setup publicly.
