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
from datetime import datetime
import io
import socketserver
import http.server
import urllib.parse
import urllib.request
import subprocess
import time

HEALTH_SERVICES = ("hermes-brain-api.service", "hermes-gateway.service", "nginx.service", "cloudflared.service")

def get_system_health():
    services = []
    for unit in HEALTH_SERVICES:
        try:
            raw = subprocess.check_output(
                ["systemctl", "show", unit, "--no-page", "--property=LoadState,ActiveState,SubState,MainPID,NRestarts,ActiveEnterTimestamp"],
                text=True, timeout=2,
            )
            props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            services.append({"unit": unit, "load": props.get("LoadState", "unknown"), "active": props.get("ActiveState", "unknown"), "sub": props.get("SubState", "unknown"), "pid": int(props.get("MainPID", "0") or 0), "restarts": int(props.get("NRestarts", "0") or 0), "since": props.get("ActiveEnterTimestamp", "")})
        except Exception as exc:
            services.append({"unit": unit, "load": "unknown", "active": "unknown", "sub": "unknown", "pid": 0, "restarts": 0, "error": str(exc)})
    mem = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            mem = {k: int(v.split()[0]) for line in f if (parts := line.split(":", 1)) and len(parts) == 2 for k, v in [parts]}
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", 0)
        memory = {"total_mb": round(total / 1024), "used_mb": round((total - available) / 1024), "percent": round((total - available) / total * 100, 1) if total else 0}
    except Exception:
        memory = {"total_mb": 0, "used_mb": 0, "percent": 0}
    disk = shutil.disk_usage("/")
    try:
        uptime_seconds = int(float(open("/proc/uptime", encoding="utf-8").read().split()[0]))
    except Exception:
        uptime_seconds = 0
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        load = []
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            cpu_before = list(map(int, f.readline().split()[1:]))
        time.sleep(0.15)
        with open("/proc/stat", encoding="utf-8") as f:
            cpu_after = list(map(int, f.readline().split()[1:]))
        idle_before = cpu_before[3] + cpu_before[4]
        idle_after = cpu_after[3] + cpu_after[4]
        total_before, total_after = sum(cpu_before), sum(cpu_after)
        total_delta = total_after - total_before
        cpu_percent = round((1 - (idle_after - idle_before) / total_delta) * 100, 1) if total_delta else 0
    except (OSError, ValueError, IndexError):
        cpu_percent = None
    return {"success": True, "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"), "services": services, "resources": {"cpu_percent": cpu_percent, "load_average": load, "memory": memory, "disk": {"free_bytes": disk.free, "total_bytes": disk.total, "percent": round(disk.used / disk.total * 100, 1) if disk.total else 0}, "uptime_seconds": uptime_seconds}}


