"""Launch unmodified upstream MultiBot UI with a separate private local profile.

The loopback-only service creates its own local account and upstream signed
session; it never imports browser, provider, or existing MultiBot credentials.
Use ?login=1 to open upstream account login instead of the managed local entry.
"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import runpy
import secrets
import sys

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT.parent / "llm-tools"
VENDOR = TOOLS / "vendor/multibot-chat"
PRIVATE = TOOLS / ".private/multibot"
USERNAME = "modeldesk_local"
os.umask(0o077)
PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
(PRIVATE / "user_config").mkdir(exist_ok=True, mode=0o700)
(PRIVATE / "tokens").mkdir(exist_ok=True, mode=0o700)
os.environ.update({
    "MULTIBOT_SECRET_KEY_FILE": str(PRIVATE / "secret.key"),
    "MULTIBOT_USER_DATA_FILE": str(PRIVATE / "users.json"),
    "MULTIBOT_USER_CONFIG_BASEDIR": str(PRIVATE / "user_config"),
    "MULTIBOT_BASE_LLM_MODEL": "gpt-6.1-sol",
    "MULTIBOT_BASE_LLM_BASE_URL": "http://127.0.0.1:8317/v1",
    "MULTIBOT_BASE_LLM_API_KEY": "local-oauth",
    "MULTIBOT_SHOW_SECRET_INFO": "True",
})
sys.path.insert(0, str(PROJECT / "scripts"))
sys.path.insert(0, str(VENDOR))
os.chdir(VENDOR)

import config
config.TOKEN_DIR = str(PRIVATE / "tokens")
from multibot_compat import install as install_compatibility
install_compatibility()
# Default new OpenAI bots to this machine's gateway; preserve saved bot settings.
for field in config.ENGINE_CONFIG["engines"]["OpenAI"]["fields"]:
    if field["name"] in {"base_url", "api_key", "model"}:
        field["default"] = {"base_url": "http://127.0.0.1:8317/v1", "api_key": "local-oauth", "model": "gpt-6.1-sol"}[field["name"]]

import streamlit as st
from utils.user_manager import user_manager
from bot.bot_session_manager import BotSessionManager

def initialize_profile():
    users_path = PRIVATE / "users.json"
    users = json.loads(users_path.read_text()) if users_path.exists() else {}
    if USERNAME not in users:
        users[USERNAME] = hashlib.sha256(secrets.token_urlsafe(48).encode()).hexdigest()
        temporary = users_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(users))
        temporary.replace(users_path)
    local_configuration = PRIVATE / "user_config" / (USERNAME + ".encrypt")
    if not local_configuration.exists():
        manager = BotSessionManager(USERNAME)
        for name, model, avatar in [("GPT · 本机登录", "gpt-6.1-sol", "🤖"), ("Gemini · 本机登录", "gemini-3-flash", "💡")]:
            manager.add_bot({"name": name, "engine": "OpenAI", "model": model,
                             "base_url": "http://127.0.0.1:8317/v1", "api_key": "local-oauth",
                             "system_prompt": "用中文回答，注重证据与清晰表达。", "temperature": 0.5,
                             "enable": True, "avatar": avatar})
        manager.last_visited_page = "group_page"
        manager.save_data_to_file()


with (PRIVATE / ".bootstrap.lock").open("a+") as guard:
    fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
    initialize_profile()

if st.query_params.get("login") != "1":
    supplied_token = st.query_params.get("token")
    if not supplied_token or not user_manager.verify_token(supplied_token):
        st.session_state.logged_in = True
        st.session_state.username = USERNAME
        st.query_params["token"] = user_manager.generate_token(USERNAME)

runpy.run_path(str(VENDOR / "app.py"), run_name="__main__")
