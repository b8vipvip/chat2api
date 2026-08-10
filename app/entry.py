from .main import app
from .voice_patch import install_voice_patch
from .v7_patch import install_v7_patch
from .v8_patch import install_v8_patch

install_voice_patch(app)
install_v7_patch(app)
install_v8_patch(app)
