"""Entry point for the Smoothie sidecar process."""

import argparse
import logging

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Smoothie Sidecar — AI backend for Blender")
    parser.add_argument("--port", type=int, default=8888, help="Port for the sidecar web server")
    parser.add_argument("--blender-port", type=int, required=True, help="Port for Blender's internal API")
    parser.add_argument("--api-key", type=str, default="", help="Anthropic API key")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-20250514", help="Claude model to use")
    args = parser.parse_args()

    # Configure logging — write to both stderr and a log file
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    import os as _os
    _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__))))
    _log_dir = _os.path.join(_project_root, "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    log_file = _os.path.join(_log_dir, "sidecar.log")
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="w"),
        ],
    )
    logger = logging.getLogger("smoothie.sidecar")
    logger.info("Starting sidecar on port %d, blender_port=%d (log: %s)", args.port, args.blender_port, log_file)

    from smoothie.sidecar import state
    state.init(blender_port=args.blender_port, api_key=args.api_key, model=args.model)

    uvicorn.run(
        "smoothie.sidecar.app:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        log_config=None,  # Prevent uvicorn from overriding our logging config
    )


if __name__ == "__main__":
    main()
