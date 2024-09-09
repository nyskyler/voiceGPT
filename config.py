import os
from pathlib import Path

BASE_DIR = os.path.dirname(__file__)
UPLOAD_FOLDER = str(Path(BASE_DIR, "voiceGPT", "static", "uploads"))

SQLALCHEMY_DATABASE_URI = 'sqlite:///{}'.format(os.path.join(BASE_DIR, 'voiceGPT.db'))
SQLALCHEMY_TRACK_MODIFICATION = False
SECRET_KEY = "dev"