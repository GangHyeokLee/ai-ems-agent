from pprint import pprint

from ai_ems.config import CASE_FILE
from ai_ems.tools.control_tools import (
    validate_whole_network_redispatch,
)


result = validate_whole_network_redispatch(
    CASE_FILE,
    outage_line_id="LINE-16-28",
    up_generator_id="GEN-19#0",
    down_generator_id="GEN-36#0",
    delta_mw=10.0,
)

pprint(result)
