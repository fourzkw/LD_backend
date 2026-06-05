"""LD_backend Flask application package."""

import logging

from flask import Flask

from ld_backend.config import LOG_FORMAT, get_log_level


def create_app() -> Flask:
    from ld_backend.blueprints.api import api_bp
    from ld_backend.blueprints.auth import auth_bp
    from ld_backend.blueprints.devices import devices_bp
    from ld_backend.blueprints.video import start_video_task_poller, video_bp
    from ld_backend.db.mongo import ping_mongo_at_startup
    from ld_backend.services.cloud_result_subscriber import start_cloud_result_subscriber

    logging.basicConfig(level=get_log_level(), format=LOG_FORMAT)
    app = Flask(__name__)
    app.register_blueprint(auth_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(video_bp)
    ping_mongo_at_startup()
    start_cloud_result_subscriber()
    start_video_task_poller()
    return app
