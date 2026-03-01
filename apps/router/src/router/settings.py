from __future__ import annotations

from emf_shared.config import Settings as SharedSettings


class Settings(SharedSettings):
    signal_api_url: str = ""
    signal_sender: str = ""
    jambonz_api_url: str = ""
    jambonz_api_key: str = ""
    ack_base_url: str = ""

    model_config = {"env_file": ".env"}
