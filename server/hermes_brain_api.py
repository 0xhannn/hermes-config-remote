#!/usr/bin/env python3
"""
Hermes Real-Time Brain API Service
Port: 8093
Endpoints:
  POST /api/hermes/auth
  GET  /api/hermes/config
  POST /api/hermes/model/switch
  POST /api/hermes/provider/add
  POST /api/hermes/provider/edit
  POST /api/hermes/provider/delete
"""

import os
import sys
import json
import yaml
import shutil
import tarfile
import tempfile
import io
import socketserver
import http.server
import urllib.parse
import urllib.request

PORT = int(os.environ.get("HERMES_PORT", 8093))
CONFIG_PATH = os.environ.get("HERMES_CONFIG_PATH", os.path.expanduser("~/.hermes/config.yaml"))
PIN_FILE = os.path.expanduser("~/.hermes/.pin_secret")

def get_current_pin():
    if os.path.exists(PIN_FILE):
        try:
            with open(PIN_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip()
                if saved:
                    return saved
        except Exception:
            pass
    return os.environ.get("HERMES_PIN_SECRET", "hermes123")

def set_current_pin(new_pin):
    os.makedirs(os.path.dirname(PIN_FILE), exist_ok=True)
    with open(PIN_FILE, "w", encoding="utf-8") as f:
        f.write(new_pin.strip())

def load_yaml():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_yaml(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

class HermesBrainHandler(http.server.BaseHTTPRequestHandler):
    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hermes-PIN, X-Hermes-Key")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def _check_auth(self):
        current_pin = get_current_pin()
        pin_hdr = self.headers.get("X-Hermes-PIN") or self.headers.get("X-Hermes-Key")
        if pin_hdr == current_pin:
            return True
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if params.get("pin", [""])[0] == current_pin:
            return True
        return False

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._check_auth():
            return self._send_json(401, {"success": False, "error": "Unauthorized: PIN diperlukan"})

        if path == "/api/hermes/config":
            try:
                cfg = load_yaml()
                model_cfg = cfg.get("model", {})
                active_model = model_cfg.get("default", "unknown")
                active_provider = model_cfg.get("provider", "custom")
                active_base_url = model_cfg.get("base_url", "")

                custom_providers = cfg.get("custom_providers", [])
                # Clean custom providers and fetch models dynamically with auth
                clean_providers = []
                for p in custom_providers:
                    p_name = p.get("name", "")
                    p_base = p.get("base_url", "")
                    p_key = p.get("api_key", "")
                    p_model = p.get("model", "")
                    
                    models_list = []
                    if p_base:
                        try:
                            v1_models_url = p_base.rstrip("/") + "/models" if not p_base.endswith("/models") else p_base
                            req_hdrs = {}
                            if p_key:
                                req_hdrs["Authorization"] = f"Bearer {p_key}"
                            m_req = urllib.request.Request(v1_models_url, headers=req_hdrs)
                            with urllib.request.urlopen(m_req, timeout=1.2) as m_resp:
                                m_json = json.loads(m_resp.read().decode())
                                models_list = [m.get("id") for m in m_json.get("data", []) if m.get("id")]
                        except Exception:
                            pass
                    
                    if not models_list and p_model:
                        models_list = [p_model]

                    clean_providers.append({
                        "name": p_name,
                        "type": "custom",
                        "base_url": p_base,
                        "model": p_model,
                        "models": models_list,
                        "api_mode": p.get("api_mode", "chat_completions"),
                        "has_key": bool(p_key)
                    })

                # Also read authenticated providers from /root/.hermes/auth.json (OAuth / built-ins like openai-codex, copilot)
                auth_path = "/root/.hermes/auth.json"
                if os.path.exists(auth_path):
                    try:
                        with open(auth_path, "r", encoding="utf-8") as af:
                            auth_data = json.load(af)
                        
                        # Providers dict (OAuth providers)
                        auth_provs = auth_data.get("providers", {})
                        if "openai-codex" in auth_provs:
                            codex_models = []
                            try:
                                from hermes_cli.codex_models import DEFAULT_CODEX_MODELS, _add_forward_compat_models
                                codex_models = _add_forward_compat_models(list(DEFAULT_CODEX_MODELS))
                            except Exception:
                                codex_models = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.3-codex-spark"]
                            
                            clean_providers.append({
                                "name": "openai-codex",
                                "type": "oauth",
                                "base_url": "https://chatgpt.com/backend-api/codex",
                                "model": codex_models[0] if codex_models else "gpt-5.5",
                                "models": codex_models,
                                "api_mode": "chat_completions",
                                "has_key": True
                            })

                        # Credential pool
                        cred_pool = auth_data.get("credential_pool", {})
                        if "copilot" in cred_pool and cred_pool.get("copilot"):
                            copilot_entry = cred_pool["copilot"][0]
                            copilot_models = []
                            try:
                                from hermes_cli.models import _PROVIDER_MODELS
                                copilot_models = list(_PROVIDER_MODELS.get("copilot", []))
                            except Exception:
                                copilot_models = [
                                    "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-4o",
                                    "claude-sonnet-4.6", "claude-haiku-4.5", "gemini-3.1-pro-preview"
                                ]
                            
                            clean_providers.append({
                                "name": "copilot",
                                "type": "credential_pool",
                                "base_url": copilot_entry.get("base_url", "https://api.githubcopilot.com"),
                                "model": copilot_models[0] if copilot_models else "gpt-4o",
                                "models": copilot_models,
                                "api_mode": "chat_completions",
                                "has_key": True
                            })
                    except Exception as e:
                        pass

                # Fetch available 9router models if 9router is running
                # Use Hermes custom_provider api_key for 9router if available, so it matches allowed models for this key
                router_models = []
                try:
                    router_key = None
                    for p in custom_providers:
                        if "20128" in p.get("base_url", "") or "9-router" in p.get("name", "").lower():
                            router_key = p.get("api_key")
                            break

                    req_headers = {}
                    if router_key:
                        req_headers["Authorization"] = f"Bearer {router_key}"

                    req = urllib.request.Request("http://127.0.0.1:20128/v1/models", headers=req_headers)
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        m_data = json.loads(resp.read().decode())
                        router_models = [m.get("id") for m in m_data.get("data", []) if m.get("id")]
                except Exception:
                    pass

                return self._send_json(200, {
                    "success": True,
                    "active": {
                        "model": active_model,
                        "provider": active_provider,
                        "base_url": active_base_url
                    },
                    "providers": clean_providers,
                    "router_models": router_models
                })
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if parsed.path.rstrip("/") == "/api/hermes/skills":
            try:
                import sys
                if "/usr/local/lib/hermes-agent" not in sys.path:
                    sys.path.insert(0, "/usr/local/lib/hermes-agent")
                from tools.skills_tool import skills_list
                res_str = skills_list()
                res_data = json.loads(res_str)
                return self._send_json(200, res_data)
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if parsed.path.rstrip("/") == "/api/hermes/skills/view":
            try:
                q = urllib.parse.parse_qs(parsed.query)
                skill_name = q.get("name", [""])[0]
                if not skill_name:
                    return self._send_json(400, {"success": False, "error": "Query param 'name' required"})
                import sys
                if "/usr/local/lib/hermes-agent" not in sys.path:
                    sys.path.insert(0, "/usr/local/lib/hermes-agent")
                from tools.skills_tool import skill_view
                res_str = skill_view(name=skill_name)
                res_data = json.loads(res_str)
                return self._send_json(200, res_data)
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if parsed.path.rstrip("/") == "/api/hermes/backup/export":
            try:
                hermes_dir = os.path.expanduser("~/.hermes")
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    cfg_file = os.path.join(hermes_dir, "config.yaml")
                    if os.path.exists(cfg_file):
                        tar.add(cfg_file, arcname="config.yaml")
                    db_file = os.path.join(hermes_dir, "state.db")
                    if os.path.exists(db_file):
                        tar.add(db_file, arcname="state.db")
                    mem_dir = os.path.join(hermes_dir, "memories")
                    if os.path.exists(mem_dir):
                        tar.add(mem_dir, arcname="memories")
                    skills_dir = os.path.join(hermes_dir, "skills")
                    if os.path.exists(skills_dir):
                        tar.add(skills_dir, arcname="skills")

                buf.seek(0)
                data = buf.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", 'attachment; filename="hermes-backup.tar.gz"')
                self.send_header("Content-Length", str(len(data)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        return self._send_json(404, {"success": False, "error": "Endpoint not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            body = {}

        if path == "/api/hermes/auth":
            pin = body.get("pin", "")
            current_pin = get_current_pin()
            if pin == current_pin:
                return self._send_json(200, {"success": True, "message": "Auth OK"})
            return self._send_json(401, {"success": False, "error": "PIN tidak valid"})

        if not self._check_auth():
            return self._send_json(401, {"success": False, "error": "Unauthorized: PIN diperlukan"})

        if path == "/api/hermes/auth/change-pin":
            old_pin = body.get("old_pin", "").strip()
            new_pin = body.get("new_pin", "").strip()
            current_pin = get_current_pin()

            if not old_pin or not new_pin:
                return self._send_json(400, {"success": False, "error": "PIN lama dan PIN baru wajib diisi"})

            if old_pin != current_pin:
                return self._send_json(400, {"success": False, "error": "PIN lama tidak cocok"})

            if len(new_pin) < 4:
                return self._send_json(400, {"success": False, "error": "PIN baru minimal 4 karakter"})

            set_current_pin(new_pin)
            return self._send_json(200, {"success": True, "message": "PIN / Password berhasil diperbarui!"})

        if path == "/api/hermes/model/switch":
            # Ganti provider + model aktif
            target_provider = body.get("provider", "").strip()
            target_model = body.get("model", "").strip()
            if not target_model:
                return self._send_json(400, {"success": False, "error": "Model wajib diisi"})

            cfg = load_yaml()
            if "model" not in cfg:
                cfg["model"] = {}
            cfg["model"]["default"] = target_model
            if target_provider:
                cfg["model"]["provider"] = target_provider

            # Handle built-in / OAuth providers khusus
            if target_provider == "openai-codex":
                cfg["model"]["base_url"] = "https://chatgpt.com/backend-api/codex"
                cfg["model"].pop("api_key", None)
            elif target_provider == "copilot":
                cfg["model"]["base_url"] = "https://api.githubcopilot.com"
                cfg["model"].pop("api_key", None)
            else:
                # Bila provider adalah salah satu custom_provider, sinkronkan base_url & key
                custom_providers = cfg.get("custom_providers", [])
                for p in custom_providers:
                    if p.get("name") == target_provider or p.get("model") == target_model:
                        if p.get("base_url"):
                            cfg["model"]["base_url"] = p.get("base_url")
                        if p.get("api_key"):
                            cfg["model"]["api_key"] = p.get("api_key")
                        break

            save_yaml(cfg)
            return self._send_json(200, {
                "success": True,
                "message": f"Model aktif berhasil diganti ke {target_model} (Provider: {cfg['model'].get('provider')})",
                "active": {
                    "model": cfg["model"]["default"],
                    "provider": cfg["model"].get("provider"),
                    "base_url": cfg["model"].get("base_url")
                }
            })

        if path == "/api/hermes/provider/add":
            name = body.get("name", "").strip()
            base_url = body.get("base_url", "").strip()
            model = body.get("model", "").strip()
            api_key = body.get("api_key", "").strip()
            api_mode = body.get("api_mode", "chat_completions").strip()

            if not name or not base_url or not model:
                return self._send_json(400, {"success": False, "error": "Nama, base_url, dan model wajib diisi"})

            cfg = load_yaml()
            if "custom_providers" not in cfg or not isinstance(cfg["custom_providers"], list):
                cfg["custom_providers"] = []

            # Cek duplikat
            for p in cfg["custom_providers"]:
                if p.get("name") == name:
                    return self._send_json(400, {"success": False, "error": f"Provider '{name}' sudah ada"})

            new_prov = {
                "name": name,
                "base_url": base_url,
                "model": model,
                "api_mode": api_mode
            }
            if api_key:
                new_prov["api_key"] = api_key

            cfg["custom_providers"].append(new_prov)
            save_yaml(cfg)
            return self._send_json(200, {"success": True, "message": f"Provider '{name}' berhasil ditambahkan"})

        if path == "/api/hermes/provider/edit":
            name = body.get("name", "").strip() # Original name
            new_name = body.get("new_name", "").strip() or name
            base_url = body.get("base_url", "").strip()
            model = body.get("model", "").strip()
            api_key = body.get("api_key", "").strip()

            cfg = load_yaml()
            custom_providers = cfg.get("custom_providers", [])
            found = False
            for p in custom_providers:
                if p.get("name") == name:
                    found = True
                    p["name"] = new_name
                    if base_url:
                        p["base_url"] = base_url
                    if model:
                        p["model"] = model
                    if api_key:
                        p["api_key"] = api_key
                    break

            if not found:
                return self._send_json(404, {"success": False, "error": f"Provider '{name}' tidak ditemukan"})

            save_yaml(cfg)
            return self._send_json(200, {"success": True, "message": f"Provider '{new_name}' berhasil diperbarui"})

        if path == "/api/hermes/provider/delete":
            name = body.get("name", "").strip()
            if not name:
                return self._send_json(400, {"success": False, "error": "Nama provider wajib diisi"})

            cfg = load_yaml()
            custom_providers = cfg.get("custom_providers", [])
            initial_len = len(custom_providers)
            cfg["custom_providers"] = [p for p in custom_providers if p.get("name") != name]

            if len(cfg["custom_providers"]) == initial_len:
                return self._send_json(404, {"success": False, "error": f"Provider '{name}' tidak ditemukan"})

            save_yaml(cfg)
            return self._send_json(200, {"success": True, "message": f"Provider '{name}' berhasil dihapus"})

        if path == "/api/hermes/skills/save":
            name = body.get("name", "").strip()
            content = body.get("content", "")
            category = body.get("category", "").strip() or None
            is_new = body.get("is_new", False)

            if not name or not content:
                return self._send_json(400, {"success": False, "error": "Nama skill dan konten wajib diisi"})

            try:
                import sys
                if "/usr/local/lib/hermes-agent" not in sys.path:
                    sys.path.insert(0, "/usr/local/lib/hermes-agent")
                from tools.skill_manager_tool import skill_manage
                action = "create" if is_new else "edit"
                res_str = skill_manage(action=action, name=name, content=content, category=category)
                res_data = json.loads(res_str)
                return self._send_json(200, res_data)
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if path == "/api/hermes/skills/delete":
            name = body.get("name", "").strip()
            if not name:
                return self._send_json(400, {"success": False, "error": "Nama skill wajib diisi"})

            try:
                import sys
                if "/usr/local/lib/hermes-agent" not in sys.path:
                    sys.path.insert(0, "/usr/local/lib/hermes-agent")
                from tools.skill_manager_tool import skill_manage
                res_str = skill_manage(action="delete", name=name)
                res_data = json.loads(res_str)
                return self._send_json(200, res_data)
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if path == "/api/hermes/system/update/check":
            try:
                import subprocess
                local_sha = subprocess.check_output(
                    ["git", "-C", "/var/www/maniflow", "rev-parse", "HEAD"],
                    stderr=subprocess.STDOUT
                ).decode("utf-8").strip()
                
                remote_out = subprocess.check_output(
                    ["git", "-C", "/var/www/maniflow", "ls-remote", "origin", "refs/heads/master"],
                    stderr=subprocess.STDOUT, timeout=10
                ).decode("utf-8").strip()
                remote_sha = remote_out.split()[0] if remote_out else local_sha

                has_update = (local_sha != remote_sha)
                
                log_out = subprocess.check_output(
                    ["git", "-C", "/var/www/maniflow", "log", "-1", "--format=%h - %s (%cr)"],
                    stderr=subprocess.STDOUT
                ).decode("utf-8").strip()

                return self._send_json(200, {
                    "success": True,
                    "local_sha": local_sha[:7],
                    "remote_sha": remote_sha[:7],
                    "has_update": has_update,
                    "last_commit": log_out
                })
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if path == "/api/hermes/system/update/apply":
            try:
                import subprocess
                pull_out = subprocess.check_output(
                    ["git", "-C", "/var/www/maniflow", "pull", "--rebase", "origin", "master"],
                    stderr=subprocess.STDOUT, timeout=30
                ).decode("utf-8")
                
                # Restart backend asynchronously so current request completes safely
                subprocess.Popen(["bash", "-c", "sleep 1 && systemctl restart hermes-brain-api.service"])

                return self._send_json(200, {
                    "success": True,
                    "message": "Update berhasil diterapkan. Layanan dimuat ulang.",
                    "output": pull_out
                })
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if path == "/api/hermes/backup/import":
            try:
                raw_b64 = body.get("file_data", "")
                if not raw_b64:
                    return self._send_json(400, {"success": False, "error": "File data backup wajib diisi"})
                
                import base64
                if "," in raw_b64:
                    raw_b64 = raw_b64.split(",", 1)[1]
                tar_bytes = base64.b64decode(raw_b64)
                
                hermes_dir = os.path.expanduser("~/.hermes")
                buf = io.BytesIO(tar_bytes)
                with tarfile.open(fileobj=buf, mode="r:*") as tar:
                    for member in tar.getmembers():
                        clean_name = os.path.normpath(member.name)
                        if clean_name.startswith("/") or clean_name.startswith(".."):
                            continue
                        target_path = os.path.join(hermes_dir, clean_name)
                        if member.isdir():
                            os.makedirs(target_path, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            with open(target_path, "wb") as f_out:
                                f_in = tar.extractfile(member)
                                if f_in:
                                    shutil.copyfileobj(f_in, f_out)

                return self._send_json(200, {"success": True, "message": "Backup berhasil diimpor ke ~/.hermes"})
            except Exception as e:
                return self._send_json(500, {"success": False, "error": f"Gagal restore backup: {str(e)}"})

        return self._send_json(404, {"success": False, "error": "Endpoint not found"})

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), HermesBrainHandler) as httpd:
        print(f"Hermes Brain API running on port {PORT}...")
        httpd.serve_forever()
