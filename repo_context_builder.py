"""Criador de Contexto Git.

Copyright (c) 2026 Pedro Augusto Martins Costa Alcantara

Licensed under the MIT License. See the LICENSE file for details.
"""

import atexit
import fnmatch
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Git Context Builder")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@dataclass
class RepoState:
    repo_url: str
    path: Path
    files: List[str]
    output: str = ""


# Armazena clones temporarios desta execucao.
REPOS: Dict[str, RepoState] = {}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def render_page(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **context: object,
):
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
        status_code=status_code,
    )


def redirect_to(request: Request, route_name: str, **path_params: str) -> RedirectResponse:
    return RedirectResponse(url=request.url_for(route_name, **path_params), status_code=303)


def build_auth_url(repo_url: str, username: str, password: str) -> str:
    if not username and not password:
        return repo_url

    parts = urlsplit(repo_url)
    if parts.scheme not in ("http", "https"):
        # SSH e outros esquemas normalmente nao usam autenticacao embutida na URL.
        return repo_url

    user = quote(username, safe="")
    pwd = quote(password, safe="")
    auth = user
    if password:
        auth += f":{pwd}"
    netloc = f"{auth}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def list_repo_files(repo_path: Path) -> List[str]:
    files: List[str] = []
    for path in repo_path.rglob("*"):
        if path.is_dir():
            continue
        if ".git" in path.parts:
            continue
        files.append(path.relative_to(repo_path).as_posix())
    files.sort()
    return files


def build_tree(paths: List[str]) -> Dict[str, object]:
    tree: Dict[str, object] = {}
    for full_path in paths:
        parts = full_path.split("/")
        node: Dict[str, object] = tree
        for idx, piece in enumerate(parts):
            is_leaf = idx == len(parts) - 1
            if is_leaf:
                node[piece] = full_path
                continue

            child = node.setdefault(piece, {})
            if isinstance(child, dict):
                node = child
    return tree


def is_probably_binary(file_path: Path) -> bool:
    try:
        chunk = file_path.read_bytes()[:1024]
    except OSError:
        return True
    return b"\x00" in chunk


def match_any_pattern(path: str, patterns: List[str]) -> bool:
    basename = path.split("/")[-1]
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
    return False


def infer_markdown_language(rel_path: str) -> str:
    extension = Path(rel_path).suffix.lower().lstrip(".")
    return extension or "text"


def to_markdown_section(rel_path: str, content: str) -> str:
    if rel_path.lower().endswith(".md"):
        return f"===== {rel_path} =====\n{content}\n"

    language = infer_markdown_language(rel_path)
    fence = "````" if "```" in content else "```"
    return f"===== {rel_path} =====\n{fence}{language}\n{content}\n{fence}\n"


def generate_context(repo_path: Path, selected_files: List[str], ignore_patterns: List[str]) -> tuple[str, int]:
    chunks: List[str] = []
    final_files = [path for path in selected_files if not match_any_pattern(path, ignore_patterns)]
    final_files.sort()
    included_count = 0

    for rel_path in final_files:
        abs_path = repo_path / rel_path
        if not abs_path.exists() or not abs_path.is_file() or is_probably_binary(abs_path):
            continue

        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        chunks.append(to_markdown_section(rel_path, content))
        included_count += 1

    return "\n".join(chunks), included_count


def clone_repository(
    repo_url: str,
    branch: str,
    username: str,
    password: str,
) -> tuple[Optional[Path], Optional[str]]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="repo_context_"))
    auth_url = build_auth_url(repo_url, username, password)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [auth_url, str(tmp_dir)]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, f"Failed to run git: {exc}"

    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        error_message = (result.stderr or result.stdout or "Unknown error during git clone").strip()
        return None, error_message

    return tmp_dir, None


def get_repo(repo_id: str) -> Optional[RepoState]:
    return REPOS.get(repo_id)


@app.get("/")
async def index(request: Request):
    return render_page(request, "index.html", error=None, page="index")


@app.post("/connect")
async def connect(request: Request):
    form = await request.form()
    repo_url = (form.get("repo_url") or "").strip()
    branch = (form.get("branch") or "").strip()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""

    if not repo_url:
        return render_page(
            request,
            "index.html",
            status_code=400,
            error="Enter the repository URL.",
            page="index",
        )

    tmp_dir, error = clone_repository(repo_url, branch, username, password)
    if error or tmp_dir is None:
        return render_page(
            request,
            "index.html",
            status_code=400,
            error=error,
            page="index",
        )

    files = list_repo_files(tmp_dir)
    repo_id = uuid.uuid4().hex
    REPOS[repo_id] = RepoState(repo_url=repo_url, path=tmp_dir, files=files)
    return redirect_to(request, "select_files", repo_id=repo_id)


@app.get("/select/{repo_id}")
async def select_files(request: Request, repo_id: str):
    repo = get_repo(repo_id)
    if repo is None:
        return redirect_to(request, "index")

    return render_page(
        request,
        "select.html",
        repo_id=repo_id,
        repo_url=repo.repo_url,
        total_files=len(repo.files),
        tree=build_tree(repo.files),
        page="select",
    )


@app.post("/generate/{repo_id}")
async def generate(request: Request, repo_id: str):
    repo = get_repo(repo_id)
    if repo is None:
        return redirect_to(request, "index")

    form = await request.form()
    selected = form.getlist("selected")
    ignore_raw = form.get("ignore_patterns") or ""
    ignore_patterns = [line.strip() for line in ignore_raw.splitlines() if line.strip()]

    output, included_count = generate_context(repo.path, selected, ignore_patterns)
    repo.output = output
    preview = output[:6000] if output else "No text file was selected or found."

    return render_page(
        request,
        "result.html",
        repo_id=repo_id,
        file_count=included_count,
        preview=preview,
        page="result",
    )


@app.get("/download/{repo_id}")
async def download(request: Request, repo_id: str):
    repo = get_repo(repo_id)
    if repo is None:
        return redirect_to(request, "index")

    headers = {"Content-Disposition": 'attachment; filename="repo_context.txt"'}
    return Response(content=repo.output, media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/view/{repo_id}")
async def view_text(request: Request, repo_id: str):
    repo = get_repo(repo_id)
    if repo is None:
        return redirect_to(request, "index")

    return PlainTextResponse(repo.output)


@atexit.register
def cleanup_repos():
    for repo in REPOS.values():
        shutil.rmtree(repo.path, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
        reload=env_flag("APP_RELOAD"),
    )
