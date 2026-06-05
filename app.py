"""Flask entrypoint: IMU inference API and auth (see ld_backend package)."""

from ld_backend import create_app
from ld_backend.config import HOST, PORT

app = create_app()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, threaded=True)
