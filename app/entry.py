from .main import app
from .voice_patch import install_voice_patch
from .live_voice_patch import install_live_voice_patch
from .v7_patch import install_v7_patch
from .v8_patch import install_v8_patch
from .v9_patch import install_v9_patch
from .v10_patch import install_v10_patch
from .v11_patch import install_v11_patch
from .v12_patch import install_v12_patch
from .v13_patch import install_v13_patch
from .v14_patch import install_v14_patch
from .v15_patch import install_v15_patch
from .v16_patch import install_v16_patch
from .v17_patch import install_v17_patch
from .v17_crypto_patch import install_v17_crypto_patch
from .v17_route_migration_patch import install_v17_route_migration_patch
from .v17_finalize_patch import install_v17_finalize_patch
from .v17_1_patch import install_v17_1_patch
from .v18_patch import install_v18_patch
from .v19_patch import install_v19_patch
from .v20_patch import install_v20_patch
from .v20_1_patch import install_v20_1_patch
from .v20_2_patch import install_v20_2_patch
from .v20_3_patch import install_v20_3_patch
from .v21_patch import install_v21_patch
from .v21_routing_patch import install_v21_routing_patch
from .v21_1_patch import install_v21_1_patch
from .v21_2_patch import install_v21_2_patch
from .v21_3_patch import install_v21_3_patch
from .v21_4_model_contract_patch import install_v21_4_model_contract_patch
from .v21_5_patch import install_v21_5_patch
from .v21_6_patch import install_v21_6_patch
from .runtime_contract import install_runtime_contract
from .v21_11_patch import install_v21_11_patch

install_voice_patch(app)
install_live_voice_patch(app)
install_v7_patch(app)
install_v8_patch(app)
install_v9_patch(app)
install_v10_patch(app)
install_v11_patch(app)
install_v12_patch(app)
install_v13_patch(app)
install_v14_patch(app)
install_v15_patch(app)
install_v16_patch(app)
install_v17_patch(app)
install_v17_crypto_patch(app)
install_v17_route_migration_patch(app)
install_v17_finalize_patch(app)
install_v17_1_patch(app)
install_v18_patch(app)
install_v19_patch(app)
install_v20_patch(app)
install_v20_1_patch(app)
install_v20_2_patch(app)
install_v20_3_patch(app)
install_v21_patch(app)
install_v21_routing_patch(app)
install_v21_1_patch(app)
install_v21_2_patch(app)
install_v21_3_patch(app)
install_v21_4_model_contract_patch(app)
install_v21_5_patch(app)
install_v21_6_patch(app)

# Install the runtime contract after historical feature patches so /version
# remains the canonical production version owner. The v21.11 UI-only patch is
# installed last because it must run after the column-layout asset in /admin,
# but it deliberately does not mutate app.version.
install_runtime_contract(app)
install_v21_11_patch(app)
