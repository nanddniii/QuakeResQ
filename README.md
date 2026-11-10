# QuakeResQ

QuakeResQ is a two-member mini project foundation for dynamic earthquake rescue planning and resource allocation. This starter includes shared database models, a FastAPI health endpoint, and a small React dashboard shell. Planning algorithms, external earthquake feeds, and operational APIs are intentionally not implemented yet.

## Project structure

```text
QuakeResQ/
├── backend/
│   ├── app/
│   │   ├── models/
│   │   ├── routes/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── database.py
│   │   └── main.py
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── data/
│   │   ├── pages/
│   │   ├── services/
│   │   └── tests/
│   ├── index.html
│   └── package.json
├── .gitignore
└── README.md
```

The SQLite database file is created as `backend/quake_resq.db` when the backend starts. Road graph data is not persisted in the database; road closure rows identify affected graph segments by node and edge IDs.

## Requirements

- Python 3.10 or newer
- Node.js 20.19 or newer and npm

## Run the backend

From the repository root, create and activate a virtual environment, then install the backend dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs`; `GET /health` returns `{"status":"ok"}`.

To run the backend test from the `backend` directory:

```powershell
python -m pytest
```

## Run the frontend

Open a second terminal at the repository root:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal (by default, `http://127.0.0.1:5173`). The dashboard's **Check backend** button calls `GET /health`.

To use a different backend URL, set `VITE_API_BASE_URL` in a `frontend/.env` file, for example:

```text
VITE_API_BASE_URL=http://localhost:8000
```

The frontend includes Leaflet and React Leaflet dependencies, but map behavior is not part of this starter.
