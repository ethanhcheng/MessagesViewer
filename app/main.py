from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .auth import SESSION_COOKIE, is_authenticated, new_session_token, require_auth, verify_credentials
from .config import config

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Messages Viewer")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

VALID_SESSIONS: set[str] = set()


def auth_dep(request: Request) -> None:
    require_auth(request, VALID_SESSIONS)


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> Response:
    if not is_authenticated(request, VALID_SESSIONS):
        return RedirectResponse("/login", status_code=303)
    if not config.is_configured():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Incorrect username or password"},
            status_code=401,
        )
    token = new_session_token()
    VALID_SESSIONS.add(token)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    return resp


@app.post("/logout")
def logout(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        VALID_SESSIONS.discard(token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, _: None = Depends(auth_dep)) -> Response:
    return templates.TemplateResponse(
        "setup.html",
        {"request": request, "current": config.data_dir, "error": None},
    )


@app.post("/setup")
def setup_submit(request: Request, data_dir: str = Form(...), _: None = Depends(auth_dep)) -> Response:
    path = Path(data_dir).expanduser()
    chat_db = path / "chat.db"
    if not chat_db.exists():
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "current": data_dir,
                "error": f"chat.db not found at {chat_db}",
            },
            status_code=400,
        )
    config.set_data_dir(str(path))
    return RedirectResponse("/", status_code=303)


@app.get("/api/config")
def api_config(_: None = Depends(auth_dep)) -> dict:
    return {
        "data_dir": config.data_dir,
        "configured": config.is_configured(),
    }


@app.get("/api/chats")
def api_chats(limit: int = 500, _: None = Depends(auth_dep)) -> list[dict]:
    return db.list_chats(limit=limit)


@app.get("/api/chats/{chat_id}/messages")
def api_chat_messages(
    chat_id: int,
    limit: int = 1000,
    offset: int = 0,
    _: None = Depends(auth_dep),
) -> list[dict]:
    messages = db.get_chat_messages(chat_id, limit=limit, offset=offset)
    for msg in messages:
        if msg["attachment_count"]:
            msg["attachments"] = db.get_message_attachments(msg["message_id"])
    return messages


@app.get("/api/search")
def api_search(q: str, limit: int = 200, _: None = Depends(auth_dep)) -> list[dict]:
    if not q.strip():
        return []
    return db.search_messages(q.strip(), limit=limit)


@app.get("/api/attachments/{attachment_id}")
def api_attachment(attachment_id: int, _: None = Depends(auth_dep)) -> Response:
    att = db.get_attachment(attachment_id)
    if not att:
        raise HTTPException(404, "Attachment not found")
    path = db.resolve_attachment_path(att["filename"])
    if not path:
        raise HTTPException(404, "Attachment file missing on disk")
    return FileResponse(path, media_type=att["mime_type"] or "application/octet-stream")
