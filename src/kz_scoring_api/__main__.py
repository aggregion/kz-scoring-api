import os

import uvicorn


def main() -> None:
    host = os.getenv("KZ_SCORING_HOST", "0.0.0.0")
    port = int(os.getenv("KZ_SCORING_PORT", "8000"))
    uvicorn.run(
        "kz_scoring_api.app:app",
        host=host,
        port=port,
        log_level=os.getenv("KZ_SCORING_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
