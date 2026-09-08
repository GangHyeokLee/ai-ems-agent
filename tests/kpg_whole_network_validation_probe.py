from pathlib import Path
from pprint import pprint

from ai_ems.tools.control_tools import (
    validate_whole_network_redispatch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = PROJECT_ROOT / "data" / "KPG193_ver2_0_pypowsybl.mat"


result = validate_whole_network_redispatch(
    CASE_FILE,
    outage_line_id="LINE-16-28",
    up_generator_id="GEN-19#0",
    down_generator_id="GEN-36#0",
    delta_mw=10.0,
)

pprint(result)