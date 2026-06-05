"""Flask entrypoint: IMU inference API and auth (see ld_backend package)."""

from ld_backend import create_app
from ld_backend.config import PORT

app = create_app()

if __name__ == "__main__":
    app.run(host="100.72.153.84", port=PORT, threaded=True)