HOST = os.environ.get("HERMES_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("HERMES_PORT", 8093)))
HERMES_WEB_DIR = os.environ.get("HERMES_WEB_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERMES_REPO_BRANCH = os.environ.get("HERMES_REPO_BRANCH", "main")
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
    return os.environ.get("HERMES_PIN_SECRET", "123456")

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

        # Static file fallback if requested (e.g. running standalone via CLI without Nginx)
        if not path.startswith("/api/"):
            clean_path = parsed.path
            if clean_path.startswith("/hermes/"):
                clean_path = clean_path[len("/hermes/"):]
            clean_path = clean_path.lstrip("/")
            if not clean_path or clean_path.endswith("/"):
                clean_path = os.path.join(clean_path, "index.html")
            
            static_file = os.path.join(HERMES_WEB_DIR, clean_path)
            if os.path.isdir(static_file):
                static_file = os.path.join(static_file, "index.html")
            
            if os.path.exists(static_file) and os.path.isfile(static_file):
                ext = os.path.splitext(static_file)[1].lower()
                mimetypes = {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".ico": "image/x-icon",
                    ".mp3": "audio/mpeg",
                    ".wav": "audio/wav"
                }
                ctype = mimetypes.get(ext, "application/octet-stream")
                try:
                    with open(static_file, "rb") as sf:
                        content = sf.read()
                    self.send_response(200)
                    self._send_cors()
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception:
                    pass

        if not self._check_auth():
            return self._send_json(401, {"success": False, "error": "Unauthorized: PIN diperlukan"})

        if path == "/api/hermes/system/health":
            return self._send_json(200, get_system_health())

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
                q = urllib.parse.parse_qs(parsed.query)
                # Filter item modular: items=persona,memory,skills,config
                # selected_skills=skill1,skill2
                raw_items = q.get("items", ["all"])[0].split(",")
                items_set = set(raw_items) if "all" not in raw_items else {"all"}
                selected_skills = [s.strip() for s in q.get("skills", [""])[0].split(",") if s.strip()]

                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    include_all = ("all" in items_set)

                    # 1. Persona (SOUL, IDENTITY, USER, TOOLS)
                    if include_all or "persona" in items_set:
                        persona_files = ["SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md"]
                        for fname in persona_files:
                            fpath = os.path.join(hermes_dir, fname)
                            if os.path.exists(fpath):
                                tar.add(fpath, arcname=fname)

                    # 2. Memory (MEMORY.md & memories/)
                    if include_all or "memory" in items_set:
                        mem_file = os.path.join(hermes_dir, "MEMORY.md")
                        if os.path.exists(mem_file):
                            tar.add(mem_file, arcname="MEMORY.md")
                        mem_dir = os.path.join(hermes_dir, "memories")
                        if os.path.exists(mem_dir):
                            tar.add(mem_dir, arcname="memories")

                    # 3. Config (config.yaml, auth.json)
                    if include_all or "config" in items_set:
                        for fname in ["config.yaml", "auth.json"]:
                            fpath = os.path.join(hermes_dir, fname)
                            if os.path.exists(fpath):
                                tar.add(fpath, arcname=fname)

                    # 4. Skills (all atau specific skills)
                    if include_all or "skills" in items_set:
                        skills_dir = os.path.join(hermes_dir, "skills")
                        if os.path.exists(skills_dir):
                            if not selected_skills:
                                # Export all skills
                                tar.add(skills_dir, arcname="skills")
                            else:
                                # Export selected skills only
                                for sname in selected_skills:
                                    s_target = os.path.join(skills_dir, sname)
                                    if os.path.exists(s_target):
                                        tar.add(s_target, arcname=f"skills/{sname}")
                                    else:
                                        # Cek jika ada extensi .md
                                        s_md = os.path.join(skills_dir, f"{sname}.md")
                                        if os.path.exists(s_md):
                                            tar.add(s_md, arcname=f"skills/{sname}.md")

                buf.seek(0)
                data = buf.read()
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", 'attachment; filename="hermes-export-bundle.tar.gz"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                return self._send_json(500, {"success": False, "error": str(e)})

        if path == "/api/hermes/system/update/check":
            try:
                import subprocess
                local_sha = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "rev-parse", "HEAD"],
                    stderr=subprocess.STDOUT
                ).decode("utf-8").strip()
                
                remote_out = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "ls-remote", "origin", f"refs/heads/{HERMES_REPO_BRANCH}"],
                    stderr=subprocess.STDOUT, timeout=10
                ).decode("utf-8").strip()
                remote_sha = remote_out.split()[0] if remote_out else local_sha

                has_update = (local_sha != remote_sha)
                
                log_out = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "log", "-1", "--format=%h - %s (%cr)"],
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
                    ["git", "-C", HERMES_WEB_DIR, "rev-parse", "HEAD"],
                    stderr=subprocess.STDOUT
                ).decode("utf-8").strip()
                
                remote_out = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "ls-remote", "origin", f"refs/heads/{HERMES_REPO_BRANCH}"],
                    stderr=subprocess.STDOUT, timeout=10
                ).decode("utf-8").strip()
                remote_sha = remote_out.split()[0] if remote_out else local_sha

                has_update = (local_sha != remote_sha)
                
                log_out = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "log", "-1", "--format=%h - %s (%cr)"],
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
                # Pastikan perubahan konfigurasi/file lokal di-stash otomatis agar rebase git clean
                subprocess.run(
                    ["git", "-C", HERMES_WEB_DIR, "stash"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                pull_out = subprocess.check_output(
                    ["git", "-C", HERMES_WEB_DIR, "pull", "--rebase", "origin", HERMES_REPO_BRANCH],
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

        if path == "/api/hermes/feedback":
            category = body.get("category", "Saran").strip()
            subject_input = body.get("subject", "").strip()
            message_input = body.get("message", "").strip()
            sender_name = body.get("sender_name", "").strip() or "Operator Hermes"

            if not message_input:
                return self._send_json(400, {"success": False, "error": "Pesan masukan tidak boleh kosong"})

            full_subject = f"[Hermes Masukan - {category}] {subject_input or 'Tanpa Judul'}"
            timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            html_body = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;">
              <h2 style="margin: 0 0 12px; color: #2563eb; font-size: 20px;">Hermes Control Center — Kotak Masukan</h2>
              <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-bottom: 16px; font-size: 14px;">
                <p style="margin: 0 0 6px;"><strong>Kategori:</strong> {category}</p>
                <p style="margin: 0 0 6px;"><strong>Pengirim:</strong> {sender_name}</p>
                <p style="margin: 0;"><strong>Waktu:</strong> {timestamp_str}</p>
              </div>
              <div style="font-size: 14px; line-height: 1.6; color: #1e293b; white-space: pre-wrap; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px;">
{message_input}
              </div>
              <p style="margin: 16px 0 0; font-size: 12px; color: #94a3b8;">Dikirim otomatis oleh Hermes Brain API dari Control Center.</p>
            </div>
            """

            try:
                import smtplib, ssl
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart

                msg = MIMEMultipart("alternative")
                msg["Subject"] = full_subject
                msg["From"] = "admin@0xhannn.com"
                msg["To"] = "admin@0xhannn.com"
                msg.attach(MIMEText(html_body, "html", "utf-8"))

                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL("smtp.zoho.com", 465, context=ctx, timeout=12) as s:
                    s.login("admin@0xhannn.com", "ncBCGqLU9pYB")
                    s.sendmail("admin@0xhannn.com", ["admin@0xhannn.com"], msg.as_string())

                return self._send_json(200, {"success": True, "message": "Masukan berhasil dikirim ke admin@0xhannn.com"})
            except Exception as e:
                return self._send_json(500, {"success": False, "error": f"Gagal mengirim email: {str(e)}"})

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
    with socketserver.TCPServer((HOST, PORT), HermesBrainHandler) as httpd:
        print(f"Hermes Brain API running on {HOST}:{PORT}...")
        httpd.serve_forever()
