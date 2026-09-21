import copy

import pytest
import yaml

from scripts.validate_workflow import ROOT, validate_flow


def test_polling_uses_native_while_without_graph_cycles():
    flow = yaml.safe_load((ROOT / "workflows/drill.yaml.tftpl").read_text())
    validate_flow(flow)
    bad = copy.deepcopy(flow)
    bad["steps"]["route"]["switch"]["default"]["next"] = "poll"
    with pytest.raises(ValueError, match="Cycle"):
        validate_flow(bad)


def test_inner_steps_cannot_jump_outside_their_scope():
    flow = yaml.safe_load((ROOT / "workflows/drill.yaml.tftpl").read_text())
    flow["steps"]["poll"]["while"]["do"]["steps"]["tick"]["functionCall"]["next"] = "route"
    with pytest.raises(ValueError, match="Unknown step in this scope"):
        validate_flow(flow)
