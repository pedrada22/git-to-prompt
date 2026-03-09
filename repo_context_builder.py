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
from io import BytesIO
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote, urlsplit, urlunsplit

from flask import Flask, Response, redirect, render_template_string, request, send_file, url_for

app = Flask(__name__)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_secret_key() -> str:
    secret = os.environ.get("APP_SECRET")
    if secret:
        return secret

    # Fallback aleatorio evita publicar uma chave previsivel no codigo.
    return uuid.uuid4().hex


app.config["SECRET_KEY"] = get_secret_key()

# Armazena clones temporarios desta execucao
REPOS: Dict[str, Dict[str, object]] = {}


INDEX_TEMPLATE = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Criador de Contexto Git</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 16px; }
    .box { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    label { display: block; margin-top: 12px; font-weight: bold; }
    input, button { font-size: 14px; }
    input[type="text"], input[type="password"] { width: 100%; padding: 8px; box-sizing: border-box; }
    button { margin-top: 14px; padding: 10px 14px; cursor: pointer; }
    .error { background: #ffe9e9; border: 1px solid #ffb3b3; color: #900; padding: 10px; border-radius: 6px; }
    .hint { color: #555; font-size: 13px; margin-top: 6px; }
  </style>
</head>
<body>
  <h1>Criador de Contexto Git</h1>
  <div class="box">
    <p>Informe o repositorio e, se necessario, credenciais para clonagem.</p>
    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}
    <form method="post" action="{{ url_for('connect') }}">
      <label for="repo_url">URL do repositorio git</label>
      <input id="repo_url" name="repo_url" type="text" required placeholder="https://github.com/org/repo.git">

      <label for="branch">Branch (opcional)</label>
      <input id="branch" name="branch" type="text" placeholder="main">

      <label for="username">Usuario (opcional)</label>
      <input id="username" name="username" type="text" placeholder="usuario">

      <label for="password">Token/Senha (opcional)</label>
      <input id="password" name="password" type="password" placeholder="ghp_...">
      <div class="hint">Para repositorios privados, prefira token de acesso pessoal.</div>

      <button type="submit">Conectar e listar arquivos</button>
    </form>
  </div>
</body>
</html>
"""


SELECT_TEMPLATE = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Selecionar Arquivos</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; }
    .layout { display: grid; grid-template-columns: 1.2fr 1fr; gap: 18px; }
    .box { border: 1px solid #ddd; border-radius: 8px; padding: 14px; }
    ul.tree { list-style: none; margin: 0; padding-left: 18px; }
    ul.tree li { margin: 4px 0; }
    .folder { font-weight: bold; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
    button { padding: 8px 12px; cursor: pointer; }
    textarea { width: 100%; min-height: 180px; box-sizing: border-box; font-family: Consolas, monospace; }
    .muted { color: #666; font-size: 13px; }
    .header { margin-bottom: 12px; }
  </style>
</head>
<body>
  {% macro render_tree(node, parent="") %}
    <ul class="tree">
    {% for name, value in node.items() %}
      {% set full_path = (parent ~ "/" ~ name).strip("/") %}
      {% if value is mapping %}
        <li>
          <label class="folder">
            <input type="checkbox" class="dir-toggle" data-dir="{{ full_path }}" checked>
            [DIR] {{ name }}
          </label>
          {{ render_tree(value, full_path) }}
        </li>
      {% else %}
        <li>
          <label>
            <input type="checkbox" name="selected" value="{{ full_path }}" data-path="{{ full_path }}" checked>
            [ARQ] {{ name }}
          </label>
        </li>
      {% endif %}
    {% endfor %}
    </ul>
  {% endmacro %}

  <h1>Selecionar arquivos do repositorio</h1>
  <div class="header muted">
    Repositorio: <strong>{{ repo_url }}</strong> | Total de arquivos: {{ total_files }}
  </div>
  <form method="post" action="{{ url_for('generate', repo_id=repo_id) }}">
    <div class="layout">
      <div class="box">
        <div class="actions">
          <button type="button" id="checkAll">Marcar tudo</button>
          <button type="button" id="uncheckAll">Desmarcar tudo</button>
          <button type="button" id="applyIgnore">Aplicar ignore no checklist</button>
        </div>
        <div class="muted">Todas as caixas comecam marcadas. Desmarque o que quiser excluir.</div>
        <hr>
        {{ render_tree(tree) }}
      </div>
      <div class="box">
        <label for="ignore_patterns"><strong>Textarea ignore (opcional)</strong></label>
        <textarea id="ignore_patterns" name="ignore_patterns" placeholder="Exemplos:
*.png
*.jpg
node_modules/*
*.lock"></textarea>
        <p class="muted">Um padrao por linha (estilo glob). Ex.: <code>*.md</code>, <code>docs/*</code>.</p>
        <button type="submit">Gerar texto concatenado</button>
      </div>
    </div>
  </form>

  <script>
    const fileCheckboxes = () => Array.from(document.querySelectorAll('input[name="selected"]'));
    const dirCheckboxes = () => Array.from(document.querySelectorAll('.dir-toggle'));

    document.getElementById('checkAll').addEventListener('click', () => {
      fileCheckboxes().forEach(cb => cb.checked = true);
      dirCheckboxes().forEach(cb => cb.checked = true);
    });

    document.getElementById('uncheckAll').addEventListener('click', () => {
      fileCheckboxes().forEach(cb => cb.checked = false);
      dirCheckboxes().forEach(cb => cb.checked = false);
    });

    dirCheckboxes().forEach(dirCb => {
      dirCb.addEventListener('change', () => {
        const prefix = dirCb.dataset.dir + '/';
        fileCheckboxes().forEach(fileCb => {
          if (fileCb.dataset.path.startsWith(prefix)) {
            fileCb.checked = dirCb.checked;
          }
        });
      });
    });

    function globToRegex(pattern) {
      const escaped = pattern
        .replace(/[.+^${}()|[\\]\\\\]/g, '\\\\$&')
        .replace(/\\*/g, '.*')
        .replace(/\\?/g, '.');
      return new RegExp('^' + escaped + '$');
    }

    document.getElementById('applyIgnore').addEventListener('click', () => {
      const text = document.getElementById('ignore_patterns').value || '';
      const patterns = text.split(/\\r?\\n/).map(s => s.trim()).filter(Boolean);
      if (!patterns.length) return;

      const regexes = patterns.map(globToRegex);
      fileCheckboxes().forEach(fileCb => {
        const path = fileCb.dataset.path;
        const ignored = regexes.some(rx => rx.test(path));
        if (ignored) fileCb.checked = false;
      });
    });
  </script>
</body>
</html>
"""


RESULT_TEMPLATE = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resultado</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 960px; margin: 24px auto; padding: 0 16px; }
    .box { border: 1px solid #ddd; border-radius: 8px; padding: 14px; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 500px; overflow: auto; background: #f7f7f7; padding: 12px; border-radius: 6px; }
    .actions { margin-top: 12px; display: flex; gap: 8px; }
    a, button { padding: 8px 12px; border: 1px solid #aaa; border-radius: 4px; text-decoration: none; color: #111; background: #fff; }
  </style>
</head>
<body>
  <h1>Contexto gerado</h1>
  <div class="box">
    <p>Arquivos incluidos: <strong>{{ file_count }}</strong></p>
    <div class="actions">
      <a href="{{ url_for('download', repo_id=repo_id) }}">Baixar resultado (.txt)</a>
      <a href="{{ url_for('view_text', repo_id=repo_id) }}" target="_blank">Abrir texto completo no navegador</a>
      <a href="{{ url_for('select_files', repo_id=repo_id) }}">Voltar para selecao</a>
    </div>
    <h3>Previa (inicio do arquivo)</h3>
    <pre>{{ preview }}</pre>
  </div>
</body>
</html>
"""


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
        rel = path.relative_to(repo_path).as_posix()
        files.append(rel)
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
            else:
                child = node.setdefault(piece, {})
                if isinstance(child, dict):
                    node = child
    return tree


def is_probably_binary(file_path: Path) -> bool:
    try:
        chunk = file_path.read_bytes()[:1024]
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    return False


def match_any_pattern(path: str, patterns: List[str]) -> bool:
    # Compara o caminho completo e o nome base para facilitar filtros simples.
    basename = path.split("/")[-1]
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
    return False


def infer_markdown_language(rel_path: str) -> str:
    extension = Path(rel_path).suffix.lower().lstrip(".")
    if not extension:
        return "text"
    return extension


def to_markdown_section(rel_path: str, content: str) -> str:
    if rel_path.lower().endswith(".md"):
        return f"===== {rel_path} =====\\n{content}\\n"

    language = infer_markdown_language(rel_path)
    fence = "```"
    if "```" in content:
        fence = "````"
    return f"===== {rel_path} =====\\n{fence}{language}\\n{content}\\n{fence}\\n"


def generate_context(repo_path: Path, selected_files: List[str], ignore_patterns: List[str]) -> tuple[str, int]:
    chunks: List[str] = []
    final_files = [
        p for p in selected_files if not match_any_pattern(p, ignore_patterns)
    ]
    final_files.sort()
    included_count = 0

    for rel_path in final_files:
        abs_path = repo_path / rel_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        if is_probably_binary(abs_path):
            continue

        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        chunks.append(to_markdown_section(rel_path, content))
        included_count += 1
    return "\\n".join(chunks), included_count


@app.get("/")
def index():
    return render_template_string(INDEX_TEMPLATE, error=None)


@app.post("/connect")
def connect():
    repo_url = (request.form.get("repo_url") or "").strip()
    branch = (request.form.get("branch") or "").strip()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not repo_url:
        return render_template_string(INDEX_TEMPLATE, error="Informe a URL do repositorio.")

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
        return render_template_string(INDEX_TEMPLATE, error=f"Falha ao executar git: {exc}")

    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        error_message = (result.stderr or result.stdout or "Erro desconhecido no git clone").strip()
        return render_template_string(INDEX_TEMPLATE, error=error_message)

    files = list_repo_files(tmp_dir)
    repo_id = uuid.uuid4().hex
    REPOS[repo_id] = {
        "repo_url": repo_url,
        "path": tmp_dir,
        "files": files,
        "output": "",
    }
    return redirect(url_for("select_files", repo_id=repo_id))


@app.get("/select/<repo_id>")
def select_files(repo_id: str):
    repo_info = REPOS.get(repo_id)
    if not repo_info:
        return redirect(url_for("index"))

    files = repo_info.get("files", [])
    if not isinstance(files, list):
        files = []
    tree = build_tree(files)
    return render_template_string(
        SELECT_TEMPLATE,
        repo_id=repo_id,
        repo_url=repo_info.get("repo_url", ""),
        total_files=len(files),
        tree=tree,
    )


@app.post("/generate/<repo_id>")
def generate(repo_id: str):
    repo_info = REPOS.get(repo_id)
    if not repo_info:
        return redirect(url_for("index"))

    selected = request.form.getlist("selected")
    ignore_raw = request.form.get("ignore_patterns") or ""
    ignore_patterns = [line.strip() for line in ignore_raw.splitlines() if line.strip()]
    repo_path = repo_info.get("path")

    if not isinstance(repo_path, Path):
        return redirect(url_for("index"))

    output, included_count = generate_context(repo_path, selected, ignore_patterns)
    repo_info["output"] = output

    preview = output[:6000] if output else "Nenhum arquivo de texto foi selecionado/encontrado."
    return render_template_string(
        RESULT_TEMPLATE,
        repo_id=repo_id,
        file_count=included_count,
        preview=preview,
    )


@app.get("/download/<repo_id>")
def download(repo_id: str):
    repo_info = REPOS.get(repo_id)
    if not repo_info:
        return redirect(url_for("index"))

    output = repo_info.get("output", "")
    if not isinstance(output, str):
        output = ""
    bio = BytesIO(output.encode("utf-8"))
    return send_file(
        bio,
        as_attachment=True,
        download_name="contexto_repo.txt",
        mimetype="text/plain; charset=utf-8",
    )


@app.get("/view/<repo_id>")
def view_text(repo_id: str):
    repo_info = REPOS.get(repo_id)
    if not repo_info:
        return redirect(url_for("index"))

    output = repo_info.get("output", "")
    if not isinstance(output, str):
        output = ""
    return Response(output, mimetype="text/plain; charset=utf-8")


@atexit.register
def cleanup_repos():
    for repo_info in REPOS.values():
        repo_path = repo_info.get("path")
        if isinstance(repo_path, Path):
            shutil.rmtree(repo_path, ignore_errors=True)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5000")),
        debug=env_flag("FLASK_DEBUG"),
    )
