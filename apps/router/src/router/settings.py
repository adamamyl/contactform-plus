from __future__ import annotations

from emf_shared.config import Settings as SharedSettings


class Settings(SharedSettings):
    local_dev: bool = False
    signal_api_url: str = ""
    signal_sender: str = ""
    tts_service_url: str = "http://tts:8003"
    mattermost_token: str = ""
    mattermost_webhook_secret: str = ""
    router_internal_secret: str = ""
    ack_base_url: str = ""
    router_self_url: str = "http://msg-router:8002"
    resend_api_key: str = ""
    emf_phone_api_url: str = ""
    emf_phone_api_key: str = ""
    signal_use_webhook: bool = True  # if True, disable polling loop; rely on /webhook/signal

    model_config = {"env_file": ".env"}
