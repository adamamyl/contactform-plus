from __future__ import annotations

from emf_shared.config import Settings as SharedSettings


class Settings(SharedSettings):
    local_dev: bool = False
    signal_api_url: str = ""
    signal_sender: str = ""
    jambonz_api_url: str = ""
    jambonz_api_key: str = ""
    jambonz_account_sid: str = ""
    jambonz_application_sid: str = ""
    jambonz_from_number: str = ""
    tts_service_url: str = "http://tts:8003"
    tts_audio_base_url: str = ""  # public base URL for Jambonz to fetch audio files; defaults to tts_service_url
    jambonz_webhook_base_url: str = ""  # public base URL for per-call call_hook override
    mattermost_token: str = ""
    mattermost_webhook_secret: str = ""
    router_internal_secret: str = ""
    ack_base_url: str = ""
    router_self_url: str = "http://msg-router:8002"

    model_config = {"env_file": ".env"}
